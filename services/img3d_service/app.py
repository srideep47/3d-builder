"""img3d service — local image-to-3D HTTP microservice (PLAN.md §9).

Runs on the inference host (Forge, RTX 4080 Super). Single-job GPU queue:
one generation at a time, the selected model stays loaded between jobs.

Endpoints:
  GET  /health            — status, selected model, queue depth, device
  GET  /models            — registered backends with availability
  POST /generate          — multipart image (+ optional form fields) → job id
  GET  /result/{job_id}   — queued | running | completed | failed (+ metrics)
  GET  /download/{job_id} — the generated GLB (completed jobs only)

Auth: when THREED_IMG3D_TOKEN is set, /generate, /result and /download
require `Authorization: Bearer <token>` (LAN mode). Localhost needs no token.

Run (from the repo root, inside the service venv):
  uvicorn app:app --host 127.0.0.1 --port 8501
Backend selection: IMG3D_MODEL=tripo_sr (default: mock). Weights cache:
IMG3D_MODELS_DIR (default: <repo>/models). Job artifacts:
IMG3D_DATA_DIR (default: <service>/data).
"""

from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from providers import BACKENDS, DEFAULT_BACKEND, create_backend
from providers.base import GenerateParams

SERVICE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SERVICE_DIR.parents[1]

DATA_DIR = Path(os.environ.get("IMG3D_DATA_DIR", SERVICE_DIR / "data"))
MODELS_DIR = Path(os.environ.get("IMG3D_MODELS_DIR", REPO_ROOT / "models"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="3D Builder img3d service", version="0.1.0")

# ── Single-job GPU queue ─────────────────────────────────────────────────────

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_queue: "queue.Queue[str]" = queue.Queue()

_backend_name = os.environ.get("IMG3D_MODEL", DEFAULT_BACKEND)
_backend = create_backend(_backend_name, models_dir=MODELS_DIR)
_backend_load_error: str | None = None
_gpu_lock = threading.Lock()  # one generation at a time, model stays loaded


def _worker() -> None:
    global _backend_load_error
    while True:
        job_id = _queue.get()
        with _jobs_lock:
            job = _jobs.get(job_id)
        if job is None:
            continue
        with _jobs_lock:
            job["status"] = "running"
        try:
            with _gpu_lock:
                try:
                    _backend.load()
                    _backend_load_error = None
                except Exception as e:
                    _backend_load_error = str(e)
                    raise
                params = GenerateParams(
                    image_path=Path(job["image_path"]),
                    output_dir=Path(job["work_dir"]),
                    target_size_m=job["target_size_m"],
                    max_tris=job["max_tris"],
                    seed=job["seed"],
                )
                out = _backend.generate(params)
            with _jobs_lock:
                job["status"] = "completed"
                job["glb_path"] = str(out.glb_path)
                job["tri_count"] = out.tri_count
                job["duration_sec"] = round(out.duration_sec, 2)
        except Exception as e:
            with _jobs_lock:
                job["status"] = "failed"
                job["error"] = str(e)
        finally:
            _queue.task_done()


threading.Thread(target=_worker, name="img3d-worker", daemon=True).start()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _require_token(authorization: str | None) -> None:
    expected = os.environ.get("THREED_IMG3D_TOKEN")
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


def _public_job(job: dict) -> dict:
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "model": job["model"],
        "created_at": job["created_at"],
        "error": job.get("error"),
        "glb_path": job.get("glb_path"),
        "tri_count": job.get("tri_count"),
        "duration_sec": job.get("duration_sec"),
    }


# ── Endpoints ────────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "img3d",
        "model": _backend_name,
        "model_loaded": getattr(_backend, "model", None) is not None or _backend_name == "mock",
        "load_error": _backend_load_error,
        "queue_depth": _queue.qsize(),
        "jobs_total": len(_jobs),
        "torch_cuda": _cuda_info(),
    }


def _cuda_info() -> dict | None:
    try:
        import torch

        return {
            "available": torch.cuda.is_available(),
            "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        }
    except Exception:
        return None


@app.get("/models")
def models() -> dict:
    out = {}
    for name, cls in BACKENDS.items():
        probe = cls(models_dir=MODELS_DIR)
        try:
            available, reason = probe.is_available()
        except Exception as e:
            available, reason = False, str(e)
        out[name] = {"available": available, "detail": reason, "selected": name == _backend_name}
    return {"backends": out, "default": _backend_name}


@app.post("/generate")
async def generate(
    file: UploadFile = File(...),
    target_x: float | None = Form(None),
    target_y: float | None = Form(None),
    target_z: float | None = Form(None),
    max_tris: int = Form(50000),
    seed: int | None = Form(None),
    authorization: str | None = Header(None),
) -> dict:
    _require_token(authorization)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty image upload")

    job_id = uuid.uuid4().hex[:12]
    work_dir = DATA_DIR / "jobs" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    image_path = work_dir / (Path(file.filename or "input.png").name)
    image_path.write_bytes(content)

    target_size_m = None
    if target_x is not None and target_y is not None and target_z is not None:
        if min(target_x, target_y, target_z) <= 0:
            raise HTTPException(status_code=400, detail="target sizes must be > 0 (meters)")
        target_size_m = [target_x, target_y, target_z]

    job = {
        "job_id": job_id,
        "status": "queued",
        "model": _backend_name,
        "created_at": time.time(),
        "image_path": str(image_path),
        "work_dir": str(work_dir),
        "target_size_m": target_size_m,
        "max_tris": int(max_tris),
        "seed": seed,
    }
    with _jobs_lock:
        _jobs[job_id] = job
    _queue.put(job_id)
    return {"job_id": job_id, "status": "queued", "models": "/models"}


@app.get("/result/{job_id}")
def result(job_id: str, authorization: str | None = Header(None)) -> dict:
    _require_token(authorization)
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job id '{job_id}'")
    return _public_job(job)


@app.get("/download/{job_id}")
def download(job_id: str, authorization: str | None = Header(None)):
    _require_token(authorization)
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job id '{job_id}'")
    if job["status"] != "completed" or not job.get("glb_path"):
        raise HTTPException(status_code=409, detail=f"job is {job['status']}, not completed")
    return FileResponse(job["glb_path"], media_type="model/gltf-binary", filename=f"{job_id}.glb")
