data "aws_ami" "al2023_arm64" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-arm64"]
  }
  filter {
    name   = "architecture"
    values = ["arm64"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    aws_region        = local.region
    secret_arn        = aws_secretsmanager_secret.app_env.arn
    image_uri         = local.image_uri
    domain_name       = var.domain_name
    backups_bucket    = aws_s3_bucket.backups.bucket
    db_address        = aws_db_instance.main.address
  })
}

resource "aws_instance" "app" {
  ami                    = data.aws_ami.al2023_arm64.id
  instance_type          = var.instance_type
  key_name               = var.ec2_key_pair_name
  vpc_security_group_ids = [aws_security_group.web.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2.name
  user_data              = local.user_data
  user_data_replace_on_change = false

  root_block_device {
    volume_size           = var.root_volume_gb
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  tags = {
    Name = "${local.name_prefix}-app"
  }

  lifecycle {
    ignore_changes = [ami, user_data]
  }
}

resource "aws_eip" "app" {
  instance = aws_instance.app.id
  domain   = "vpc"

  tags = {
    Name = "${local.name_prefix}-eip"
  }
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/${var.project}/${var.environment}/app"
  retention_in_days = 30
}
