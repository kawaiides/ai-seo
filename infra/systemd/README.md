# systemd units for AEGIS Autopilot

Three one-shot services + timers that drive the passive outbound loop.
None of them assume Docker — they invoke the same uvicorn-side virtualenv
that `aegis.service` already uses.

## Install on a fresh box

```
sudo cp infra/systemd/*.service /etc/systemd/system/
sudo cp infra/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now \
    aegis-autopilot-daily.timer \
    aegis-autopilot-mail.timer \
    aegis-autopilot-reaudit.timer
systemctl list-timers --all | grep aegis
```

## Schedule

| Timer | Fires | Chain |
|---|---|---|
| `aegis-autopilot-daily.timer`   | 03:30 UTC daily          | `prospect --auto-seed` → `audit` → `contacts` → `report` |
| `aegis-autopilot-mail.timer`    | 14:00 UTC daily          | `mail` → `sequence`                                       |
| `aegis-autopilot-reaudit.timer` | 04:00 UTC every Monday   | `site_reaudit` (Slack/Linear alerts on score drops)        |

`Persistent=true` on every timer so a missed fire (reboot, maintenance)
fires on the next boot.

## Env file

All three services read `/opt/aegis/.env` (mode 0600, owned by `ubuntu`).
Operator must set at minimum:

```
SERPAPI_API_KEY=...        # prospector
HUNTER_API_KEY=...         # contact finder (or skip → mailto-only)
RESEND_API_KEY=...         # mail transport (or SMTP_HOST + SMTP_USER)
SMTP_FROM=outreach@aegis.example
DATABASE_URL=postgresql+asyncpg://aegis:...@localhost:5432/aegis
```

Optional (re-audit alerts):

```
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
LINEAR_API_KEY=lin_api_...
LINEAR_TEAM_ID=...
```

## Operations

```
# Watch a run live
journalctl -u aegis-autopilot-daily.service -f

# Trigger a fire now (skip the schedule)
sudo systemctl start aegis-autopilot-daily.service

# Pause without disabling
sudo systemctl stop aegis-autopilot-daily.timer
```
