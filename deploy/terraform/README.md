# Terraform: the AWS platform

The cluster that CI deploys to (`.github/workflows/ci-cd.yml`, job `deploy`), as code: re-creatable,
reviewed in pull requests, and the same every time. Terraform builds the platform; what runs on it
stays in [deploy/k8s](../k8s), applied by CI.

```
VPC (3 zones)                                   vpc.tf
├── public subnets ─ load balancers, NAT gateway
└── private subnets ─ EKS managed node group      eks.tf
                      2-4 × m6i.xlarge, 50 GB gp3, AL2023
EKS cluster (control plane by AWS)               eks.tf
├── add-ons: VPC CNI, CoreDNS, kube-proxy,
│            EBS CSI driver (disks for the PVCs), metrics-server (for the HPAs)
└── access: the creator, and the GitHub deploy role (cluster admin)
IAM
├── GitHub OIDC provider                         github-deploy-role.tf
├── role github-deploy  ← only repo:<owner>/<repo>:environment:production
└── role ebs-csi        ← only kube-system:ebs-csi-controller-sa (IRSA)
```

Community modules ([vpc](https://registry.terraform.io/modules/terraform-aws-modules/vpc/aws),
[eks](https://registry.terraform.io/modules/terraform-aws-modules/eks/aws),
[iam](https://registry.terraform.io/modules/terraform-aws-modules/iam/aws)) do the heavy lifting; the files
here are the decisions. CI checks `terraform fmt` and `terraform validate` on every push, never applies.

## First time

You need the AWS CLI logged in as someone who may create VPCs, EKS clusters and IAM roles, and
Terraform 1.10 or newer.

```bash
cd deploy/terraform

# 1. A bucket for the state (once per account). Versioned, so a bad apply's state can be recovered.
aws s3api create-bucket --bucket mychatapp-terraform-state-<account-id> --region ap-south-1 \
  --create-bucket-configuration LocationConstraint=ap-south-1
aws s3api put-bucket-versioning --bucket mychatapp-terraform-state-<account-id> \
  --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket mychatapp-terraform-state-<account-id> \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

# 2. Settings
cp backend.hcl.example backend.hcl                 # the bucket name
cp terraform.tfvars.example terraform.tfvars       # only if a default must change

# 3. Create (about 15 minutes, mostly the cluster)
terraform init -backend-config=backend.hcl
terraform plan -out=tfplan                         # read it: what will be created
terraform apply tfplan
git add .terraform.lock.hcl                        # the exact provider versions, for everyone

# 4. Use it
$(terraform output -raw configure_kubectl)
kubectl get nodes
kubectl top nodes                                  # the metrics-server works: the HPAs can scale
```

Then turn on CI's deploy job: GitHub → Settings → Secrets and variables → Actions:

- secret `AWS_DEPLOY_ROLE_ARN` = `terraform output -raw github_deploy_role_arn`
- the app's secrets (`DATABASE_URL`, `JWT_SECRET`, ...; [deploy/README.md](../README.md#step-4-cicd-with-github-actions))
- variable `DEPLOY_ENABLED` = `true`

The next push to `main` deploys. The prod overlay adds the default `gp3` StorageClass, without which the
PostgreSQL, Qdrant, MinIO and Ollama volumes would stay `Pending`.

## Changing it

Edit the `.tf` files in a pull request, run `terraform plan` and paste what it says into the PR, apply after
review. Anything changed by hand in the AWS console is undone by the next apply (and shows up in `plan`
as drift).

- **More nodes**: `node_max_size` / `node_desired_size`. The HPAs only add pods; adding nodes automatically
  needs Cluster Autoscaler or Karpenter (not installed yet). Until then, pods that don't fit stay `Pending`
  (`kubectl get pods -A --field-selector=status.phase=Pending`).
- **Kubernetes upgrade**: `kubernetes_version`, one minor version at a time; the add-ons follow
  (`most_recent`). Versions past standard support cost extra per hour.

## Cost (ap-south-1, on-demand, roughly)

| | per month |
|---|---|
| EKS control plane | ~$73 |
| 2 × m6i.xlarge | ~$280 |
| NAT gateway (one) + traffic | ~$35+ |
| EBS volumes (nodes + PVCs, ~140 GB gp3) | ~$12 |
| Load balancer (Service `frontend`) | ~$20 |

`terraform destroy` removes all of it. Volumes use `reclaimPolicy: Retain` (so a deleted namespace keeps
the database), so delete leftover EBS volumes in the console afterwards, or they keep costing.

## Not here yet

- RDS instead of the in-cluster PostgreSQL, S3 instead of MinIO (see the table in [deploy/README.md](../README.md#step-3-imagined-the-same-app-on-aws))
- The AWS Load Balancer Controller + ACM certificate for https (the cluster serves http through a Classic
  Load Balancer until then; `COOKIE_SECURE=true` in the prod overlay needs https)
- Cluster Autoscaler or Karpenter, a GPU node group for Ollama
