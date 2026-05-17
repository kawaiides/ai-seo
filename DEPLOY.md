# AEGIS — Deployment Guide

This guide covers three deployment paths. Pick one:

| Path | Best for | Cost (rough) |
|---|---|---|
| **Docker Compose on a single VPS** | Solo / staging / demo | $5–20/mo (Hetzner, DO, Linode) |
| **Fly.io** | Managed, autoscale, multi-region | $5–15/mo + Postgres |
| **AWS (EC2 + RDS + ECR + GHA)** | Native AWS, IaC reproducible, CI/CD wired | ~$44/mo on-demand, ~$18 reserved — see [§3](#3-aws-ec2--rds--ecr--github-actions--terraform) |
| **Render / Railway** | Push-to-deploy with managed Postgres | $7+/mo per service |

All three use the same `Dockerfile`. Build artifacts and behaviour are identical — only the orchestration differs.

---

## 0. Pre-flight checklist

Before any deploy:

1. **Secrets generated.** Two 32-byte random strings for `REPORT_SIGNING_KEY` and `SESSION_SIGNING_KEY`:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. **OpenAI key.** `OPENAI_API_KEY` set (Feature 2, rewrite, GEO probes all need it).
3. **CORS tightened.** `app/main.py` currently sets `allow_origins=["*"]`. Replace with your real origin list before production.
4. **Rate limiter is in-memory.** `app/main.py` counts requests per IP in a process-local dict. Fine for a single worker; if you scale to >1 worker or >1 instance, swap to Redis (see `RATE_LIMITED_PATHS`).
5. **Migrations apply automatically.** The container entrypoint runs `alembic upgrade head` on boot. Set `AEGIS_RUN_MIGRATIONS=0` to disable (e.g. when running multiple replicas and you want migrations gated to a single job).

The full env-var checklist is at the bottom of this file.

---

## 1. Docker Compose on a single VPS

Cheapest, no vendor lock-in. Use any Ubuntu 22.04+ box with ≥2GB RAM.

```bash
# on your VPS
git clone <repo> aegis && cd aegis
cp .env.example .env
# edit .env: OPENAI_API_KEY, signing keys, SMTP creds, payment keys, ADMIN_TOKEN

docker compose --profile prod up -d --build
docker compose logs -f app
```

Service map:
- `postgres` — Postgres 16 with a named volume (`aegis_pgdata`)
- `mailhog` — dev SMTP catch-all (port 8025 web UI). Replace with real SMTP creds in `.env` for prod.
- `app` — FastAPI container, runs migrations on boot, listens on `:8000`

Put a TLS-terminating reverse proxy in front (Caddy is the lowest-friction option):

```caddy
# /etc/caddy/Caddyfile
aegis.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}
```

`sudo systemctl reload caddy` and you have HTTPS via Let's Encrypt.

**Updates:**
```bash
git pull
docker compose --profile prod build app
docker compose --profile prod up -d app
```

**Backups:** dump the volume daily:
```bash
docker exec aegis-postgres pg_dump -U aegis aegis | gzip > /var/backups/aegis-$(date +%F).sql.gz
```

---

## 2. Fly.io

The included `fly.toml` is wired for a single shared-CPU VM with 1GB RAM and a Fly Postgres attachment.

```bash
brew install flyctl
flyctl auth login

# Edit fly.toml: set `app = "<your-unique-name>"` and `primary_region`.
flyctl apps create <your-unique-name>

# Provision Postgres in the same region:
flyctl postgres create --name aegis-pg --region iad
flyctl postgres attach aegis-pg          # sets DATABASE_URL automatically

# Set the rest of the secrets:
flyctl secrets set \
    OPENAI_API_KEY="sk-..." \
    REPORT_SIGNING_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
    SESSION_SIGNING_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
    SMTP_HOST=... SMTP_PORT=587 SMTP_USER=... SMTP_PASS=... SMTP_FROM="audits@yourdomain.com" \
    STRIPE_SECRET_KEY=... STRIPE_WEBHOOK_SECRET=... \
    ADMIN_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"

flyctl deploy
flyctl logs
flyctl open
```

Notes:
- `fly.toml` enables `auto_stop_machines` so the VM hibernates when idle (saves money for a demo deployment). Set `min_machines_running = 1` if you can't tolerate cold-start latency.
- The Fly Postgres URL uses `postgres://`; the app expects `postgresql+asyncpg://`. The `postgres attach` step writes the right scheme into `DATABASE_URL`. If you ever rotate by hand, prefix it with `postgresql+asyncpg://`.

---

## 3. AWS (EC2 + RDS + ECR + GitHub Actions) — Terraform

End-to-end recipe driven by [infra/terraform/](infra/terraform/). Provisions VPC SGs, RDS Postgres 16, ECR repo, EC2 t4g.medium (ARM, Graviton), Elastic IP, Route 53 zone + records, S3 backups bucket, Secrets Manager, CloudWatch log group, optional GitHub Actions OIDC role. Cron jobs ship as systemd timers in the EC2 user-data.

### 3.0 Prereqs

```bash
# Local
brew install awscli terraform docker jq
docker buildx version    # buildx needed for ARM cross-build
aws configure            # admin keys
```

AWS console one-offs:
1. Create EC2 key pair `aegis-prod` (EC2 → Key Pairs → Create). Download the `.pem`.
2. Set a billing alarm at $50 (Billing → Budgets).

Get your /32:
```bash
echo "$(curl -s ifconfig.me)/32"
```

### 3.1 Populate `terraform.tfvars`

```bash
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars
$EDITOR infra/terraform/terraform.tfvars
```

Fill in at minimum: `ssh_allowed_cidr`, `ec2_key_pair_name`, `domain_name`, `openai_api_key`. Leave Stripe blank for now — Stripe webhook secret needs the public domain to exist first, so it's a second-pass fill.

### 3.2 Provision

```bash
./scripts/preflight.sh                # tool + creds sanity
cd infra/terraform
terraform init
terraform plan -out=tf.plan
terraform apply tf.plan
```

5-8 min. Outputs include `ec2_public_ip`, `rds_endpoint`, `ecr_repo_url`, `route53_name_servers`, `github_actions_role_arn`, `admin_token` (sensitive).

Copy the 4 name servers into your domain registrar. Wait for DNS propagation (`dig +short yourdomain.com` returns the EIP).

### 3.3 First image push

The EC2 instance is up but pulling `aegis-app:latest` fails until you push something. Build + push the first image manually:

```bash
ACCT=$(aws sts get-caller-identity --query Account --output text)
REGION=us-east-1
aws ecr get-login-password --region $REGION | \
    docker login --username AWS \
    --password-stdin ${ACCT}.dkr.ecr.${REGION}.amazonaws.com

docker buildx build --platform linux/arm64 \
    -t ${ACCT}.dkr.ecr.${REGION}.amazonaws.com/aegis-app:latest \
    --push .
```

The EC2 user-data will pull on the next `docker compose up -d` (triggered next time the SSM redeploy fires, or you can SSH and run `cd /opt/aegis && docker compose up -d` once).

### 3.4 GitHub Actions auto-deploy

The Terraform module creates an OIDC role (`github_actions_role_arn` output) if you set `github_actions_role_subjects` in `terraform.tfvars`. Wire three GitHub Actions secrets in the repo settings:

| Secret | Value |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | `terraform output -raw github_actions_role_arn` |
| `AWS_INSTANCE_ID` | `terraform output -raw ec2_instance_id` |
| `AWS_SECRET_ARN` | `terraform output -raw secret_arn` |
| `PUBLIC_HEALTH_URL` (optional) | `https://yourdomain.com/api/health` for post-deploy smoke check |

Push to `main` → [.github/workflows/deploy-aws.yml](.github/workflows/deploy-aws.yml) builds ARM image, pushes to ECR, triggers SSM `RunShellScript` on the EC2 box, polls until the new container is healthy. No SSH keys in CI.

Manual fallback when CI is offline: [scripts/deploy.sh](scripts/deploy.sh).

### 3.5 SES (outbound email)

Terraform doesn't provision SES (the DKIM CNAMEs differ per account and need a Route 53 record set the module doesn't manage). Do this in console:

