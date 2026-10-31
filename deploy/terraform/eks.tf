# The EKS cluster: AWS runs the control plane; the managed node group runs the pods.
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.37"

  cluster_name    = var.cluster_name
  cluster_version = var.kubernetes_version

  # kubectl from GitHub's runners and from your laptop, over the internet; still needs AWS credentials
  cluster_endpoint_public_access       = true
  cluster_endpoint_public_access_cidrs = var.api_allowed_cidrs

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  # whoever runs `terraform apply` can use kubectl right away
  enable_cluster_creator_admin_permissions = true

  # Add-ons: cluster software that AWS installs and upgrades. most_recent = the newest version
  # that supports this Kubernetes version.
  cluster_addons = {
    vpc-cni = {
      most_recent    = true
      before_compute = true # the nodes need pod networking before they can become Ready
    }
    kube-proxy = { most_recent = true }
    coredns    = { most_recent = true }

    # disks for the PersistentVolumeClaims (PostgreSQL, Qdrant, MinIO, Ollama, Prometheus...);
    # the default StorageClass that uses it is deploy/k8s/overlays/prod/storageclass.yaml
    aws-ebs-csi-driver = {
      most_recent              = true
      service_account_role_arn = module.ebs_csi_irsa.iam_role_arn
    }

    # pod CPU and memory, which the HorizontalPodAutoscalers scale on (deploy/k8s/base/autoscaling.yaml)
    metrics-server = { most_recent = true }
  }

  eks_managed_node_groups = {
    general = {
      ami_type       = "AL2023_x86_64_STANDARD"
      instance_types = var.node_instance_types

      min_size     = var.node_min_size
      max_size     = var.node_max_size
      desired_size = var.node_desired_size

      # container images (the backend's is large) and Ollama's scratch space; data lives on PVCs
      block_device_mappings = {
        xvda = {
          device_name = "/dev/xvda"
          ebs         = {
            volume_size           = 50
            volume_type           = "gp3"
            encrypted             = true
            delete_on_termination = true
          }
        }
      }
    }
  }

  # Who may use the cluster besides its creator: the role CI deploys with (github-deploy-role.tf).
  # Cluster admin, because the manifests include cluster-wide objects (the namespace, the StorageClass).
  access_entries = {
    github_deploy = {
      principal_arn       = aws_iam_role.github_deploy.arn
      policy_associations = {
        admin = {
          policy_arn   = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = { type = "cluster" }
        }
      }
    }
  }
}

# The EBS CSI driver creates and attaches disks, so its pods need an AWS role: IRSA ties that role
# to the driver's Kubernetes service account, and to nothing else in the cluster.
module "ebs_csi_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.55"

  role_name             = "${var.cluster_name}-ebs-csi"
  attach_ebs_csi_policy = true

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:ebs-csi-controller-sa"]
    }
  }
}
