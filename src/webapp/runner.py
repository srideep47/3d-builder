"""Run registry — executes agent runs in worker threads and fans progress
events out to WebSocket subscribers.

The AgentLoop runs blocking in a thread; each WebSocket connection owns an
asyncio.Queue, and events cross the thread boundary via
loop.call_soon_threadsafe(queue.put_nowait, event)."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..pipeline import ThreeDBuilderPipeline
from ..run_store import RunStore
from ..spec.schema import ObjectSpec

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class _SimpleRunResult:
    """Minimal result shape `_launch`'s worker reads (success/error/
    final_glb_path/manifest_path) for deterministic non-loop flows."""
    success: bool
    error: str | None
    final_glb_path: str | None
    manifest_path: Path | None


@dataclass
class ActiveRun:
    run_id: str
    run_dir: Path
    mode: str  # "ai" | "spec"
    label: str
    status: str = "running"  # running | completed | completed_with_warnings | failed | cancelled | budget_exhausted | iteration_cap_exhausted
    started: float = field(default_factory=time.time)
    events: list[dict[str, Any]] = field(default_factory=list)
    subscribers: list[tuple[Any, Any]] = field(default_factory=list)  # (event_loop, queue)
    cancel_flag: threading.Event = field(default_factory=threading.Event)
    error: str | None = None
    final_glb: str | None = None


class RunRegistry:
    def __init__(self, pipeline: ThreeDBuilderPipeline | None = None, run_store: RunStore | None = None):
        self.pipeline = pipeline or ThreeDBuilderPipeline()
        self.run_store = run_store or RunStore()
        self.runs: dict[str, ActiveRun] = {}
        self._lock = threading.Lock()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start_ai(
        self,
        prompt: str,
        measurements: str = "",
        material_preset: str | None = None,
        images: list[str] | None = None,
        job_card=None,
        route_decision: dict | None = None,
    ) -> str:
        run_dir = self.run_store.create_run("ui_ai")

        def factory(progress: Callable, cancel: Callable) -> Any:
            return self.pipeline.generate_from_prompt(
                prompt=prompt,
                measurements=measurements,
                material_preset=material_preset,
                images=images,
                run_name="ui_ai",
                run_dir=run_dir,
                progress=progress,
                cancel=cancel,
                job_card=job_card,
                route_decision=route_decision,
            )

        run = ActiveRun(run_id=run_dir.name, run_dir=run_dir, mode="ai", label=prompt[:80])
        return self._launch(run, factory)

    def start_spec(self, spec: ObjectSpec, job_card=None, route_decision: dict | None = None) -> str:
        run_dir = self.run_store.create_run("ui_spec")

        def factory(progress: Callable, cancel: Callable) -> Any:
            return self.pipeline.generate_from_spec(
                spec_source=spec,
                run_name="ui_spec",
                run_dir=run_dir,
                progress=progress,
                cancel=cancel,
                job_card=job_card,
                route_decision=route_decision,
            )

        run = ActiveRun(run_id=run_dir.name, run_dir=run_dir, mode="spec", label=spec.name)
        return self._launch(run, factory)

    def start_template(
        self,
        job_card,
        template_path: str | Path,
        route_decision: dict | None = None,
        resolution: int | None = None,
        bake_device: str = "auto",
        out_root: Path | str | None = None,
    ) -> str:
        """Template route (§4.0.5 route 1): compile templates/<class>.yaml
        with the card's owner-supplied dimensions, then the deterministic
        T3 delivery chain. No analyst, no neural call."""
        run_dir = self.run_store.create_run("ui_template")
        started = time.time()

        def factory(progress: Callable, cancel: Callable) -> Any:
            def emit(event: str, **data) -> None:
                progress({"event": event, "run_id": run_dir.name,
                          "ts": time.time(), **data})

            emit("run_started", mode="template", run_dir=str(run_dir),
                 job_code=job_card.job_code, template=str(template_path))
            if route_decision is not None:
                emit("route_decided", route=route_decision.get("route"),
                     reason=route_decision.get("reason"),
                     forced=route_decision.get("forced", False))
            emit("template_compiling", template=str(template_path))
            from ..client.package import finish_delivery
            from ..spec.template import compile_spec, load_template

            tpl = load_template(Path(template_path))
            spec, warnings = compile_spec(tpl, job_card)
            for w in warnings:
                emit("template_warning", warning=w)
            spec_path = run_dir / "spec.json"
            spec_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
            emit("template_compiled", spec=str(spec_path))

            report = finish_delivery(
                job_card, spec,
                out_root=out_root or PROJECT_ROOT / "output" / "packages",
                log=lambda msg: emit("package_log", message=msg),
                resolution=resolution,
                bake_device=bake_device,
                bake_timeout_sec=3600.0,
            )
            all_passed = bool(report.get("all_passed"))
            manifest = {
                "run_id": run_dir.name,
                "created_at": started,
                "model_name": f"{job_card.job_code} template build",
                "spec_path": str(spec_path.resolve()),
                "final_glb_path": report.get("lp_glb"),
                "renders": {},
                "dimension_gate_passed": all_passed,
                "mesh_gate_passed": all_passed,
                "tri_count": 0,
                "vertex_count": 0,
                "dimensions_m": [],
                "metrics": {
                    "wall_clock_s": round(time.time() - started, 1),
                    "route": route_decision,  # §4.0.5: recorded every time
                    "job_code": job_card.job_code,
                    "template": str(template_path),
                    "package": {
                        "package_dir": report.get("package_dir"),
                        "all_passed": all_passed,
                    },
                },
                "status": "completed" if all_passed else "completed_with_warnings",
            }
            manifest_path = run_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
            emit("run_finished", success=all_passed,
                 status=manifest["status"],
                 model_name=manifest["model_name"],
                 package_dir=report.get("package_dir"),
                 route=route_decision,
                 wall_clock_s=manifest["metrics"]["wall_clock_s"])
            return _SimpleRunResult(
                success=all_passed,
                error=None if all_passed else "delivery gates failed — see qa_report.json",
                final_glb_path=report.get("lp_glb"),
                manifest_path=manifest_path,
            )

        run = ActiveRun(run_id=run_dir.name, run_dir=run_dir, mode="template",
                        label=f"{job_card.job_code} template {Path(template_path).name}")
        return self._launch(run, factory)

    def start_neural(
        self,
        views: dict[str, str],
        job_card,
        route_decision: dict,
        declared_fabric: bool = False,
        max_tris: int = 50000,
        resolution: int | None = None,
        bake_device: str = "auto",
    ) -> str:
        """Neural route (§4.0.5 route 3): the §4.1–§4.4 chain — generate
        (img3d multi-view) → analyse (measured gates) → conform (S1
        refusal + spec) → finish_delivery. Blocking in the worker thread."""
        run_dir = self.run_store.create_run("ui_neural")

        def factory(progress: Callable, cancel: Callable) -> Any:
            from ..neural.flow import run_neural_build

            return run_neural_build(
                views=views,
                job_card=job_card,
                route_decision=route_decision,
                run_dir=run_dir,
                progress=progress,
                cancel=cancel,
                declared_fabric=declared_fabric,
                max_tris=max_tris,
                resolution=resolution,
                bake_device=bake_device,
            )

        run = ActiveRun(run_id=run_dir.name, run_dir=run_dir, mode="neural",
                        label=f"{job_card.job_code} neural")
        return self._launch(run, factory)

    def _launch(self, run: ActiveRun, factory: Callable[[Callable, Callable], Any]) -> str:
        with self._lock:
            self.runs[run.run_id] = run

        def progress(ev: dict[str, Any]) -> None:
            self._publish(run, ev)

        def work() -> None:
            try:
                result = factory(progress, run.cancel_flag.is_set)
                run.status = _manifest_status(result)
                run.error = result.error
                run.final_glb = str(result.final_glb_path) if result.final_glb_path else None
                if run.cancel_flag.is_set() and not result.success:
                    run.status = "cancelled"
            except Exception as e:
                run.status = "failed"
                run.error = str(e)
                self._publish(run, {"event": "run_error", "run_id": run.run_id, "ts": time.time(), "error": str(e)[:2000]})

        threading.Thread(target=work, daemon=True, name=f"run-{run.run_id}").start()
        return run.run_id

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            run = self.runs.get(run_id)
        if run is None:
            return False
        run.cancel_flag.set()
        self._publish(run, {"event": "cancel_requested", "run_id": run_id, "ts": time.time()})
        return True

    # ── events ───────────────────────────────────────────────────────────────

    def _publish(self, run: ActiveRun, ev: dict[str, Any]) -> None:
        with self._lock:
            run.events.append(ev)
            subscribers = list(run.subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, ev)
            except Exception:
                pass

    def history(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            run = self.runs.get(run_id)
            return list(run.events) if run else []

    def subscribe(self, run_id: str, loop: Any, queue: Any) -> bool:
        """Register a subscriber queue. Returns True while the run is live."""
        with self._lock:
            run = self.runs.get(run_id)
            if run is None:
                return False
            run.subscribers.append((loop, queue))
            return run.status == "running"

    def unsubscribe(self, run_id: str, loop: Any, queue: Any) -> None:
        with self._lock:
            run = self.runs.get(run_id)
            if run is not None:
                try:
                    run.subscribers.remove((loop, queue))
                except ValueError:
                    pass

    def get(self, run_id: str) -> ActiveRun | None:
        with self._lock:
            return self.runs.get(run_id)

    def active_runs(self) -> list[dict[str, Any]]:
        """All runs this server session has touched (not only running ones);
        `live` reflects the current status."""
        with self._lock:
            return [
                {
                    "run_id": r.run_id,
                    "mode": r.mode,
                    "label": r.label,
                    "status": r.status,
                    "started": r.started,
                    "live": r.status == "running",
                }
                for r in self.runs.values()
            ]


def _manifest_status(result: Any) -> str:
    try:
        manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
        return manifest.get("status", "completed")
    except Exception:
        return "completed_with_warnings" if result is not None and not result.success else "completed"
