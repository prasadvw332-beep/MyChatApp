# The AWS side of the Kubernetes deployment: network, EKS cluster, and the role CI deploys with.
# What runs inside the cluster stays in deploy/k8s (applied by CI): Terraform builds the platform,
# Kubernetes manifests describe the app.
#
#   vpc.tf                   VPC, public/private subnets in 3 zones, NAT
#   eks.tf                   the cluster, its add-ons (incl. EBS disks and the metrics-server) and nodes
#   github-deploy-role.tf    the IAM role GitHub Actions assumes (OIDC, no stored AWS keys)

provider "aws" {
  region = var.region

  # on every resource that supports tags: who owns it, and that Terraform manages it (don't edit by hand)
  default_tags {
    tags = local.tags
  }
}

data "aws_availability_zones" "available" {
  state = "available"
  # only regular zones, not Local Zones or Wavelength
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

data "aws_caller_identity" "current" {}

locals {
  azs  = slice(data.aws_availability_zones.available.names, 0, 3)
  tags = {
    Project   = "mychatapp"
    ManagedBy = "terraform"
    Source    = "github.com/${var.github_repository}/deploy/terraform"
  }
}
