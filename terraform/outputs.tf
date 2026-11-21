output "cluster_name" {
  description = "Name of the provisioned EKS cluster"
  value       = aws_eks_cluster.eks_cluster.name
}

output "cluster_endpoint" {
  description = "Endpoint URL for the EKS Kubernetes API server"
  value       = aws_eks_cluster.eks_cluster.endpoint
}

output "cluster_certificate_authority_data" {
  description = "Base64 encoded certificate data required to communicate with the cluster"
  value       = aws_eks_cluster.eks_cluster.certificate_authority[0].data
}

output "cluster_oidc_issuer_url" {
  description = "OIDC issuer URL of the EKS cluster"
  value       = aws_eks_cluster.eks_cluster.identity[0].oidc[0].issuer
}

output "s3_artifact_bucket_name" {
  description = "S3 bucket name created for MLflow and model artifact storage"
  value       = aws_s3_bucket.mlops_artifacts.id
}

output "s3_artifact_bucket_arn" {
  description = "ARN of the S3 artifact bucket"
  value       = aws_s3_bucket.mlops_artifacts.arn
}

output "irsa_role_arns" {
  description = "Map of all IAM Role ARNs provisioned for Kubernetes Service Accounts"
  value = {
    mlflow_s3_access        = aws_iam_role.mlflow_s3_access.arn
    model_serving_s3_access = aws_iam_role.model_serving_s3_access.arn
    drift_detector_s3_access = aws_iam_role.drift_detector_s3_access.arn
  }
}
