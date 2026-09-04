"""HTTP client for the local img3d service (services/img3d_service).

Implements the existing ImageTo3DProvider ABC against the service's async
job API: POST /generate → poll GET /result/<id> → GET /download/<id>. Only
httpx — no torch — so it lives in the light main environment.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

from .provider import ImageTo3DProvider, ImageTo3DResult

DEFAULT_HARDWARE_CONFIG = Path(__file__).resolve().parents[2] / "config" / "hardware.yaml"

_DEFAULTS: dict[str, Any] = {
    "role": "standalone",
    "img3d": {
        "enabled": False,
        "base_url": "http://127.0.0.1:8501",
        "token_env": "THREED_IMG3D_TOKEN",
    },
}


def load_hardware_config(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_HARDWARE_CONFIG
    try:
        if p.exists():
            loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            return {**_DEFAULTS, **loaded}
    except Exception:
        pass
    return dict(_DEFAULTS)


class RemoteImg3DProvider(ImageTo3DProvider):
    """Talks to the img3d FastAPI service over HTTP (LAN-capable)."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout_sec: float = 600.0,
        poll_interval_s: float = 1.5,
        health_timeout_s: float = 3.0,
    ):
        cfg = load_hardware_config().get("img3d", {}) or {}
        self.base_url = (base_url or cfg.get("base_url") or "http://127.0.0.1:8501").rstrip("/")
        token_env = cfg.get("token_env") or "THREED_IMG3D_TOKEN"
        self.token = token or os.environ.get(token_env)
        self.timeout_sec = float(timeout_sec)
        self.poll_interval_s = float(poll_interval_s)
        self.health_timeout_s = float(health_timeout_s)
        self._available: bool | None = None  # cached after first probe

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def is_available(self, recheck: bool = False) -> bool:
        """Cheap liveness probe; the result is cached for the process lifetime."""
        if self._available is not None and not recheck:
            return self._available
        try:
            resp = httpx.get(f"{self.base_url}/health", timeout=self.health_timeout_s)
            self._available = resp.status_code == 200
        except Exception:
            self._available = False
        return self._available

    def generate_mesh_from_image(
        self,
        image_path: str | Path,
        output_dir: str | Path,
        target_dimensions_m: list[float] | None = None,
    ) -> ImageTo3DResult:
        p = Path(image_path)
        started = time.perf_counter()
        if not p.exists():
            return ImageTo3DResult(
                success=False, output_glb_path=None, tri_count=0,
                duration_sec=0.0, error=f"Image file not found: {image_path}",
            )

        data: dict[str, Any] = {}
        if target_dimensions_m:
            if len(target_dimensions_m) != 3 or min(target_dimensions_m) <= 0:
                return ImageTo3DResult(
                    success=False, output_glb_path=None, tri_count=0,
                    duration_sec=0.0, error=f"target_dimensions_m must be [x, y, z] > 0, got {target_dimensions_m}",
                )
            data = {
                "target_x": float(target_dimensions_m[0]),
                "target_y": float(target_dimensions_m[1]),
                "target_z": float(target_dimensions_m[2]),
            }

        try:
            with open(p, "rb") as img_file:
                resp = httpx.post(
                    f"{self.base_url}/generate",
                    files={"file": (p.name, img_file, "application/octet-stream")},
                    data=data,
                    headers=self._headers(),
                    timeout=30.0,
                )
            if resp.status_code == 401:
                return self._fail("img3d service rejected the token (401)", started)
            if resp.status_code != 200:
                return self._fail(f"img3d service returned HTTP {resp.status_code}: {resp.text[:300]}", started)
            job_id = resp.json().get("job_id")
            if not job_id:
                return self._fail(f"img3d service returned no job_id: {resp.text[:300]}", started)

            # Poll until the job leaves the single-job GPU queue and finishes.
            deadline = time.monotonic() + self.timeout_sec
            while True:
                r = httpx.get(
                    f"{self.base_url}/result/{job_id}", headers=self._headers(), timeout=10.0
                )
                if r.status_code != 200:
                    return self._fail(f"result poll HTTP {r.status_code}: {r.text[:300]}", started)
                job = r.json()
                status = job.get("status")
                if status == "failed":
                    return self._fail(f"img3d generation failed: {job.get('error')}", started, job)
                if status == "completed":
                    break
                if time.monotonic() > deadline:
                    return self._fail(f"img3d job timed out after {self.timeout_sec}s (status: {status})", started, job)
                time.sleep(self.poll_interval_s)

            out_d = Path(output_dir)
            out_d.mkdir(parents=True, exist_ok=True)
            target_glb = out_d / f"{p.stem}_{job_id[:8]}.glb"
            dl = httpx.get(
                f"{self.base_url}/download/{job_id}", headers=self._headers(), timeout=60.0
            )
            if dl.status_code != 200:
                return self._fail(f"download HTTP {dl.status_code}: {dl.text[:300]}", started, job)
            target_glb.write_bytes(dl.content)

            return ImageTo3DResult(
                success=True,
                output_glb_path=target_glb,
                tri_count=int(job.get("tri_count") or 0),
                duration_sec=time.perf_counter() - started,
                error=job.get("error"),
            )
        except Exception as e:
            return self._fail(str(e), started)

    def generate_mesh_from_views(
        self,
        views: dict[str, str | Path],
        output_dir: str | Path,
        max_tris: int | None = None,
        seed: int | None = None,
    ) -> ImageTo3DResult:
        """Labelled multi-view generation (§4.2 neural intake): front required,
        back/left/right optional, keys validated server-side by the backend.
        Sizing is deliberately NOT requested — conform (§4.4) owns scaling and
        must see the raw aspect ratio."""
        started = time.perf_counter()
        label_paths = {str(k): Path(v) for k, v in views.items()}
        if "front" not in label_paths:
            return self._fail("multi-view generation needs a 'front' view", started)
        for label, p in label_paths.items():
            if not p.is_file():
                return self._fail(f"view image not found ({label}): {p}", started)

        files: dict[str, tuple[str, Any, str]] = {}
        for label, p in label_paths.items():
            mime = "image/png" if p.suffix.lower() == ".png" else "application/octet-stream"
            files[label] = (p.name, p.read_bytes(), mime)
        data: dict[str, Any] = {}
        if max_tris is not None:
            data["max_tris"] = str(int(max_tris))
        if seed is not None:
            data["seed"] = str(int(seed))

        try:
            resp = httpx.post(
                f"{self.base_url}/generate",
                files=files,
                data=data,
                headers=self._headers(),
                timeout=60.0,
            )
            if resp.status_code == 401:
                return self._fail("img3d service rejected the token (401)", started)
            if resp.status_code != 200:
                return self._fail(
                    f"img3d service returned HTTP {resp.status_code}: {resp.text[:300]}", started
                )
            job_id = resp.json().get("job_id")
            if not job_id:
                return self._fail(f"img3d service returned no job_id: {resp.text[:300]}", started)

            job = self._poll_job(job_id, started)
            if job is None:
                return self._fail(
                    f"img3d job timed out after {self.timeout_sec}s", started
                )
            if job.get("status") == "failed":
                return self._fail(f"img3d generation failed: {job.get('error')}", started, job)

            out_d = Path(output_dir)
            out_d.mkdir(parents=True, exist_ok=True)
            target_glb = out_d / f"neural_{job_id[:8]}.glb"
            dl = httpx.get(
                f"{self.base_url}/download/{job_id}", headers=self._headers(), timeout=60.0
            )
            if dl.status_code != 200:
                return self._fail(f"download HTTP {dl.status_code}: {dl.text[:300]}", started, job)
            target_glb.write_bytes(dl.content)

            return ImageTo3DResult(
                success=True,
                output_glb_path=target_glb,
                tri_count=int(job.get("tri_count") or 0),
                duration_sec=time.perf_counter() - started,
                error=job.get("error"),
            )
        except Exception as e:
            return self._fail(str(e), started)

    def _poll_job(self, job_id: str, started: float) -> dict | None:
        """Poll /result until the job leaves the queue; None on timeout, the
        final job dict otherwise (caller checks status == 'failed')."""
        deadline = time.monotonic() + self.timeout_sec
        while True:
            r = httpx.get(
                f"{self.base_url}/result/{job_id}", headers=self._headers(), timeout=10.0
            )
            if r.status_code != 200:
                raise RuntimeError(f"result poll HTTP {r.status_code}: {r.text[:300]}")
            job = r.json()
            status = job.get("status")
            if status in ("failed", "completed"):
                return job
            if time.monotonic() > deadline:
                return None
            time.sleep(self.poll_interval_s)

    def _fail(self, message: str, started: float, job: dict | None = None) -> ImageTo3DResult:
        return ImageTo3DResult(
            success=False,
            output_glb_path=None,
            tri_count=int((job or {}).get("tri_count") or 0),
            duration_sec=time.perf_counter() - started,
            error=message,
        )


def get_img3d_provider(config_path: str | Path | None = None) -> RemoteImg3DProvider | None:
    """Factory: the configured provider, or None when img3d is disabled.

    Availability is probed lazily by the caller (is_available()) so a stopped
    service costs one short health check, not an exception.
    """
    cfg = load_hardware_config(config_path)
    img3d_cfg = cfg.get("img3d", {}) or {}
    if not img3d_cfg.get("enabled", False):
        return None
    return RemoteImg3DProvider(
        base_url=img3d_cfg.get("base_url"),
        token=os.environ.get(img3d_cfg.get("token_env") or "THREED_IMG3D_TOKEN"),
    )
