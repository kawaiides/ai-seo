resource "aws_db_subnet_group" "main" {
  name       = "${local.name_prefix}-db-subnets"
  subnet_ids = data.aws_subnets.default.ids
}

resource "aws_db_parameter_group" "pg16" {
  name   = "${local.name_prefix}-pg16"
  family = "postgres16"

  parameter {
    name  = "log_min_duration_statement"
    value = "500" # log slow queries >500ms
  }
}

resource "aws_db_instance" "main" {
  identifier             = "${local.name_prefix}-pg"
  engine                 = "postgres"
  engine_version         = "16.4"
  instance_class         = var.db_instance_class
  allocated_storage      = var.db_allocated_storage_gb
  max_allocated_storage  = var.db_allocated_storage_gb * 5
  storage_type           = "gp3"
  storage_encrypted      = true
  username               = "aegis"
  password               = random_password.db.result
  db_name                = "aegis"
  parameter_group_name   = aws_db_parameter_group.pg16.name
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  multi_az               = false
  backup_retention_period = var.db_backup_retention_days
  backup_window           = "06:00-07:00"
  maintenance_window      = "sun:07:30-sun:08:30"
  copy_tags_to_snapshot   = true
  deletion_protection     = var.environment == "prod"
  skip_final_snapshot     = var.environment != "prod"
  final_snapshot_identifier = var.environment == "prod" ? "${local.name_prefix}-pg-final-${formatdate("YYYYMMDDhhmmss", timestamp())}" : null
  apply_immediately       = false
  performance_insights_enabled = false

  lifecycle {
    ignore_changes = [final_snapshot_identifier]
  }
}
