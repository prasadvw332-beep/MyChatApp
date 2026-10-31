# Network: nodes in private subnets (no public IPs), load balancers in public ones.
#
#   10.0.0.0/16
#   ├── private  10.0.0.0/20, 10.0.16.0/20, 10.0.32.0/20   nodes and pods (the VPC CNI gives each pod a VPC IP,
#   │                                                       hence the large ranges)
#   └── public   10.0.48.0/24, 10.0.49.0/24, 10.0.50.0/24  load balancers, NAT gateway
#
# Nodes reach the internet (images from ghcr.io, models for Ollama) through the NAT gateway.
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.21"

  name = var.cluster_name
  cidr = var.vpc_cidr
  azs  = local.azs

  private_subnets = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 4, i)]
  public_subnets  = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 8, 48 + i)]

  enable_nat_gateway   = true
  single_nat_gateway   = var.single_nat_gateway
  enable_dns_hostnames = true

  # how Kubernetes finds the subnets for a Service of type LoadBalancer (public) or an internal one
  public_subnet_tags = {
    "kubernetes.io/role/elb" = 1
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = 1
  }
}
