"""Inference logger — writes all LLM interactions as structured JSONL records."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class InferenceLog:
    def __init__(self, log_file: str | Path | None = None):
        if log_file:
            self.log_path = Path(log_file)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            # Default on: every model call is recorded for debugging.
            self.log_path = Path(__file__).resolve().parents[2] / "output" / "inference_log.jsonl"
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        role: str,
        messages: list[dict[str, Any]],
        response: dict[str, Any] | str,
        latency_ms: float,
        model: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        if not self.log_path:
            return

        record = {
            "timestamp": time.time(),
            "role": role,
            "model": model,
            "latency_ms": round(latency_ms, 2),
            "messages": messages,
            "response": response,
            "meta": meta or {},
        }

        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass
