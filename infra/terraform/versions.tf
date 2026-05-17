terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Uncomment + customize once you have an S3 bucket to hold the state file.
  # backend "s3" {
  #   bucket = "aegis-tfstate-<account-id>"
  #   key    = "aegis/prod.tfstate"
  #   region = "us-east-1"
  # }
}
