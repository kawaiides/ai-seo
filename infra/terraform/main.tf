provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

locals {
  name_prefix     = "${var.project}-${var.environment}"
  ecr_repo        = "${var.project}-app"
  account_id      = data.aws_caller_identity.current.account_id
  region          = data.aws_region.current.name
  default_origins = var.enable_tls ? "https://${var.domain_name},https://www.${var.domain_name}" : "*"
  allowed_origins = var.allowed_origins != "" ? var.allowed_origins : local.default_origins
  image_uri       = "${local.account_id}.dkr.ecr.${local.region}.amazonaws.com/${local.ecr_repo}:latest"
}

resource "random_password" "db" {
  length      = 32
  special     = true
  min_special = 2
  override_special = "!#$%*+-=?"
}

resource "random_password" "report_signing_key" {
  length  = 48
  special = false
}

resource "random_password" "session_signing_key" {
  length  = 48
  special = false
}

resource "random_password" "aegis_secret_key" {
  length  = 48
  special = false
}

resource "random_password" "admin_token" {
  length  = 32
  special = false
}
