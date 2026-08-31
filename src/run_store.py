"""Run store — manages per-run workspace directories, artifacts, and manifests."""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .spec.schema import ObjectSpec


@dataclass
class RunManifest:
    run_id: str
    created_at: float
    model_name: str
    spec_path: str
    final_glb_path: str | None
    renders: dict[str, str]
    dimension_gate_passed: bool
    mesh_gate_passed: bool
    tri_count: int
    vertex_count: int
    dimensions_m: list[float]
    metrics: dict[str, Any]
    status: str  # "completed", "failed", "in_progress"


class RunStore:
    def __init__(self, root_dir: str | Path | None = None):
        if root_dir:
            self.base_dir = Path(root_dir)
        else:
            self.base_dir = Path(__file__).resolve().parents[1] / "output" / "runs"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def create_run(self, name_prefix: str = "run") -> Path:
        """Create a new unique run directory."""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:6]
        run_id = f"{timestamp}_{name_prefix}_{short_id}"
        run_path = self.base_dir / run_id
        run_path.mkdir(parents=True, exist_ok=True)
        (run_path / "renders").mkdir(exist_ok=True)
        (run_path / "steps").mkdir(exist_ok=True)
        return run_path

    def save_spec(self, run_dir: Path, spec: ObjectSpec) -> Path:
        spec_path = run_dir / "spec.json"
        with open(spec_path, "w", encoding="utf-8") as f:
            f.write(spec.model_dump_json(indent=2))
        return spec_path

    def save_manifest(self, run_dir: Path, manifest: RunManifest) -> Path:
        manifest_path = run_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(asdict(manifest), f, indent=2, ensure_ascii=False)
        return manifest_path

    def get_run_manifest(self, run_id: str) -> dict[str, Any] | None:
        manifest_path = self.base_dir / run_id / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def list_runs(self) -> list[dict[str, Any]]:
        runs = []
        if not self.base_dir.exists():
            return runs
        for item in sorted(self.base_dir.iterdir(), reverse=True):
            if item.is_dir():
                mf = item / "manifest.json"
                if mf.exists():
                    try:
                        with open(mf, "r", encoding="utf-8") as f:
                            runs.append(json.load(f))
                    except Exception:
                        runs.append({"run_id": item.name, "status": "corrupt_manifest"})
                else:
                    runs.append({"run_id": item.name, "status": "incomplete"})
        return runs
