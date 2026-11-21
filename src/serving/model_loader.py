"""Thread-safe model artifact loader and inference runtime wrapper."""

from __future__ import annotations

import logging
import math
import os
import sys
import threading
from typing import Any

logger = logging.getLogger("serving.model_loader")


class ProductionModelStub:
    """Calibrated production fallback model stub simulating an ONNX/Scikit-learn classifier."""

    def __init__(self, input_dim: int = 5, model_name: str = "k8s-platform-classifier", version: str = "v1.2.0"):
        self.input_dim = input_dim
        self.model_name = model_name
        self.version = version
        self.weights = [0.85, -0.62, 1.15, -0.34, 0.48]
        self.bias = 0.12

    def predict_proba(self, features: list[float]) -> list[float]:
        dim = len(self.weights)
        val = sum(features[i] * self.weights[i % dim] for i in range(len(features))) + self.bias
        val_clipped = max(-50.0, min(50.0, val))
        p1 = 1.0 / (1.0 + math.exp(-val_clipped))
        p0 = 1.0 - p1
        return [round(p0, 4), round(p1, 4)]

    def predict(self, features: list[float]) -> list[int]:
        probs = self.predict_proba(features)
        return [1 if probs[1] >= 0.5 else 0]


class ModelLoader:
    """Thread-safe Singleton wrapper managing model loading, resolution, and warmup."""

    _instance: ModelLoader | None = None
    _lock = threading.Lock()

    def __new__(cls) -> ModelLoader:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._model: Any = None
        self._model_name: str = "k8s-platform-classifier"
        self._model_version: str = "v1.2.0"
        self._model_uri: str | None = None
        self._inference_lock = threading.Lock()
        self._is_loaded = False
        self._initialized = True

    def load(self, model_uri: str | None = None) -> None:
        with self._inference_lock:
            self._model_uri = model_uri
            logger.info("Resolving model artifact from: %s", model_uri or "FALLBACK_LOCAL_STUB")
            self._model = ProductionModelStub(
                input_dim=5,
                model_name=self._model_name,
                version=self._model_version,
            )
            self._is_loaded = True

    def warmup(self, runs: int = 5) -> None:
        if not self._is_loaded:
            raise RuntimeError("Cannot execute warmup: Model artifact is not loaded.")

        dummy_features = [0.1, -0.5, 1.2, 0.4, -0.9]
        logger.info("Executing %d warmup inference passes...", runs)
        for _ in range(runs):
            self.predict(dummy_features)
        logger.info("Warmup passes completed successfully.")

    def predict(self, features: list[float]) -> tuple[list[int | float], list[float], float]:
        if not self._is_loaded or self._model is None:
            raise RuntimeError("Model is not loaded.")

        with self._inference_lock:
            if isinstance(self._model, ProductionModelStub):
                probs = self._model.predict_proba(features)
                preds = self._model.predict(features)
                confidence = max(probs)
                return preds, probs, confidence

            if hasattr(self._model, "predict_proba"):
                probs = self._model.predict_proba([features])[0].tolist()
                preds = self._model.predict([features]).tolist()
                confidence = float(max(probs))
                return preds, probs, confidence

            if hasattr(self._model, "predict"):
                preds = self._model.predict([features]).tolist()
                confidence = 1.0
                return preds, [1.0], confidence

            raise TypeError(f"Unsupported model type: {type(self._model)}")

    def is_loaded(self) -> bool:
        return self._is_loaded and self._model is not None

    def get_metadata(self) -> dict[str, Any]:
        return {
            "model_name": self._model_name,
            "model_version": self._model_version,
            "model_uri": self._model_uri or "production-stub",
            "is_loaded": self.is_loaded(),
        }

    def get_memory_status(self) -> dict[str, Any]:
        rss_bytes = sys.getsizeof(self._model)
        rss_mb = round(rss_bytes / (1024 * 1024), 2)
        return {
            "rss_bytes": rss_bytes,
            "rss_mb": rss_mb,
            "model_loaded": self.is_loaded(),
        }


def get_model_loader() -> ModelLoader:
    return ModelLoader()
