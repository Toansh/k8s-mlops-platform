"""Test suite for FastAPI model serving and statistical drift detection."""

import numpy as np
import pytest
from fastapi import status
from fastapi.testclient import TestClient

from src.serving.app import app
from src.serving.model_loader import get_model_loader
from src.drift_detection.drift_detector import (
    StatisticalDriftDetector,
    calculate_ks_test,
    calculate_psi,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def ensure_model_loaded():
    loader = get_model_loader()
    loader.load(None)
    loader.warmup(runs=2)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "healthy"
    assert data["model_loaded"] is True
    assert "memory_usage_mb" in data
    assert "uptime_seconds" in data


def test_predict_success():
    payload = {
        "request_id": "test-req-001",
        "features": [0.42, 1.15, -0.89, 2.34, 0.12],
    }
    response = client.post("/v1/predict", json=payload)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["request_id"] == "test-req-001"
    assert "predictions" in data
    assert "confidence_score" in data
    assert "latency_ms" in data
    assert data["confidence_score"] >= 0.0


def test_predict_validation_error():
    # Empty features list violates min_length=1
    payload = {
        "request_id": "invalid-req",
        "features": [],
    }
    response = client.post("/v1/predict", json=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_metrics_endpoint():
    response = client.get("/metrics")
    assert response.status_code == status.HTTP_200_OK
    assert "inference_requests_total" in response.text


def test_ks_test_no_drift():
    np.random.seed(42)
    ref = np.random.normal(0.0, 1.0, 500)
    cur = np.random.normal(0.0, 1.0, 500)
    d_stat, p_val, drift = calculate_ks_test(ref, cur, alpha=0.01)
    assert drift is False
    assert p_val > 0.01


def test_ks_test_significant_drift():
    np.random.seed(42)
    ref = np.random.normal(0.0, 1.0, 500)
    cur = np.random.normal(2.5, 1.0, 500)  # Significant mean shift
    d_stat, p_val, drift = calculate_ks_test(ref, cur, alpha=0.05)
    assert drift is True
    assert p_val < 0.05


def test_psi_calculation():
    np.random.seed(42)
    ref = np.random.normal(0.0, 1.0, 1000)
    cur_stable = np.random.normal(0.02, 0.99, 1000)
    psi_val, psi_status = calculate_psi(ref, cur_stable)
    assert psi_status == "STABLE"
    assert psi_val < 0.10

    cur_drifted = np.random.normal(1.5, 1.2, 1000)
    psi_drift_val, psi_drift_status = calculate_psi(ref, cur_drifted)
    assert psi_drift_val > 0.10
