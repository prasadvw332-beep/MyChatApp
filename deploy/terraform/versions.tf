# Terraform and provider versions. `~>` allows newer minor versions, not a new major one (which
# could rename inputs). .terraform.lock.hcl, written by `terraform init`, pins the exact versions:
# commit it, so CI and every laptop use the same providers.
terraform {
  required_version = ">= 1.10" # S3 state locking without DynamoDB (use_lockfile)

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.95"
    }
  }

  # State lives in S3, not on a laptop: shared, versioned, locked while someone applies.
  # The bucket is created once by hand (README.md, "First time"); its name goes in backend.hcl:
  #   terraform init -backend-config=backend.hcl
  backend "s3" {}
}
