"""Production FastAPI application for real-time model inference and telemetry."""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from src.serving.model_loader import ModelLoader, get_model_loader

# Logging Setup
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("serving.app")

# OpenTelemetry Tracing Initialization
resource = Resource.create(
    {
        "service.name": os.getenv("OTEL_SERVICE_NAME", "k8s-mlops-serving"),
        "service.version": os.getenv("APP_VERSION", "1.0.0"),
        "deployment.environment": os.getenv("ENVIRONMENT", "production"),
    }
)
provider = TracerProvider(resource=resource)
provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("k8s-mlops-serving.tracer", "1.0.0")

# Prometheus Metrics Instrumentation
INFERENCE_REQUESTS_TOTAL = Counter(
    name="inference_requests_total",
    documentation="Cumulative total of model inference requests processed.",
    labelnames=["model_name", "model_version", "status"],
)

INFERENCE_LATENCY_SECONDS = Histogram(
    name="inference_latency_seconds_bucket",
    documentation="Latency distribution for model inference executions in seconds.",
    labelnames=["model_name", "status"],
    buckets=(0.002, 0.005, 0.010, 0.025, 0.050, 0.075, 0.100, 0.250, 0.500, 1.000),
)

MODEL_INPUT_FEATURES_GAUGE = Gauge(
    name="model_input_features_gauge",
    documentation="Dimension count of input features passed to the model.",
    labelnames=["model_name"],
)

MODEL_CONFIDENCE_GAUGE = Gauge(
    name="model_inference_confidence_score",
    documentation="Instantaneous confidence score of top prediction.",
    labelnames=["model_name"],
)

MODEL_MEMORY_USAGE_MB = Gauge(
    name="model_serving_memory_usage_mb",
    documentation="Resident Set Size (RSS) memory consumed by serving process in MB.",
    labelnames=["model_name"],
)

# Audit Buffer for Drift Monitoring
class ProductionAuditBuffer:
    def __init__(self, capacity: int = 10000):
        self.capacity = capacity
        self.buffer: deque[dict[str, Any]] = deque(maxlen=capacity)

    def record(self, record: dict[str, Any]) -> None:
        self.buffer.append(record)

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self.buffer)

    def __len__(self) -> int:
        return len(self.buffer)


audit_buffer = ProductionAuditBuffer(
    capacity=int(os.getenv("AUDIT_BUFFER_CAPACITY", "50000"))
)

# Pydantic v2 Request / Response Schemas
class InferenceRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    request_id: str = Field(
        default_factory=lambda: f"req-{uuid.uuid4()}",
        description="Globally unique request correlation ID.",
    )
    features: list[float] = Field(
        ...,
        min_length=1,
        description="Continuous numerical feature vector.",
    )
    model_name: str | None = Field(default=None)
    model_version: str | None = Field(default=None)


class InferenceResponse(BaseModel):
    request_id: str = Field(..., description="Correlated request identifier.")
    predictions: list[int | float] = Field(..., description="Predicted discrete class or outputs.")
    probabilities: list[float] = Field(default_factory=list)
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    latency_ms: float = Field(..., description="Inference latency in milliseconds.")
    model_name: str = Field(..., description="Serving model identifier.")
    model_version: str = Field(..., description="Loaded artifact version.")
    timestamp: float = Field(default_factory=time.time)


class HealthStatus(BaseModel):
    status: str
    model_loaded: bool
    model_name: str
    model_version: str
    memory_usage_mb: float
    uptime_seconds: float
    audit_buffer_size: int


START_TIME = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting k8s-mlops model serving engine...")
    loader: ModelLoader = get_model_loader()
    model_uri = os.getenv("MODEL_URI", None)
    loader.load(model_uri)
    loader.warmup(runs=5)

    meta = loader.get_metadata()
    logger.info("Model ready: name=%s version=%s", meta.get("model_name"), meta.get("model_version"))

    yield

    logger.info("Commencing graceful shutdown...")
    try:
        provider.shutdown()
    except Exception as exc:
        logger.error("Error shutting down tracer provider: %s", exc)


