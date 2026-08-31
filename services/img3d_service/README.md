# img3d service — local image-to-3D microservice

FastAPI service that turns a single reference image into a GLB mesh. Runs on
the inference host (**Forge**, RTX 4080 Super) with a **single-job GPU
queue**: one generation at a time, the model stays loaded between jobs.
PC 2 (Scout) calls it over LAN. See PLAN.md §9 and PROJECT_PLAN.md §13.2.

## Backends

| name | VRAM | state | notes |
|---|---|---|---|
| `mock` | none | ✅ working | deterministic displaced icosphere; pipeline bring-up + tests |
| `tripo_sr` | ~6 GB | ✅ wired | fastest/lightest real model; vendored repo + HF weights |
| `trellis` | ~16 GB | ⬜ stub | best geometry; install during the M4 bake-off |
| `hunyuan3d` | ~12 GB | ⬜ stub | best PBR textures; install during the M4 bake-off |

## Running (mock backend — main env is enough)

```powershell
.venv/Scripts/python -m uvicorn app:app --app-dir services/img3d_service --host 127.0.0.1 --port 8501
# or: scripts/start-img3d.ps1
```

## Running (GPU backend, service venv)

```powershell
uv venv services/img3d_service/.venv --python 3.11
uv pip install -r services/img3d_service/requirements-gpu.txt -p services/img3d_service/.venv
powershell -ExecutionPolicy Bypass -File scripts/setup-img3d-gpu.ps1   # vendors TripoSR repo
$env:IMG3D_MODEL = "tripo_sr"
services/img3d_service/.venv/Scripts/python -m uvicorn app:app --app-dir services/img3d_service --port 8501
```

Weights are cached under `<repo>/models/` (gitignored). Job artifacts under
`services/img3d_service/data/` (gitignored).

## API

```
GET  /health              → {status, model, model_loaded, queue_depth, torch_cuda}
GET  /models              → backend registry with availability probes
POST /generate            → multipart: file=<image>, target_x/y/z (m, optional),
                            max_tris (default 50000), seed → {job_id}
GET  /result/{job_id}     → {status: queued|running|completed|failed, glb_path,
                            tri_count, duration_sec, error}
GET  /download/{job_id}   → GLB bytes (completed jobs only)
```

Auth: set `THREED_IMG3D_TOKEN` to require `Authorization: Bearer <token>`
on /generate, /result, /download (LAN mode). Env: `IMG3D_MODEL` (backend
selection), `IMG3D_MODELS_DIR`, `IMG3D_DATA_DIR`.

The agent-side client is `src/img3d/client.py` (`RemoteImg3DProvider`),
configured by `config/hardware.yaml` (`img3d.base_url`, `img3d.enabled`).

## Post-processing contract

Neural output is never shipped raw: backends decimate to `max_tris` when the
simplifier is available and scale to `target_size` (exact bounds, per-axis).
The Blender harness additionally re-scales to the part's `target_size` on
import and generates UVs — so measured dimensions are enforced twice.
