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


@dataclass
class ActiveRun:
    run_id: str
    run_dir: Path
    mode: str  # "ai" | "spec"
    label: str
    status: str = "running"  # running | completed | completed_with_warnings | failed | cancelled | budget_exhausted
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
            )

        run = ActiveRun(run_id=run_dir.name, run_dir=run_dir, mode="ai", label=prompt[:80])
        return self._launch(run, factory)

    def start_spec(self, spec: ObjectSpec) -> str:
        run_dir = self.run_store.create_run("ui_spec")

        def factory(progress: Callable, cancel: Callable) -> Any:
            return self.pipeline.generate_from_spec(
                spec_source=spec,
                run_name="ui_spec",
                run_dir=run_dir,
                progress=progress,
                cancel=cancel,
            )

        run = ActiveRun(run_id=run_dir.name, run_dir=run_dir, mode="spec", label=spec.name)
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