1. SES → Verified identities → Create → Domain `yourdomain.com` → Easy DKIM.
2. Add the 3 DKIM CNAME records to your Route 53 hosted zone.
3. SES → Account dashboard → Request production access (24h human approval).
4. SES → SMTP settings → Create SMTP credentials → save `SMTP_USER` + `SMTP_PASS`.
5. Update Secrets Manager:
   ```bash
   aws secretsmanager update-secret --secret-id aegis-prod/app-env --secret-string "$(aws secretsmanager get-secret-value --secret-id aegis-prod/app-env --query SecretString --output text | jq --arg u "$SMTP_USER" --arg p "$SMTP_PASS" '. + {SMTP_HOST:"email-smtp.us-east-1.amazonaws.com", SMTP_PORT:"587", SMTP_USER:$u, SMTP_PASS:$p, SMTP_FROM:"audits@yourdomain.com"}')"
   ```
6. Trigger redeploy: `./scripts/deploy.sh` or push a no-op commit.

### 3.6 Stripe live keys

1. Stripe dashboard (Live mode) → Developers → API keys → copy `sk_live_...`.
2. Products → create → copy `price_...`.
3. Webhooks → Add endpoint → `https://hooks.yourdomain.com/api/payment_webhooks/stripe`.
   Events: `checkout.session.completed`, `customer.subscription.{created,updated,deleted}`, `invoice.payment_{succeeded,failed}`.
