# ADR-002: Kubernetes MLOps Platform Architecture & GitOps Operating Model

- **Status:** Accepted
- **Author:** Ansh Taneja, Senior Tech Lead & Systems Architect
- **Date:** 2024-Q4
- **Target Platform:** Cloud-Native Kubernetes (Amazon EKS / Multi-AZ), ArgoCD, MLflow, Prometheus/Grafana, FastAPI

---

## 1. Context & Problem Statement

Production machine learning systems fail predominantly not due to algorithmic deficiencies, but because of architectural friction across four vectors:
1. **Infrastructure & Configuration Drift:** Imperative deployments create snowflake environments across experimentation and inference.
2. **Operational Opacity & Scalability:** Inability to dynamically scale based on inference latency (P99) and request concurrency, resulting in over-provisioning or service degradation under burst traffic.
3. **Statistical Data & Concept Drift:** Unmonitored divergence between training baselines and real-time inference distributions leading to silent model failure.
4. **Security Vulnerabilities:** Permissive lateral pod networking and over-privileged cloud credentials exposing model intellectual property and private training artifacts.

This document formalizes the architecture, operating model, mathematical verification frameworks, and zero-trust controls for `k8s-mlops-platform`.

---

## 2. Platform Architecture

The platform is designed around strict separation of concerns, orchestrated entirely via GitOps principles and isolated across functional Kubernetes namespaces:
- `argocd`: GitOps reconciliation engine.
- `mlops-infra`: Centralized state, MLflow Tracking Server, and Model Registry metadata.
- `mlops-serving`: High-performance inference endpoints, autoscalers, and statistical drift monitors.
- `mlops-monitoring`: Observability plane (Prometheus, Grafana, Alertmanager).

```
                     +-------------------------------------------------+
                     |                 Git Repository                  |
                     |  (https://github.com/Toansh/k8s-mlops-platform) |
                     +-------------------------------------------------+
                                              |
                                              | Sync / Reconcile
                                              v
                              +-------------------------------+
                              |    ArgoCD Root Application    |
                              |       (App-of-Apps)           |
                              +-------------------------------+
                                 /            |            \
                   Sync Wave 0  /  Sync Wave 1|  Sync Wave 2\
                               v              v              v
               +------------------+ +------------------+ +--------------------+
               |  Monitoring App  | |    MLflow App    | | Model Serving App  |
               | (mlops-monitoring| |  (mlops-infra)   | |  (mlops-serving)   |
               +------------------+ +------------------+ +--------------------+
                        |                     ^                     |
                  Scrapes Metrics             | Pulls Artifacts     |
                        |                     |                     v
                        |           +-------------------+   +------------------+
                        +---------->| S3 Artifact Store |<--| Inference Pods   |
                                    +-------------------+   +------------------+
                                                                    ^
                                                                    | Ingress Port 8080
                                                            +------------------+
                                                            | NGINX Ingress    |
                                                            +------------------+
```

---

## 3. GitOps App-of-Apps Operating Model

We adopt the **ArgoCD App-of-Apps** pattern as the single entrypoint (`gitops/root-app.yaml`).

### Mechanics:
1. **Root Application Declaration:** The root app inspects `gitops/apps/` and deploys child `Application` custom resources.
2. **Sync Waves & Dependency Ordering:**
   - **Wave 0 (`monitoring-app`):** Installs Prometheus Operator CRDs and base monitoring infrastructure to ensure collectors exist before workloads spin up.
   - **Wave 1 (`mlflow-app`):** Deploys backend database, S3 connectors, and MLflow Tracking server.
   - **Wave 2 (`model-serving-app`):** Deploys model runtime workloads, ServiceMonitors, and HPA configurations.
3. **Automated Drift Healing:**
   - `prune: true`: Guarantees declarative parity; resources removed from Git are pruned from the cluster.
   - `selfHeal: true`: Active cluster alterations outside Git are overridden within the reconciliation interval (3m max).
   - `applyOutOfSyncOnly: true`: Minimizes API server load during high-frequency sync cycles.

---

## 4. Model Lifecycle & HPA Scaling Rationale

