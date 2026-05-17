variable "aws_region" {
  description = "AWS region for every resource in this module."
  type        = string
  default     = "ap-southeast-2"
}

variable "enable_tls" {
  description = "Enable Caddy auto-TLS via Let's Encrypt. Requires a real public domain pointing at the EIP. Set false when deploying without DNS."
  type        = bool
  default     = true
}

variable "project" {
  description = "Tag/name prefix for every resource."
  type        = string
  default     = "aegis"
}

variable "environment" {
  description = "Deployment environment (prod / staging / dev). Drives naming + tags."
  type        = string
  default     = "prod"
}

# ---- Networking ----

variable "ssh_allowed_cidr" {
  description = "CIDR allowed to SSH to the EC2 instance. SET TO YOUR /32 — never 0.0.0.0/0."
  type        = string
}

# ---- Compute ----

variable "instance_type" {
  description = "EC2 instance type. t4g.medium = 2vCPU/4GB ARM, fits torch+spaCy."
  type        = string
  default     = "t4g.medium"
}

variable "ec2_key_pair_name" {
  description = "Name of an existing EC2 key pair for SSH access. Create one in the EC2 console first."
  type        = string
}

variable "root_volume_gb" {
  description = "EBS root volume size in GB."
  type        = number
  default     = 30
}

# ---- Database ----

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage_gb" {
  description = "RDS storage in GB."
  type        = number
  default     = 20
}

variable "db_backup_retention_days" {
  description = "RDS automated backup retention. Max 35."
  type        = number
  default     = 7
}

# ---- DNS ----

variable "domain_name" {
  description = "Apex domain (e.g. aegis.example.com). Hosted zone is created for it."
  type        = string
}

variable "create_dns_records" {
  description = "Create A records on the hosted zone for apex/www/hooks. Set false if managing DNS elsewhere."
  type        = bool
  default     = true
}

# ---- App secrets (loaded into Secrets Manager) ----

variable "openai_api_key" {
  description = "OPENAI_API_KEY"
  type        = string
  sensitive   = true
}

variable "openai_model" {
  description = "OPENAI_MODEL override."
  type        = string
  default     = "gpt-4o-mini"
}

variable "stripe_secret_key" {
  description = "STRIPE_SECRET_KEY (sk_live_...)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "stripe_webhook_secret" {
  description = "STRIPE_WEBHOOK_SECRET (whsec_...). Set after creating the webhook in Stripe."
  type        = string
  default     = ""
  sensitive   = true
}

variable "stripe_price_id" {
  description = "STRIPE_PRICE_ID."
  type        = string
  default     = ""
}

variable "razorpay_key_id" {
  type      = string
  default   = ""
  sensitive = true
}

variable "razorpay_key_secret" {
  type      = string
  default   = ""
  sensitive = true
}

variable "razorpay_webhook_secret" {
  type      = string
  default   = ""
  sensitive = true
}

variable "razorpay_plan_id_upi" {
  type    = string
  default = ""
}

variable "serpapi_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "sentry_dsn" {
  description = "Optional Sentry DSN; leave blank to disable."
  type        = string
  default     = ""
  sensitive   = true
}

variable "allowed_origins" {
  description = "CORS allow-list (comma separated). Defaults to apex + www."
  type        = string
  default     = ""
}

variable "extra_env" {
  description = "Map of extra env vars merged into Secrets Manager (useful for one-off flags)."
  type        = map(string)
  default     = {}
}

# ---- CI/CD ----

variable "github_actions_role_subjects" {
  description = "List of GitHub `repo:owner/name:ref:refs/heads/main` style subjects allowed to assume the CI role via OIDC. Leave empty to skip OIDC role creation."
  type        = list(string)
  default     = []
}

variable "github_oidc_thumbprint" {
  description = "GitHub Actions OIDC thumbprint. Update if GitHub rotates its cert."
  type        = string
  default     = "6938fd4d98bab03faadb97b34396831e3780aea1"
}
