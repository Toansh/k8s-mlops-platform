"""Statistical drift detection engine implementing Kolmogorov-Smirnov and PSI tests."""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy import stats

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] [drift_detector] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("drift_detector")


class FeatureDriftResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    feature_name: str
    ks_statistic: float
    ks_pvalue: float
    ks_drift_detected: bool
    psi_value: float
    psi_status: str
    has_drift: bool


class DriftReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str
    evaluated_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    reference_sample_size: int
    production_sample_size: int
    total_features_evaluated: int
    drifted_feature_count: int
    critical_alert_triggered: bool
    overall_status: str
    feature_metrics: dict[str, FeatureDriftResult]


def calculate_ks_test(
    reference: np.ndarray,
    current: np.ndarray,
    alpha: float = 0.05,
) -> tuple[float, float, bool]:
    ref_clean = reference[~np.isnan(reference)]
    cur_clean = current[~np.isnan(current)]

    if len(ref_clean) == 0 or len(cur_clean) == 0:
        return 0.0, 1.0, False

    result = stats.ks_2samp(ref_clean, cur_clean)
    d_stat = float(result.statistic)
    p_val = float(result.pvalue)
    drift_detected = bool(p_val < alpha)

    return round(d_stat, 5), round(p_val, 5), drift_detected


def calculate_psi(
    reference: np.ndarray,
    current: np.ndarray,
    num_bins: int = 10,
    epsilon: float = 1e-4,
) -> tuple[float, str]:
    ref_clean = reference[~np.isnan(reference)]
    cur_clean = current[~np.isnan(current)]

    if len(ref_clean) == 0 or len(cur_clean) == 0:
        return 0.0, "STABLE"

    quantiles = np.linspace(0, 100, num_bins + 1)
    bin_edges = np.percentile(ref_clean, quantiles)
    bin_edges = np.unique(bin_edges)

    if len(bin_edges) < 2:
        return 0.0, "STABLE"

    bin_edges[0] = -np.inf
    bin_edges[-1] = np.inf

    ref_counts, _ = np.histogram(ref_clean, bins=bin_edges)
    cur_counts, _ = np.histogram(cur_clean, bins=bin_edges)

    ref_pct = ref_counts / len(ref_clean)
    cur_pct = cur_counts / len(cur_clean)

    ref_pct = np.where(ref_pct == 0, epsilon, ref_pct)
    cur_pct = np.where(cur_pct == 0, epsilon, cur_pct)

    psi_vector = (cur_pct - ref_pct) * np.log(cur_pct / ref_pct)
    psi_total = float(np.sum(psi_vector))
    psi_total = max(0.0, round(psi_total, 4))

    if psi_total < 0.10:
        status = "STABLE"
    elif psi_total < 0.20:
        status = "MODERATE_DRIFT"
    else:
        status = "SIGNIFICANT_DRIFT"

    return psi_total, status


class StatisticalDriftDetector:
    def __init__(self, ks_alpha: float = 0.05, psi_threshold: float = 0.20):
        self.ks_alpha = ks_alpha
        self.psi_threshold = psi_threshold

    def evaluate(
        self,
        reference_data: dict[str, np.ndarray],
        current_data: dict[str, np.ndarray],
    ) -> DriftReport:
        shared_features = sorted(list(set(reference_data.keys()) & set(current_data.keys())))
        if not shared_features:
            raise ValueError("No common features found between reference and production datasets.")

        feature_metrics: dict[str, FeatureDriftResult] = {}
        critical_drift_count = 0
        ref_size = len(next(iter(reference_data.values())))
        cur_size = len(next(iter(current_data.values())))

        for feat in shared_features:
            ref_arr = reference_data[feat]
            cur_arr = current_data[feat]

            d_stat, p_val, ks_drift = calculate_ks_test(ref_arr, cur_arr, alpha=self.ks_alpha)
            psi_val, psi_status = calculate_psi(ref_arr, cur_arr)

            has_drift = ks_drift or (psi_val >= 0.10)
            if psi_val >= self.psi_threshold:
                critical_drift_count += 1

            feature_metrics[feat] = FeatureDriftResult(
                feature_name=feat,
                ks_statistic=d_stat,
                ks_pvalue=p_val,
                ks_drift_detected=ks_drift,
                psi_value=psi_val,
                psi_status=psi_status,
                has_drift=has_drift,
            )

        overall_status = "CLEAN"
        critical_alert = critical_drift_count > 0
        if critical_alert:
            overall_status = "CRITICAL_DRIFT"
        elif any(f.has_drift for f in feature_metrics.values()):
            overall_status = "WARNING"

        return DriftReport(
            report_id=str(uuid.uuid4()),
            reference_sample_size=ref_size,
            production_sample_size=cur_size,
            total_features_evaluated=len(shared_features),
            drifted_feature_count=sum(1 for f in feature_metrics.values() if f.has_drift),
            critical_alert_triggered=critical_alert,
            overall_status=overall_status,
            feature_metrics=feature_metrics,
        )


def run_cli() -> int:
    parser = argparse.ArgumentParser(description="Kubernetes MLOps Drift Detection Engine")
    parser.add_argument("--output-dir", type=str, default="./reports")
    parser.add_argument("--ks-alpha", type=float, default=0.05)
    parser.add_argument("--psi-threshold", type=float, default=0.20)
    args = parser.parse_args()

    np.random.seed(42)
    ref_data = {f"feature_{i}": np.random.normal(0.0, 1.0, 1000) for i in range(5)}
    cur_data = {
        "feature_0": np.random.normal(0.35, 1.0, 1000),
        "feature_1": np.random.normal(1.80, 1.2, 1000),
        "feature_2": np.random.normal(0.0, 1.0, 1000),
        "feature_3": np.random.normal(0.02, 0.98, 1000),
        "feature_4": np.random.normal(-0.01, 1.01, 1000),
    }

    detector = StatisticalDriftDetector(ks_alpha=args.ks_alpha, psi_threshold=args.psi_threshold)
    report = detector.evaluate(ref_data, cur_data)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_file = out_dir / f"drift_report_{report.report_id}.json"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report.model_dump_json(indent=2))

    logger.info("Drift evaluation report written to: %s", report_file)
    logger.info("Summary: Status=%s | Drifted Features=%d", report.overall_status, report.drifted_feature_count)
    return 0


if __name__ == "__main__":
    sys.exit(run_cli())
