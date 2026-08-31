"""3D Builder web UI server — FastAPI app with REST API, WebSocket progress
streaming, and static frontend hosting.

Run with:  python -m src.cli ui   (or uvicorn src.webapp.server:app)
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..ai.aptos import load_ai_config
from ..blender.locate import locate_blender
from ..materials.pbr import list_material_presets
from ..run_store import RunStore
from ..spec.schema import ObjectSpec
from .runner import RunRegistry

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
UPLOAD_DIR = Path(__file__).resolve().parents[2] / "output" / "uploads"


def create_app(pipeline=None) -> FastAPI:
    app = FastAPI(title="3D Builder", version="1.0.0")
    registry = RunRegistry(pipeline=pipeline)
    store = RunStore()
    app.state.registry = registry
    app.state.store = store

    # ── helpers ──────────────────────────────────────────────────────────

    def _run_dir(run_id: str) -> Path:
        base = store.base_dir.resolve()
        candidate = (base / run_id).resolve()
        if not str(candidate).startswith(str(base)) or not candidate.is_dir():
            raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
        return candidate

    def _safe_file(run_id: str, rel_path: str) -> Path:
        run_dir = _run_dir(run_id)
        target = (run_dir / rel_path).resolve()
        if not str(target).startswith(str(run_dir)) or not target.is_file():
            raise HTTPException(status_code=404, detail=f"Artifact not found: {rel_path}")
        return target

    # ── system ───────────────────────────────────────────────────────────

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        blender = locate_blender()
        ai = registry.pipeline.provider.health()
        config = load_ai_config()
        return {
            "blender": {
                "available": blender is not None and blender.supported,
                "version": blender.version if blender else None,
                "path": blender.executable if blender else None,
            },
            "ai": {
                "healthy": ai.healthy,
                "model": ai.model,
                "endpoint": ai.endpoint,
                "vision_supported": ai.vision_supported,
                "tools_supported": ai.tools_supported,
                "error": ai.error,
            },
            "config": {
                "reasoning_effort": config.get("reasoning_effort"),
                "max_tokens": config.get("max_tokens"),
                "max_iterations": (config.get("agent") or {}).get("max_iterations"),
                "wall_clock_budget_s": (config.get("agent") or {}).get("wall_clock_budget_s"),
            },
            "agent": {
                "max_iterations": registry.pipeline.loop.max_iterations,
                "wall_clock_budget_s": registry.pipeline.loop.wall_clock_budget_s,
            },
        }

    @app.get("/api/presets")
    def presets() -> list[dict[str, Any]]:
        return list_material_presets()

    # ── uploads (reference images) ───────────────────────────────────────

    @app.post("/api/uploads")
    async def upload_images(files: list[UploadFile]) -> dict[str, Any]:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        saved = []
        for f in files:
            suffix = Path(f.filename or "image").suffix[:10] or ".png"
            dest = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{Path(f.filename or 'image').stem[:40]}{suffix}"
            content = await f.read()
            dest.write_bytes(content)
            saved.append({"name": f.filename, "path": str(dest), "size": len(content)})
        return {"files": saved}

    # ── build ────────────────────────────────────────────────────────────

    @app.post("/api/build")
    async def build(payload: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode", "ai")).lower()
        if mode == "spec":
            spec_data = payload.get("spec")
            if not isinstance(spec_data, dict):
                raise HTTPException(status_code=400, detail="mode 'spec' requires a 'spec' object")
            try:
                spec = ObjectSpec.model_validate(spec_data)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid ObjectSpec: {e}")
            run_id = registry.start_spec(spec)
        else:
            prompt = str(payload.get("prompt", "")).strip()
            if not prompt:
                raise HTTPException(status_code=400, detail="mode 'ai' requires a non-empty 'prompt'")
            images = [str(p) for p in (payload.get("images") or []) if Path(str(p)).exists()]
            run_id = registry.start_ai(
                prompt=prompt,
                measurements=str(payload.get("measurements", "")),
                material_preset=payload.get("material_preset") or None,
                images=images,
            )
        return {"run_id": run_id}

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> dict[str, Any]:
        if not registry.cancel(run_id):
            raise HTTPException(status_code=404, detail=f"No active run: {run_id}")
        return {"cancelled": True}

    # ── runs ─────────────────────────────────────────────────────────────

    @app.get("/api/runs")
    def list_runs() -> list[dict[str, Any]]:
        # Persisted manifests first, then overlay this session's registry view
        # (fresher status) so finished runs keep their rich manifest fields.
        merged: dict[str, dict[str, Any]] = {}
        for manifest in store.list_runs():
            entry = {**manifest, "live": False}
            merged[str(manifest.get("run_id"))] = entry
        for r in registry.active_runs():
            rid = str(r.get("run_id"))
            merged[rid] = {**merged.get(rid, {}), **r}
        return sorted(merged.values(), key=lambda r: str(r.get("run_id", "")), reverse=True)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        active = registry.get(run_id)
        run_dir = _run_dir(run_id)
        manifest_path = run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
        spec_path = run_dir / "spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8")) if spec_path.exists() else None

        renders: dict[str, str] = {}
        if manifest:
            for view, abs_path in (manifest.get("renders") or {}).items():
                renders[view] = f"/api/runs/{run_id}/file/renders/{Path(abs_path).name}"

        steps = [
            f"/api/runs/{run_id}/file/steps/{p.name}" for p in sorted((run_dir / "steps").glob("*.glb"))
        ] if (run_dir / "steps").is_dir() else []

        return {
            "run_id": run_id,
            "live": active is not None and active.status == "running",
            "status": (active.status if active else None) or (manifest or {}).get("status"),
            "mode": active.mode if active else ("spec" if "ui_spec" in run_id else "ai"),
            "label": active.label if active else (manifest or {}).get("model_name"),
            "manifest": manifest,
            "spec": spec,
            "renders": renders,
            "steps": steps,
            "final_glb": f"/api/runs/{run_id}/file/final.glb" if (run_dir / "final.glb").exists() else None,
            "run_dir": str(run_dir),
            "events": registry.history(run_id),
        }

    @app.get("/api/runs/{run_id}/file/{rel_path:path}")
    def run_file(run_id: str, rel_path: str) -> FileResponse:
        target = _safe_file(run_id, rel_path)
        media = "model/gltf-binary" if target.suffix == ".glb" else (
            "image/png" if target.suffix == ".png" else "application/octet-stream"
        )
        return FileResponse(target, media_type=media, filename=target.name)

    # ── WebSocket progress stream ────────────────────────────────────────

    @app.websocket("/api/ws/{run_id}")
    async def run_ws(websocket: WebSocket, run_id: str) -> None:
        await websocket.accept()
        active = registry.get(run_id)
        if active is None or active.status != "running":
            for ev in registry.history(run_id):
                await websocket.send_json(ev)
            await websocket.send_json({"event": "ws_closed", "reason": "run not live"})
            await websocket.close()
            return

        # Subscribe BEFORE replaying history so no event is lost in between;
        # the client tolerates the rare duplicate.
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        registry.subscribe(run_id, loop, queue)
        try:
            for ev in registry.history(run_id):
                await websocket.send_json(ev)
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    current = registry.get(run_id)
                    if current is None or current.status != "running":
                        break
                    continue
                await websocket.send_json(ev)
                if ev.get("event") in ("run_finished", "run_error"):
                    break
        except WebSocketDisconnect:
            pass
        finally:
            registry.unsubscribe(run_id, loop, queue)
            try:
                await websocket.close()
            except Exception:
                pass

    # ── static frontend (registered last so /api wins) ───────────────────

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    return app


app = create_app()
