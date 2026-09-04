"""Neural build flow (GLM_PROMPT_NEURAL_INTAKE.md §4.1–§4.4): the full
generate → analyse → conform → deliver chain for one neural-routed job.

Wiring, not invention: every stage is a module that already exists —
img3d multi-view generation (§4.2), analyse (§4.3), conform (§4.4), and
the T3 delivery chain (finish_delivery: build + weld + retopology +
atlas + bake + decimate + FBX + gates + qa_report). This function owns
the ORDER and the evidence: every decision lands in the run manifest,
every stage emits a progress event, and the stop conditions fail loud:

  S1  conform refuses an aspect ratio off beyond tolerance
  S2  intake already refused the job without explicit dims (rule 9)
  S3  the img3d service failed to obtain the machine GPU lock
  S4  vision is NOT called here at all — this flow is measured facts and
      the delivery gates; the advisory VLM gate belongs to the agent loop

The route decision (§4.0.5) is made by the CALLER (the router) and passed
in verbatim — the flow records it, it never re-decides.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..client.job import JobCard
from ..run_store import RunManifest
from .analyse import NeuralAnalyseReport, analyse_neural_mesh
from .conform import ConformRefusal, build_conform_spec, split_packed_maps

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class NeuralBuildResult:
    success: bool
    status: str  # completed | completed_with_warnings | failed | cancelled
    error: str | None = None
    final_glb_path: str | None = None
    package_dir: str | None = None
    all_gates_passed: bool | None = None
    manifest_path: Path | None = None
    analyse: dict | None = None
    conform: dict | None = None
    steps: list[dict] = field(default_factory=list)


def _write_manifest(
    run_dir: Path,
    *,
    job_card: JobCard,
    route_decision: dict,
    started: float,
    status: str,
    analyse: dict | None,
    conform: dict | None,
    package_report: dict | None,
    error: str | None,
    glb_path: Path | None,
) -> Path:
    manifest = RunManifest(
        run_id=run_dir.name,
        created_at=started,
        model_name=f"{job_card.job_code} neural build",
        spec_path=str((run_dir / "spec.json").resolve())
        if (run_dir / "spec.json").exists() else None,
        final_glb_path=str(glb_path.resolve()) if glb_path else None,
        renders={},
        dimension_gate_passed=bool(package_report and package_report.get("all_passed")),
        mesh_gate_passed=bool(package_report and package_report.get("all_passed")),
        tri_count=int((analyse or {}).get("triangles") or 0),
        vertex_count=int((analyse or {}).get("vertices") or 0),
        dimensions_m=list((analyse or {}).get("extents_m") or []),
        metrics={
            "wall_clock_s": round(time.time() - started, 1),
            "unresolved_error": error,
            # §4.0.5: which path built this asset and why — every time
            "route": route_decision,
            "analyse": analyse,
            "conform": conform,
            "package": (
                {
                    "package_dir": package_report.get("package_dir"),
                    "all_passed": package_report.get("all_passed"),
                    "gates": package_report.get("gates"),
                }
                if package_report
                else None
            ),
            "job_code": job_card.job_code,
        },
        status=status,
    )
    path = run_dir / "manifest.json"
    import json
    from dataclasses import asdict

    path.write_text(json.dumps(asdict(manifest), indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path


def run_neural_build(
    *,
    views: dict[str, str | Path],
    job_card: JobCard,
    route_decision: dict,
    run_dir: str | Path,
    progress: Callable[[dict], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    declared_fabric: bool = False,
    max_tris: int = 50000,
    out_root: Path | str | None = None,
    resolution: int | None = None,
    bake_device: str = "auto",
    bake_timeout_sec: float = 3600.0,
    img3d_provider: Any = None,
) -> NeuralBuildResult:
    """Generate → analyse → conform → deliver one neural job. Blocking; the
    webapp runs it in a worker thread. `img3d_provider` is injectable for
    tests (defaults to the configured RemoteImg3DProvider)."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "renders").mkdir(exist_ok=True)
    (run_dir / "steps").mkdir(exist_ok=True)
    started = time.time()
    steps: list[dict] = []

    def emit(event: str, **data) -> None:
        if progress is not None:
            try:
                progress({"event": event, "run_id": run_dir.name,
                          "ts": time.time(), **data})
            except Exception:
                pass

    def cancelled() -> bool:
        return bool(cancel) and cancel()

    def finish(
        status: str,
        error: str | None,
        glb_path: Path | None,
        analyse: dict | None,
        conform: dict | None,
        package_report: dict | None,
    ) -> NeuralBuildResult:
        manifest_path = _write_manifest(
            run_dir, job_card=job_card, route_decision=route_decision,
            started=started, status=status, analyse=analyse, conform=conform,
            package_report=package_report, error=error, glb_path=glb_path,
        )
        success = status == "completed"
        # final_glb stays absolute (manifest-consistent); final_glb_rel is the
        # run-relative form the web viewer fetches via /api/runs/<id>/file/<rel>
        glb_rel = str(glb_path.relative_to(run_dir).as_posix()) if glb_path and (
            glb_path is not None and run_dir in glb_path.parents) else None
        emit(
            "run_finished",
            success=success,
            status=status,
            model_name=f"{job_card.job_code} neural build",
            final_glb=str(glb_path) if glb_path else None,
            final_glb_rel=glb_rel,
            error=error,
            package_dir=package_report.get("package_dir") if package_report else None,
            wall_clock_s=round(time.time() - started, 1),
            route=route_decision,
        )
        return NeuralBuildResult(
            success=success,
            status=status,
            error=error,
            final_glb_path=str(glb_path) if glb_path else None,
            package_dir=package_report.get("package_dir") if package_report else None,
            all_gates_passed=package_report.get("all_passed") if package_report else None,
            manifest_path=manifest_path,
            analyse=analyse,
            conform=conform,
            steps=steps,
        )

    emit("run_started", mode="neural", run_dir=str(run_dir),
         views=sorted(views), job_code=job_card.job_code)
    emit("route_decided", route=route_decision.get("route"),
         reason=route_decision.get("reason"),
         forced=route_decision.get("forced", False))

    # ── 1. Generate (§4.2): the img3d service, multi-view ───────────────────
    emit("neural_generation_started", views=sorted(views), max_tris=max_tris)
    provider = img3d_provider
    if provider is None:
        from ..img3d.client import get_img3d_provider

        provider = get_img3d_provider()
    if provider is None:
        return finish("failed",
                      "img3d is disabled (config/hardware.yaml img3d.enabled) — "
                      "the neural route cannot run", None, None, None, None)
    if not provider.is_available():
        return finish(
            "failed",
            f"img3d service unreachable at {provider.base_url} — start it with "
            "scripts/start-img3d.ps1 comfy_trellis2 (S3-class: the GPU service "
            "must be up before a neural run)",
            None, None, None, None)

    gen = provider.generate_mesh_from_views(views, run_dir / "neural",
                                             max_tris=max_tris)
    steps.append({"step": "generate", "duration_sec": round(gen.duration_sec, 1),
                  "tri_count": gen.tri_count})
    if cancelled():
        return finish("cancelled", "Cancelled by user", None, None, None, None)
    if not gen.success or not gen.output_glb_path:
        return finish("failed", f"neural generation failed: {gen.error}",
                      None, None, None, None)
    glb_path = Path(gen.output_glb_path)
    emit("neural_generation_done", glb=str(glb_path), tri_count=gen.tri_count,
         duration_sec=round(gen.duration_sec, 1))

    # ── 2. Analyse (§4.3): measured facts, gates before eyes ────────────────
    try:
        report: NeuralAnalyseReport = analyse_neural_mesh(
            glb_path, job_card, declared_fabric=declared_fabric)
    except Exception as e:
        return finish("failed", f"analyse failed: {e}", glb_path, None, None, None)
    analyse_dict = report.to_dict()
    emit("analyse_done", passed=report.passed,
         failed=[c["name"] for c in report.failed_checks()],
         triangles=report.triangles, bodies=report.bodies,
         metallic=report.metallic,
         maps={k: v["present"] for k, v in report.maps.items()})
    if not report.passed:
        failed = "; ".join(
            f"{c['name']}: {c['note']}" for c in report.failed_checks())
        return finish("failed", f"analyse gates failed — {failed}",
                      glb_path, analyse_dict, None, None)

    # ── 3. Conform (§4.4): S1 refusal, split maps, author the spec ──────────
    try:
        maps_written = split_packed_maps(glb_path, run_dir / "maps")
        spec, conform_decisions = build_conform_spec(
            glb_path, job_card, analyse_report=report,
            maps_dir=run_dir / "maps" if maps_written else None,
            declared_fabric=declared_fabric)
    except ConformRefusal as e:
        emit("conform_refused", reason=str(e))
        return finish("failed", f"conform REFUSED (S1): {e}", glb_path,
                      analyse_dict, {"refused": str(e)}, None)
    conform_decisions["maps_written"] = sorted(maps_written)
    spec_path = run_dir / "spec.json"
    spec_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    emit("conform_done", spec=str(spec_path),
         retopology=conform_decisions.get("retopology"),
         maps=sorted(maps_written))

    if cancelled():
        return finish("cancelled", "Cancelled by user", glb_path,
                      analyse_dict, conform_decisions, None)

    # ── 4. Deliver: the T3 chain (build + bake + gates + qa_report) ─────────
    from ..client.package import finish_delivery

    emit("package_started")
    package_report: dict = {}
    try:
        package_report = finish_delivery(
            job_card, spec,
            out_root=out_root or PROJECT_ROOT / "output" / "packages",
            log=lambda msg: emit("package_log", message=msg),
            resolution=resolution,
            bake_device=bake_device,
            bake_timeout_sec=bake_timeout_sec,
        )
    except Exception as e:
        return finish("failed", f"delivery failed: {e}", glb_path,
                      analyse_dict, conform_decisions, None)
    all_passed = bool(package_report.get("all_passed"))
    emit("package_done", all_passed=all_passed,
         package_dir=package_report.get("package_dir"),
         gates=package_report.get("gates"))
    return finish(
        "completed" if all_passed else "completed_with_warnings",
        None if all_passed else "delivery gates failed — see qa_report.json",
        glb_path, analyse_dict, conform_decisions, package_report)