app = FastAPI(
    title="K8s MLOps Model Serving Engine",
    description="High-performance model serving with Prometheus/OTel instrumentation.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", f"corr-{uuid.uuid4()}")
    response: Response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


@app.post(
    "/v1/predict",
    response_model=InferenceResponse,
    status_code=status.HTTP_200_OK,
    summary="Real-time Model Inference",
    tags=["Inference"],
)
async def predict(payload: InferenceRequest) -> InferenceResponse:
    start_time = time.perf_counter()
    loader = get_model_loader()

    if not loader.is_loaded():
        logger.error("Inference requested but model is not loaded.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model artifact is currently unavailable.",
        )

    meta = loader.get_metadata()
    model_name = payload.model_name or meta.get("model_name", "unknown_model")
    model_version = payload.model_version or meta.get("model_version", "unknown_version")

    with tracer.start_as_current_span("model_inference_execution") as span:
        span.set_attribute("mlops.request_id", payload.request_id)
        span.set_attribute("mlops.model_name", model_name)
        span.set_attribute("mlops.model_version", model_version)
        span.set_attribute("mlops.feature_count", len(payload.features))

        try:
            MODEL_INPUT_FEATURES_GAUGE.labels(model_name=model_name).set(len(payload.features))

            predictions, probabilities, confidence = loader.predict(payload.features)

            elapsed_seconds = time.perf_counter() - start_time
            latency_ms = round(elapsed_seconds * 1000.0, 3)

            INFERENCE_REQUESTS_TOTAL.labels(
                model_name=model_name,
                model_version=model_version,
                status="success",
            ).inc()
            INFERENCE_LATENCY_SECONDS.labels(
                model_name=model_name,
                status="success",
            ).observe(elapsed_seconds)
            MODEL_CONFIDENCE_GAUGE.labels(model_name=model_name).set(confidence)

            audit_entry = {
                "request_id": payload.request_id,
                "timestamp": time.time(),
                "model_name": model_name,
                "model_version": model_version,
                "features": payload.features,
                "predictions": predictions,
                "confidence_score": confidence,
                "latency_ms": latency_ms,
            }
            audit_buffer.record(audit_entry)

            return InferenceResponse(
                request_id=payload.request_id,
                predictions=predictions,
                probabilities=probabilities,
                confidence_score=confidence,
                latency_ms=latency_ms,
                model_name=model_name,
                model_version=model_version,
            )

        except Exception as exc:
            elapsed_seconds = time.perf_counter() - start_time
            INFERENCE_REQUESTS_TOTAL.labels(
                model_name=model_name,
                model_version=model_version,
                status="error",
            ).inc()
            INFERENCE_LATENCY_SECONDS.labels(
                model_name=model_name,
                status="error",
            ).observe(elapsed_seconds)

            span.record_exception(exc)
            span.set_status(trace.StatusCode.ERROR, str(exc))
            logger.exception("Inference failed for request_id=%s: %s", payload.request_id, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Inference execution error: {str(exc)}",
            ) from exc


@app.get(
    "/health",
    response_model=HealthStatus,
    status_code=status.HTTP_200_OK,
    summary="Liveness and Readiness Probe",
    tags=["Observability"],
)
async def health() -> JSONResponse:
    loader = get_model_loader()
    is_ready = loader.is_loaded()
    mem_info = loader.get_memory_status()
    meta = loader.get_metadata()

    model_name = meta.get("model_name", "unknown")
    rss_mb = mem_info.get("rss_mb", 0.0)
    MODEL_MEMORY_USAGE_MB.labels(model_name=model_name).set(rss_mb)

    health_data = {
        "status": "healthy" if is_ready else "degraded",
        "model_loaded": is_ready,
        "model_name": model_name,
        "model_version": meta.get("model_version", "unknown"),
        "memory_usage_mb": rss_mb,
        "uptime_seconds": round(time.time() - START_TIME, 2),
        "audit_buffer_size": len(audit_buffer),
    }

    status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=health_data, status_code=status_code)


@app.get(
    "/metrics",
    summary="Prometheus Metrics Endpoint",
    tags=["Observability"],
)
async def metrics() -> Response:
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
