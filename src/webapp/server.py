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
import yaml

from ..ai.aptos import load_ai_config
from ..blender.locate import locate_blender
from ..client.gates import MeshFacts, run_all_gates
from ..client.job import JobCard, JobDims, load_job
from ..materials.pbr import list_material_presets
from ..run_store import RunStore
from ..spec.schema import ObjectSpec
from ..spec.template import load_template
from .runner import RunRegistry

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
UPLOAD_DIR = Path(__file__).resolve().parents[2] / "output" / "uploads"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def create_app(pipeline=None) -> FastAPI:
    app = FastAPI(title="3D Builder", version="1.0.0")
    registry = RunRegistry(pipeline=pipeline)
    store = RunStore()
    app.state.registry = registry
    app.state.store = store
    # Client-delivery roots (T5 intake + compliance panel); overridable on
    # app.state so tests can point them at temp dirs.
    app.state.jobs_dir = PROJECT_ROOT / "input" / "jobs"
    app.state.templates_dir = PROJECT_ROOT / "templates"
    app.state.packages_root = PROJECT_ROOT / "output" / "packages"
    app.state.blocked_root = PROJECT_ROOT / "output" / "blocked"

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

    # ── client delivery: job intake + compliance panel (T5) ───────────────

    def _template_dir() -> Path:
        return Path(app.state.templates_dir)

    def _jobs_dir() -> Path:
        return Path(app.state.jobs_dir)

    def _qa_report(kind_root: Path, job_code: str) -> dict[str, Any] | None:
        p = kind_root / job_code / "qa_report.json"
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    @app.get("/api/templates")
    def list_templates() -> list[dict[str, Any]]:
        out = []
        for p in sorted(_template_dir().glob("*.yaml")):
            try:
                tpl = load_template(p)
                out.append({
                    "file": p.name,
                    "product_class": tpl.product_class,
                    "description": " ".join(tpl.description.split())[:200],
                    "tri_budget": tpl.tri_budget,
                })
            except Exception as e:
                out.append({"file": p.name, "error": str(e)[:200]})
        return out

    @app.get("/api/jobs")
    def list_jobs() -> list[dict[str, Any]]:
        out = []
        for p in sorted(_jobs_dir().glob("*.yaml")):
            try:
                job = load_job(p)
                out.append({
                    "job_code": job.job_code,
                    "file": p.name,
                    "dims": {
                        "length": job.dims.length, "width": job.dims.width,
                        "height": job.dims.height, "unit": job.dims.unit,
                    },
                    "dims_placeholder": job.dims_placeholder,
                    "product_class": job.product_class,
                    "complexity": job.complexity,
                    "orientation": job.orientation,
                })
            except Exception as e:
                out.append({"file": p.name, "error": str(e)[:200]})
        return out

    @app.post("/api/jobs")
    async def create_job(payload: dict[str, Any]) -> dict[str, Any]:
        """Write a job card from the intake form. Dimensions are REQUIRED
        with an explicit unit (rule 9 — never inferred). A card may be
        saved with `dims_placeholder: true` (delivery will be REFUSED) only
        when the form explicitly says the stand-ins are not owner-supplied.
        Existing job codes are never overwritten."""
        code = str(payload.get("job_code", "")).strip()
        dims_in = payload.get("dims") or {}
        try:
            card = JobCard(
                job_code=code,
                dims=JobDims(
                    length=float(dims_in.get("length", 0) or 0),
                    width=float(dims_in.get("width", 0) or 0),
                    height=float(dims_in.get("height", 0) or 0),
                    unit=str(dims_in.get("unit", "")).strip(),
                ),
                complexity=str(payload.get("complexity", "simple")),
                orientation=str(payload.get("orientation", "floor")),
                product_class=str(payload.get("product_class", "")).strip(),
                part_scope=str(payload.get("part_scope", "")).strip(),
                reference_dir=str(payload.get("reference_dir") or
                                  PROJECT_ROOT / "input" / "reference"),
                dims_placeholder=bool(payload.get("dims_placeholder")),
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid job card: {e}")

        _jobs_dir().mkdir(parents=True, exist_ok=True)
        path = _jobs_dir() / f"{card.job_code}.yaml"
        if path.exists():
            raise HTTPException(status_code=409,
                                detail=f"Job card already exists: {path} "
                                       "(edit the file to change it)")
        data = {
            "job_code": card.job_code,
            "dims": {
                "length": card.dims.length, "width": card.dims.width,
                "height": card.dims.height, "unit": card.dims.unit,
            },
        }
        if card.dims_placeholder:
            data["dims_placeholder"] = True
        data.update({
            "complexity": card.complexity,
            "orientation": card.orientation,
            "product_class": card.product_class,
            "part_scope": card.part_scope,
            "reference_dir": str(card.reference_dir),
        })
        header = ""
        if card.dims_placeholder:
            header = (
                "# Created via the web intake form with dims_placeholder: true —\n"
                "# the dims above are STAND-INS, not owner-supplied. The pipeline\n"
                "# runs for structural review but NO deliverable package is emitted\n"
                "# until real dimensions replace them (rule 9: never inferred).\n"
            )
        path.write_text(header + yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return {"job_code": card.job_code, "path": str(path),
                "dims_placeholder": card.dims_placeholder}

    def _package_entry(job_code: str, kind: str, root: Path) -> dict[str, Any] | None:
        report = _qa_report(root, job_code)
        if report is None:
            return None
        gates = report.get("gates")
        return {
            "job_code": job_code,
            "kind": kind,  # "package" | "blocked"
            "refused": bool(report.get("refused")),
            "all_passed": report.get("all_passed"),
            "gates_passed": (sum(1 for g in gates if g.get("passed"))
                             if isinstance(gates, list) else None),
            "gates_total": len(gates) if isinstance(gates, list) else None,
            "package_dir": report.get("package_dir"),
        }

    @app.get("/api/packages")
    def list_packages() -> list[dict[str, Any]]:
        out = []
        for kind, root in (("blocked", app.state.blocked_root),
                           ("package", app.state.packages_root)):
            root = Path(root)
            if not root.is_dir():
                continue
            for d in sorted(root.iterdir()):
                if d.is_dir():
                    entry = _package_entry(d.name, kind, root)
                    if entry:
                        out.append(entry)
        return out

    @app.get("/api/packages/{job_code}")
    def get_package(job_code: str) -> dict[str, Any]:
        for kind, root in (("package", app.state.packages_root),
                           ("blocked", app.state.blocked_root)):
            report = _qa_report(Path(root), job_code)
            if report is not None:
                return {"job_code": job_code, "kind": kind, "report": report}
        raise HTTPException(status_code=404, detail=f"No package for {job_code}")

    @app.post("/api/packages/{job_code}/validate")
    def validate_package(job_code: str) -> dict[str, Any]:
        """Live re-run of the client validator (the local mirror of their
        panel) against a package on disk. One fresh Blender process for the
        mesh facts; without Blender the mesh gates fail closed."""
        pkg = Path(app.state.packages_root) / job_code
        if not pkg.is_dir():
            raise HTTPException(status_code=404, detail=f"No package dir: {pkg}")
        job_path = Path(app.state.jobs_dir) / f"{job_code}.yaml"
        if not job_path.is_file():
            raise HTTPException(status_code=400,
                                detail=f"Job card not found: {job_path}")
        job_card = load_job(job_path)
        if job_card.dims_placeholder:
            raise HTTPException(
                status_code=409,
                detail="REFUSED — this job card carries dims_placeholder: true; "
                       "the owner must supply real dimensions before validation "
                       "(rule 9).")

        facts: MeshFacts | None = None
        fbx = pkg / f"{job_code}.fbx"
        if fbx.is_file():
            blender = locate_blender()
            if blender is not None:
                try:
                    from ..blender.runner import BlenderRunner

                    report = BlenderRunner().execute_op(
                        "topology_report", {"model_path": str(fbx)})
                    facts = MeshFacts.from_topology_report(report)
                except Exception:
                    facts = None
        results = run_all_gates(pkg, job_card, facts)
        return {
            "job_code": job_code,
            "all_passed": all(r.passed for r in results),
            "blender_facts": facts is not None,
            "gates": [r.to_dict() for r in results],
        }


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
