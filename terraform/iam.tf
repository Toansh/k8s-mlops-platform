# -----------------------------------------------------------------------------
# S3 Artifact Bucket for MLflow and Model Assets
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "mlops_artifacts" {
  bucket        = "${var.s3_artifact_bucket_prefix}-${var.environment}-${var.aws_region}"
  force_destroy = false

  tags = {
    Name        = "${var.s3_artifact_bucket_prefix}-${var.environment}"
    Environment = var.environment
  }
}

resource "aws_s3_bucket_versioning" "mlops_artifacts" {
  bucket = aws_s3_bucket.mlops_artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "mlops_artifacts" {
  bucket = aws_s3_bucket.mlops_artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "mlops_artifacts" {
  bucket = aws_s3_bucket.mlops_artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# -----------------------------------------------------------------------------
# IRSA: 1. MLflow Tracking Server (Read / Write S3 Artifacts)
# -----------------------------------------------------------------------------

data "aws_iam_policy_document" "mlflow_assume_role" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    effect  = "Allow"

    condition {
      test     = "StringEquals"
      variable = "${replace(aws_iam_openid_connect_provider.eks.url, "https://", "")}:sub"
      values   = ["system:serviceaccount:${var.mlflow_namespace}:${var.mlflow_service_account_name}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${replace(aws_iam_openid_connect_provider.eks.url, "https://", "")}:aud"
      values   = ["sts.amazonaws.com"]
    }

    principals {
      identifiers = [aws_iam_openid_connect_provider.eks.arn]
      type        = "Federated"
    }
  }
}

resource "aws_iam_role" "mlflow_s3_access" {
  name               = "${var.cluster_name}-mlflow-s3-access"
  assume_role_policy = data.aws_iam_policy_document.mlflow_assume_role.json
}

resource "aws_iam_policy" "mlflow_s3_rw" {
  name        = "${var.cluster_name}-mlflow-s3-rw-policy"
  description = "Allows MLflow tracking server to read and write artifacts in S3"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ListMLflowBucket"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = aws_s3_bucket.mlops_artifacts.arn
      },
      {
        Sid    = "ReadWriteMLflowObjects"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:DeleteObject",
          "s3:AbortMultipartUpload",
          "s3:ListMultipartUploadParts"
        ]
        Resource = "${aws_s3_bucket.mlops_artifacts.arn}/*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "mlflow_s3_attach" {
  role       = aws_iam_role.mlflow_s3_access.name
  policy_arn = aws_iam_policy.mlflow_s3_rw.arn
}

# -----------------------------------------------------------------------------
# IRSA: 2. Model Serving (Read-Only Model Artifacts)
# -----------------------------------------------------------------------------

data "aws_iam_policy_document" "model_serving_assume_role" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    effect  = "Allow"

    condition {
      test     = "StringEquals"
      variable = "${replace(aws_iam_openid_connect_provider.eks.url, "https://", "")}:sub"
      values   = ["system:serviceaccount:${var.model_serving_namespace}:${var.model_serving_service_account_name}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${replace(aws_iam_openid_connect_provider.eks.url, "https://", "")}:aud"
      values   = ["sts.amazonaws.com"]
    }

    principals {
      identifiers = [aws_iam_openid_connect_provider.eks.arn]
      type        = "Federated"
    }
  }
}

resource "aws_iam_role" "model_serving_s3_access" {
  name               = "${var.cluster_name}-model-serving-s3-access"
  assume_role_policy = data.aws_iam_policy_document.model_serving_assume_role.json
}

resource "aws_iam_policy" "model_serving_s3_ro" {
  name        = "${var.cluster_name}-model-serving-s3-ro-policy"
  description = "Allows model serving pods read-only access to model artifacts"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ListBucket"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = aws_s3_bucket.mlops_artifacts.arn
      },
      {
        Sid    = "ReadOnlyModelObjects"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion"
        ]
        Resource = [
          "${aws_s3_bucket.mlops_artifacts.arn}/models/*",
          "${aws_s3_bucket.mlops_artifacts.arn}/mlflow/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "model_serving_s3_attach" {
  role       = aws_iam_role.model_serving_s3_access.name
  policy_arn = aws_iam_policy.model_serving_s3_ro.arn
}

# -----------------------------------------------------------------------------
# IRSA: 3. Drift Detector (Read-Only Baseline Distributions & Logs)
# -----------------------------------------------------------------------------

data "aws_iam_policy_document" "drift_detector_assume_role" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    effect  = "Allow"

    condition {
      test     = "StringEquals"
      variable = "${replace(aws_iam_openid_connect_provider.eks.url, "https://", "")}:sub"
      values   = ["system:serviceaccount:${var.drift_detector_namespace}:${var.drift_detector_service_account_name}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${replace(aws_iam_openid_connect_provider.eks.url, "https://", "")}:aud"
      values   = ["sts.amazonaws.com"]
    }

    principals {
      identifiers = [aws_iam_openid_connect_provider.eks.arn]
      type        = "Federated"
    }
  }
}

resource "aws_iam_role" "drift_detector_s3_access" {
  name               = "${var.cluster_name}-drift-detector-s3-access"
  assume_role_policy = data.aws_iam_policy_document.drift_detector_assume_role.json
}

resource "aws_iam_policy" "drift_detector_s3_ro" {
  name        = "${var.cluster_name}-drift-detector-s3-ro-policy"
  description = "Allows drift detector pods read-only access to baselines and inference logs"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ListBucket"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = aws_s3_bucket.mlops_artifacts.arn
      },
      {
        Sid    = "ReadOnlyDriftObjects"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion"
        ]
        Resource = [
          "${aws_s3_bucket.mlops_artifacts.arn}/baselines/*",
          "${aws_s3_bucket.mlops_artifacts.arn}/inference_logs/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "drift_detector_s3_attach" {
  role       = aws_iam_role.drift_detector_s3_access.name
  policy_arn = aws_iam_policy.drift_detector_s3_ro.arn
}