4. Copy `whsec_...`.
5. Edit `infra/terraform/terraform.tfvars` with the three values → `terraform apply` (only secrets change, no infra churn).
6. Redeploy.

### 3.7 Smoke test

```bash
curl -fsS https://yourdomain.com/api/health
curl -fsS -X POST https://yourdomain.com/api/aeo/analyze \
    -H 'Content-Type: application/json' \
    -d '{"input_type":"text","input_value":"<h1>Python</h1><p>Python is high-level.</p>"}' | jq '.aeo_score'
```

### 3.8 Operations

| Task | Command |
|---|---|
| SSH | `aws ssm start-session --target $(terraform output -raw ec2_instance_id)` (no key pair needed via SSM) |
| Tail app logs | CloudWatch Logs group `/aegis/prod/app` |
| Bounce app | `aws ssm send-command --instance-ids ... --document-name AWS-RunShellScript --parameters 'commands=["cd /opt/aegis && docker compose restart app"]'` |
| Rotate secrets | Edit `terraform.tfvars` → `terraform apply` → `./scripts/deploy.sh` |
| Restore DB | `./scripts/restore_db.sh [YYYY-MM-DD]` |
| Run migration manually | `aws ssm send-command --instance-ids ... --parameters 'commands=["cd /opt/aegis && docker compose exec -T app alembic upgrade head"]'` |
| List timers | SSM into box → `systemctl list-timers aegis-*` |
| Tear down | `terraform destroy` (RDS + S3 bucket protected by `prevent_destroy` — see lifecycle blocks if you really mean it) |

### 3.9 Costs (us-east-1, on-demand)

| Item | $/mo |
|---|---|
| EC2 t4g.medium | 24.40 |
| EBS 30GB gp3 | 2.40 |
| Elastic IP (attached) | 0 |
| RDS db.t4g.micro | 12.41 |
| RDS 20GB gp3 | 2.30 |
| Route 53 zone | 0.50 |
| Secrets Manager 1 secret | 0.40 |
| CloudWatch Logs ~1GB | 0.50 |
| S3 (10GB Glacier-IR) | 0.20 |
| ECR storage ~3GB | 0.30 |
| SES (~3k emails) | 0.30 |
| **Total** | **~$44** |

3-yr Savings Plan on EC2 + 1-yr RDS reserved instance drops the bill to ~$18. Worth setting up after you trust the workload pattern.

### 3.10 Files dropped by this path

```
infra/terraform/
├── versions.tf
├── variables.tf
├── main.tf
├── network.tf
├── rds.tf
├── ecr.tf
├── iam.tf
├── secrets.tf
├── ec2.tf
├── dns.tf
├── s3.tf
├── outputs.tf
├── terraform.tfvars.example
└── templates/
    └── user_data.sh.tftpl
.github/workflows/deploy-aws.yml
scripts/
├── preflight.sh
├── deploy.sh
└── restore_db.sh
```

---

## 4. Render / Railway (managed PaaS alt)

Both providers can build directly from the `Dockerfile`. Wire two services:

1. **Postgres** (managed) — copy the connection string, prefix with `postgresql+asyncpg://`, save as `DATABASE_URL`.
2. **Web service** — `Dockerfile` build, port `8000`, health-check `/api/health`. Copy the env-var list from `.env.example` into the dashboard.

Render-specific:
- Use the **Background Worker** type if you want the autopilot scheduler (`python -m app.autopilot.blog_scheduler`) running separately from the web dyno.
- Set `AEGIS_RUN_MIGRATIONS=1` on the web service and `0` on the worker so only one process runs Alembic at a time.

---

## 4. Autopilot / background jobs

The autopilot CLI lives at `python -m app.autopilot.<subcommand>` (see `app/autopilot/`). Three ways to run it in production:

