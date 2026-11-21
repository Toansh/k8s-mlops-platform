variable "aws_region" {
  description = "AWS deployment region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment (e.g. prod, staging, dev)"
  type        = string
  default     = "prod"
}

variable "cluster_name" {
  description = "Name of the EKS cluster"
  type        = string
  default     = "eks-mlops-prod"
}

variable "kubernetes_version" {
  description = "Kubernetes control plane version"
  type        = string
  default     = "1.30"
}

variable "vpc_cidr" {
  description = "CIDR range for MLOps platform VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "cluster_endpoint_public_access_cidrs" {
  description = "List of CIDR blocks permitted to access the cluster public API endpoint"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "s3_artifact_bucket_prefix" {
  description = "S3 bucket name prefix for MLflow and model assets"
  type        = string
  default     = "k8s-mlops-artifacts"
}

variable "mlflow_namespace" {
  description = "Kubernetes namespace where MLflow tracking server is deployed"
  type        = string
  default     = "mlops-infra"
}

variable "mlflow_service_account_name" {
  description = "Kubernetes service account name for MLflow tracking server"
  type        = string
  default     = "mlflow"
}

variable "model_serving_namespace" {
  description = "Kubernetes namespace where FastAPI model serving pods are deployed"
  type        = string
  default     = "mlops-serving"
}

variable "model_serving_service_account_name" {
  description = "Kubernetes service account name for model serving pods"
  type        = string
  default     = "model-serving-sa"
}

variable "drift_detector_namespace" {
  description = "Kubernetes namespace where drift detection workloads run"
  type        = string
  default     = "mlops-serving"
}

variable "drift_detector_service_account_name" {
  description = "Kubernetes service account name for drift detector jobs"
  type        = string
  default     = "drift-detector-sa"
}
