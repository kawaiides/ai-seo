locals {
  url_scheme  = var.enable_tls ? "https" : "http"
  public_host = var.enable_tls ? var.domain_name : aws_eip.app.public_ip
  base_url    = "${local.url_scheme}://${local.public_host}"

  base_env = {
    DATABASE_URL          = "postgresql+asyncpg://aegis:${random_password.db.result}@${aws_db_instance.main.address}:5432/aegis"
    OPENAI_API_KEY        = var.openai_api_key
    OPENAI_MODEL          = var.openai_model
    REPORT_SIGNING_KEY    = random_password.report_signing_key.result
    SESSION_SIGNING_KEY   = random_password.session_signing_key.result
    AEGIS_SECRET_KEY      = random_password.aegis_secret_key.result
    AEGIS_ENV             = var.environment
    ADMIN_TOKEN           = random_password.admin_token.result
    APP_BASE_URL          = local.base_url
    REPORT_BASE_URL       = local.base_url
    AEGIS_SITE_URL        = local.base_url
    ALLOWED_ORIGINS       = local.allowed_origins
    STRIPE_SECRET_KEY     = var.stripe_secret_key
    STRIPE_WEBHOOK_SECRET = var.stripe_webhook_secret
    STRIPE_PRICE_ID       = var.stripe_price_id
    RAZORPAY_KEY_ID         = var.razorpay_key_id
    RAZORPAY_KEY_SECRET     = var.razorpay_key_secret
    RAZORPAY_WEBHOOK_SECRET = var.razorpay_webhook_secret
    RAZORPAY_PLAN_ID_UPI    = var.razorpay_plan_id_upi
    SERPAPI_KEY           = var.serpapi_key
    SENTRY_DSN            = var.sentry_dsn
    AEGIS_RUN_MIGRATIONS  = "1"
    AEGIS_WAIT_FOR_DB     = "1"
  }

  app_env = merge(local.base_env, var.extra_env)
}

resource "aws_secretsmanager_secret" "app_env" {
  name                    = "${local.name_prefix}/app-env"
  description             = "Environment variables for the AEGIS app container."
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "app_env" {
  secret_id     = aws_secretsmanager_secret.app_env.id
  secret_string = jsonencode(local.app_env)
}
