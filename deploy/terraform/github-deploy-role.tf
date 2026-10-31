# The role the deploy job in .github/workflows/ci-cd.yml assumes (secret AWS_DEPLOY_ROLE_ARN).
# GitHub signs a short-lived OIDC token for each job; AWS trusts that token instead of stored keys.
# The trust below accepts only this repository's jobs in the `production` environment, so a pull
# request (or any other workflow) can't deploy, and the environment's required reviewers still apply.

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]

  # AWS no longer checks these for GitHub (it trusts GitHub's certificate authority), but the API wants a value
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1", "1c58a3a8518e8759bf075b76b750d4f2df264fcd"]
}

locals {
  github_oidc_provider_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"

  # built from the name, not taken from module.eks: the cluster's access entries refer to this role
  cluster_arn = "arn:aws:eks:${var.region}:${data.aws_caller_identity.current.account_id}:cluster/${var.cluster_name}"
}

data "aws_iam_policy_document" "github_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # a job with `environment:` gets this subject; without one it would be ...:ref:refs/heads/main
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:environment:${var.github_environment}"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name                 = "${var.cluster_name}-github-deploy"
  description          = "Assumed by the deploy job of ${var.github_repository} (GitHub OIDC)"
  assume_role_policy   = data.aws_iam_policy_document.github_trust.json
  max_session_duration = 3600
}

# In AWS, the role may only look up the cluster (for `aws eks update-kubeconfig`). What it may do
# inside Kubernetes is the cluster's access entry (eks.tf), not IAM.
data "aws_iam_policy_document" "github_deploy" {
  statement {
    actions   = ["eks:DescribeCluster"]
    resources = [local.cluster_arn]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "describe-cluster"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy.json
}