Inference workloads possess non-linear resource characteristics. Traditional CPU/Memory horizontal autoscaling is insufficient because inference saturation typically correlates with HTTP connection concurrency or worker queue contention before CPU limits are breached.

### Scaling Architecture:
1. **Dual Metric HPA (Kubernetes Metrics API + Prometheus Adapter):**
   - **Metric 1: Request Rate (RPS):** Target: 500 requests/second per replica.
   - **Metric 2: Resource Target:** CPU utilization target: 70%.
2. **Cold Start & Provisioning Mitigation:**
   - Model weights are pre-cached and loaded into resident memory at startup.
   - Kubernetes `readinessProbe` verifies model serialization in memory before registering the pod to the Service endpoints slice.
   - Graceful termination lifecycle hooks (`preStop: sleep 10`) allow in-flight requests to finish prior to SIGKILL.

---

## 5. Statistical Drift Detection: KS-Test vs. PSI

Real-time model degradation occurs through **Covariate Shift** $P(X) \neq P_{ref}(X)$ and **Concept Drift** $P(Y|X) \neq P_{ref}(Y|X)$. The platform implements automated statistical validation running via a scheduled CronJob.

### 5.1 Kolmogorov-Smirnov Test (Two-Sample KS-Test)
For continuous features (e.g., embeddings, numerical inputs), the non-parametric Two-Sample KS-Test evaluates the null hypothesis $H_0: F_1(x) = F_2(x)$:

$$D = \sup_{x} |F_1(x) - F_2(x)|$$

Where:
- $F_1(x)$ is the Empirical Cumulative Distribution Function (ECDF) of baseline training data.
- $F_2(x)$ is the ECDF of production inference data over the observation window.
- Reject $H_0$ at significance level $\alpha = 0.05$ when $p\text{-value} < 0.05$.

### 5.2 Population Stability Index (PSI)
For binned continuous and discrete categorical features, the Population Stability Index quantifies divergence:

$$\text{PSI} = \sum_{k=1}^{K} \left( P_k - Q_k \right) \times \ln\left(\frac{P_k}{Q_k}\right)$$

Where:
- $P_k$ is the observed inference proportion in bin $k$.
- $Q_k$ is the expected training baseline proportion in bin $k$.
- $K$ is the total number of quantile bins (default: $K = 10$).

### 5.3 Operational Thresholds & Automated Triggering:
| Metric | Threshold | Action |
|---|---|---|
| **PSI** | $\text{PSI} < 0.10$ | Stable: No drift detected. Normal monitoring. |
| **PSI** | $0.10 \le \text{PSI} < 0.20$ | Moderate Shift: Emit warning alert to Prometheus Alertmanager. |
| **PSI** | $\text{PSI} \ge 0.20$ | Significant Shift: Emit critical alert, invoke automated retraining pipeline Job. |
| **KS-Test** | $p\text{-value} < 0.05$ | Reject $H_0$: Feature distribution drifted. Flag feature for retraining. |

---

## 6. Zero-Trust Network Security & RBAC

The platform enforces a micro-segmented security perimeter using Kubernetes CNI-enforced `NetworkPolicy` objects and AWS IAM Roles for Service Accounts (IRSA):

1. **Default-Deny-All Baseline:**
   Both `mlops-serving` and `mlops-infra` enforce ingress and egress default denial.
2. **Explicit Whitelisting:**
   - **Inbound:** Inference pods accept TCP 8080 solely from the Ingress Controller namespace (`ingress-nginx`). Prometheus scrapes are restricted to `mlops-monitoring` source pods.
   - **Outbound:** Egress from inference workloads is blocked to internal private cluster subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) and cloud metadata (`169.254.169.254/32`). Only DNS (UDP/TCP 53) and secure HTTPS (TCP 443) to AWS S3 endpoints are permitted.
3. **Least Privilege RBAC:**
   - Pods are denied cluster-wide read/write capabilities.
   - Workloads bind to scoped `ServiceAccount`s utilizing OIDC token exchange (IRSA) for temporary, rotating STS credentials.
