output "ec2_public_ip" {
  value       = aws_eip.app.public_ip
  description = "Public IP for SSH and DNS records."
}

output "ec2_instance_id" {
  value       = aws_instance.app.id
  description = "Used by the GitHub Actions workflow to target SSM commands."
}

output "rds_endpoint" {
  value       = aws_db_instance.main.address
  description = "Postgres host."
}

output "ecr_repo_url" {
  value       = aws_ecr_repository.app.repository_url
  description = "Push images here."
}

output "backups_bucket" {
  value       = aws_s3_bucket.backups.bucket
  description = "S3 bucket holding pg_dump archives."
}

output "secret_arn" {
  value       = aws_secretsmanager_secret.app_env.arn
  description = "Secrets Manager secret with the app env vars."
}

output "route53_name_servers" {
  value       = try(aws_route53_zone.main[0].name_servers, [])
  description = "Set these at your domain registrar."
}

output "github_actions_role_arn" {
  value       = try(aws_iam_role.github_actions[0].arn, "")
  description = "Use this as the GitHub Actions OIDC `role-to-assume`."
}

output "admin_token" {
  value       = random_password.admin_token.result
  description = "Header value for /admin endpoints."
  sensitive   = true
}

output "db_password" {
  value       = random_password.db.result
  description = "RDS master password (also stored in Secrets Manager DATABASE_URL)."
  sensitive   = true
}