| Mode | When to use | How |
|---|---|---|
| In-process scheduler | Single VPS, low volume | `AEGIS_BLOG_AUTOGEN=1` env var → the FastAPI startup hook spawns the scheduler thread (see `app/main.py::_maybe_start_blog_scheduler`). |
| External cron | Multi-worker / multi-instance | Host cron: `0 * * * * docker exec aegis-app python -m app.autopilot.blog_scheduler --once` |
| Separate worker service | Fly/Render/Railway | Second app/service running the same image with `CMD ["python", "-m", "app.autopilot.site_runner"]` — keep `AEGIS_RUN_MIGRATIONS=0` on this one. |

---

## 5. Env-var checklist

Required:

| Var | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection (`postgresql+asyncpg://…`). The entrypoint waits up to 60s for the host:port to become reachable. |
| `OPENAI_API_KEY` | LLM calls (fanout, rewrite, GEO probes). |
| `REPORT_SIGNING_KEY` | Itsdangerous signer for shareable report URLs. 32+ bytes random. |
| `SESSION_SIGNING_KEY` | Auth session cookies. 32+ bytes random. |

Strongly recommended:

| Var | Purpose |
|---|---|
| `OPENAI_MODEL` | Defaults to `gpt-4o-mini`. Switch to `gpt-5` or similar when promoted. |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `SMTP_FROM` | Outbound email (report delivery, billing receipts). |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` / `STRIPE_PRICE_ID` | Billing (skip if you're not exposing checkout yet). |
| `RAZORPAY_*` | India billing path. |
| `ADMIN_TOKEN` | Header-gated `/admin/*` access. |
| `REPORT_BASE_URL` / `APP_BASE_URL` | Public URL the app should generate links against. Set to your custom domain. |
| `AEGIS_SITE_URL` | Public origin written into `robots.txt` / `sitemap.xml`. |
| `SERPAPI_KEY` | Prospector / contact-finder fan-out. |

Operational toggles:

| Var | Default | Purpose |
|---|---|---|
| `AEGIS_RUN_MIGRATIONS` | `1` | Set to `0` on replicas so only one container runs `alembic upgrade head`. |
| `AEGIS_WAIT_FOR_DB` | `1` | Container blocks on `DATABASE_URL` reachability for up to 60s before booting. |
| `AEGIS_BLOG_AUTOGEN` | unset | `1` enables the in-process blog scheduler (single-worker deployments only). |
| `AEGIS_DISABLE_RATE_LIMIT` | unset | Tests only — don't set in production. |
| `AEGIS_FANOUT_CACHE` | unset | `1` enables in-memory fanout cache for dev. |

---

## 6. Smoke test post-deploy

```bash
BASE=https://your-domain.example.com

# health
curl -fsS "$BASE/api/health"
# → {"status":"ok"}

# Feature 1 — should return a populated aeo_score
curl -fsS -X POST "$BASE/api/aeo/analyze" \
    -H 'Content-Type: application/json' \
    -d '{"input_type":"text","input_value":"<h1>Python</h1><p>Python is a high-level language.</p>"}' | head -c 400

# Feature 2 — needs OPENAI_API_KEY set
curl -fsS -X POST "$BASE/api/fanout/generate" \
    -H 'Content-Type: application/json' \
    -d '{"target_query":"best AI writing tool"}' | head -c 400
```

If `/api/fanout/generate` returns `503 llm_unavailable`, your OpenAI key isn't reaching the container. Check:
```bash
docker exec aegis-app env | grep OPENAI    # compose
flyctl ssh console -C "env | grep OPENAI"  # fly
```

---

## 7. Known gotchas

1. **Image size: ~2.5GB.** Torch CPU + spaCy `en_core_web_lg` + MiniLM. Pre-bake them in the build stage (already done) so the running container starts fast. If you have to fit in a 1GB image budget, switch to `en_core_web_sm` (Check D/A quality drops) and lazy-load MiniLM (first fanout request pays ~10s download).
2. **Multi-worker rate limit.** `app/main.py` keeps the counter in a process dict. Two workers = double the effective limit per IP. Move to Redis (`redis-py` already a sibling of other deps) before scaling out.
3. **Migrations + replicas.** Don't run `alembic upgrade head` from every replica on boot. Pick one to be the migrator (`AEGIS_RUN_MIGRATIONS=1`, others `0`) or move the migration step into a pre-deploy job.
4. **Stripe / Razorpay webhooks.** The signing-secret check is HMAC-based; if you put a reverse proxy in front, make sure it forwards the raw body (Caddy does, nginx by default does too — but watch for any body-rewrite middleware).
5. **CORS.** The current `allow_origins=["*"]` is fine for the public API but won't pass a security review with cookie-auth enabled. Replace before exposing `/account` / `/admin` over the public internet.
