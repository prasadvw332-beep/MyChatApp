# Inputs. The defaults match what CI expects (.github/workflows/ci-cd.yml: cluster mychatapp-cluster
# in ap-south-1); override any of them in terraform.tfvars (see terraform.tfvars.example).

variable "region" {
  description = "AWS region for everything"
  type        = string
  default     = "ap-south-1"
}

variable "cluster_name" {
  description = "EKS cluster name; CI runs `aws eks update-kubeconfig --name <this>`"
  type        = string
  default     = "mychatapp-cluster"
}

variable "kubernetes_version" {
  description = "EKS Kubernetes version. Upgrade one minor version at a time; old versions cost extra (extended support)"
  type        = string
  default     = "1.34"
}

variable "vpc_cidr" {
  description = "Address range of the VPC; the subnets are carved out of it"
  type        = string
  default     = "10.0.0.0/16"
}

variable "single_nat_gateway" {
  description = "One NAT gateway for all zones (cheaper) instead of one per zone (survives a zone outage)"
  type        = bool
  default     = true
}

variable "api_allowed_cidrs" {
  description = "Who may reach the Kubernetes API. GitHub's runners have no fixed IPs, so CI needs 0.0.0.0/0 (access still needs AWS credentials)"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

# All pods requested together: ~2.3 CPU / 4.5 GiB at the HPAs' minimums, ~4.2 CPU / 7 GiB at their
# maximums (deploy/k8s/base/autoscaling.yaml), plus Ollama's real use on CPU (up to 6 GiB).
# Two m6i.xlarge (4 vCPU, 16 GiB each) hold all of it with room for system pods.
variable "node_instance_types" {
  description = "EC2 instance types of the node group"
  type        = list(string)
  default     = ["m6i.xlarge"]
}

variable "node_min_size" {
  type    = number
  default = 2
}

variable "node_desired_size" {
  description = "Nodes at creation. Later changes are ignored by the EKS module, so a node autoscaler can own the count"
  type        = number
  default     = 2
}

variable "node_max_size" {
  type    = number
  default = 4
}

variable "github_repository" {
  description = "owner/name of the GitHub repository whose deploy job may assume the deploy role"
  type        = string
  default     = "prasadvw332-beep/MyChatApp"
}

variable "github_environment" {
  description = "GitHub environment of the deploy job (`environment:` in ci-cd.yml); only that job gets the role"
  type        = string
  default     = "production"
}

variable "create_github_oidc_provider" {
  description = "An AWS account has at most one GitHub OIDC provider: false if it already exists"
  type        = bool
  default     = true
}
