"""Assemble the client delivery package (§4.1) + qa_report.json audit record.

Orchestration lives here, never in the harness (rule 10); no product nouns
(rule 11) — this module knows file names, formats and the job card only.

Two entry points:
- ``package_delivery`` (T2): assemble from an already-verified source GLB.
  LP/HP/textures are PLACEHOLDERS until a real bake — visible, never silent
  (qa_report ``placeholders`` block + per-file ``placeholder`` flags).
- ``finish_delivery`` (T3): the full-quality chain — build → quad-verify +
  UV atlas → bake the real 5-map texture set from a high-poly detail shell →
  decimate the LP to the tier budget → export the deliverable FBX from the
  LIVE QUAD-CLEAN SCENE (owner decision, not the triangulated GLB) →
  assemble + audit. Bake stats and UV diagnostics travel in qa_report.json
  as mechanical evidence (the operator is text-only: numbers, not eyeballs).

qa_report.json is a complete audit record (owner amendment 4): job card as
loaded, every gate result with expected/received, the axis convention
actually written (independently parsed from the FBX header — not Blender's
word for it), tool versions, file hashes and sizes, cross-checks between the
harness and the independent FBX parse, and every outstanding open question.
If a delivery is disputed, this file is the evidence.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from .contract import OPEN_QUESTIONS
from .fbx_inspect import read_fbx_info
from .gates import MeshFacts, run_all_gates
from .job import JobCard

# Synthetic flat fills for the placeholder texture set (T3 replaces these).
# Normal must be the OpenGL neutral (128,128,255); the rest are neutral PBR.
_PLACEHOLDER_TEXTURES: dict[str, tuple[int, int, int]] = {
    "_BaseColor.png": (200, 200, 200),
    "_Normal.png": (128, 128, 255),
    "_Roughness.png": (128, 128, 128),
    "_Metallic.png": (0, 0, 0),
    "_AO.png": (255, 255, 255),
}

# The axis convention we ask the exporter for. The FBX-standard Y-up is what
# third-party consumers expect; qa_report records what was ACTUALLY written
# (independently parsed) next to this request. A job card may override the
# pair (Phase 4: fbx_axis_up/fbx_axis_forward — set together, never half).
FBX_AXIS_UP = "Y"
FBX_AXIS_FORWARD = "-Z"


def _fbx_axes(job: JobCard) -> tuple[str, str]:
    """(axis_up, axis_forward) requested from the exporter: card override >
    the FBX-standard Y-up default (open question 'fbx-axis-convention')."""
    if job.fbx_axis_up is not None:
        return job.fbx_axis_up, job.fbx_axis_forward or FBX_AXIS_FORWARD
    return FBX_AXIS_UP, FBX_AXIS_FORWARD


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def usdz_structure_report(path: Path) -> dict[str, Any]:
    """Structural (non-Blender) check of a USDZ: valid zip, uncompressed
    entries, one default layer. Presence-only gate + audit evidence."""
    report: dict[str, Any] = {"path": path.name, "exists": path.is_file()}
    if not path.is_file():
        return report
    report["size_bytes"] = path.stat().st_size
    try:
        with zipfile.ZipFile(path) as zf:
            members = zf.infolist()
            report["members"] = [m.filename for m in members]
            report["compressed"] = any(m.compress_type != zipfile.ZIP_STORED for m in members)
            report["has_layer"] = any(m.filename.endswith((".usd", ".usda", ".usdc")) for m in members)
    except zipfile.BadZipFile as e:
        report["error"] = f"not a valid zip: {e}"
    return report


def _write_placeholder_textures(package_dir: Path, job_code: str) -> None:
    for suffix, color in _PLACEHOLDER_TEXTURES.items():
        path = package_dir / (job_code + suffix)
        Image.new("RGB", (64, 64), color).save(path, format="PNG")


def _assemble_and_audit(
    job: JobCard,
    package_dir: Path,
    runner,
    log: Callable[[str], None],
    files: list[dict[str, Any]],
    fbx_path: Path,
    usdz_path: Path,
    usdz_method: str | None,
    usdz_direct_error: str | None,
    source_note: str,
    extra_sections: dict[str, Any] | None = None,
    placeholders: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shared tail of both delivery paths: gates, the independent FBX parse,
    cross-checks, qa_report.json. ``files`` is the already-recorded manifest
    (name/size/sha256/placeholder/note per deliverable)."""
    topology = runner.execute_op("topology_report", {"model_path": str(fbx_path)})
    facts = MeshFacts.from_topology_report(topology)
    results = run_all_gates(package_dir, job, facts)

    # Independent, non-Blender verification of what was actually written
    # (owner amendment 1). A Blender re-import is self-consistent even when
    # the file is wrong for a third party — this parse is the third party.
    fbx_info = read_fbx_info(fbx_path)

    # Cross-checks: harness vs the independent FBX parse. The independent
    # parse resolves world space in the FILE's declared axis system; the
    # harness re-import reports Blender Z-up. Extents are compared as a
    # sorted multiset (axis-direction proof lives in the chiral export test,
    # which owns the signed-permutation machinery).
    harness_extents = sorted((round(facts.extent_m(a), 6) for a in "xyz"), reverse=True)
    independent_extents = [round(e, 6) for e in fbx_info.world_extents_m()]
    cross_checks = {
        "ngon_count_harness": facts.ngon_count,
        "ngon_count_independent_parse": fbx_info.ngon_count(),
        "faces_total_harness": facts.faces_total,
        "faces_total_independent_parse": fbx_info.faces_total(),
        "triangle_equivalent_harness": facts.triangle_equivalent,
        "triangle_equivalent_independent_parse": fbx_info.triangle_equivalent(),
        "world_extents_harness_m": harness_extents,
        "world_extents_independent_parse_m": independent_extents,
    }
    cross_checks["agree"] = (
        cross_checks["ngon_count_harness"] == cross_checks["ngon_count_independent_parse"]
        and cross_checks["triangle_equivalent_harness"] == cross_checks["triangle_equivalent_independent_parse"]
        and len(independent_extents) == 3
        and all(abs(a - b) <= 1e-3
                for a, b in zip(independent_extents, harness_extents))
    )

    info_res = runner.execute_op("info", {})
    # Phase 4: per-file required flag against the card's effective deliverable
    # set, and the override note when the owner's prompt changed the contract.
    required_names = {job.job_code + s for s in job.effective_required_suffixes()}
    for entry in files:
        entry["required"] = entry["name"] in required_names
    contract_note = None
    if job.required_formats is not None:
        contract_note = {
            "required_suffixes": job.effective_required_suffixes(),
            "note": (
                "The owner's prompt overrode the required deliverable set. The "
                "gates enforce exactly this list; the finishing chain still "
                "emits the standard superset (a partial chain degrades the "
                "FBX — its materials come from the bake), so files marked "
                "required: false are extras, not contract violations."
            ),
        }
    report = {
        "schema": "threed-qa-report/1",
        "job_code": job.job_code,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "package_dir": str(package_dir),
        "source": source_note,
        "all_passed": all(r.passed for r in results),
        "job_card": job.model_dump(mode="json"),
        "gates": [r.to_dict() for r in results],
        "axis_convention": {
            "requested": dict(zip(("axis_up", "axis_forward"), _fbx_axes(job))),
            "written": fbx_info.axes.to_dict(),
            "fbx_version": fbx_info.version,
            "creator": fbx_info.creator,
            "verified_by": "independent binary-FBX header parse (src/client/fbx_inspect.py), not a Blender round trip",
        },
        "usdz": {
            "method": usdz_method,
            "direct_error": usdz_direct_error,
            "structure": usdz_structure_report(usdz_path),
        },
        "cross_checks": cross_checks,
        "tools": {
            "blender": info_res.get("blender_version"),
            "fbx_exporter": "Blender built-in binary FBX writer (Blender writes FBX 7.4, readable by the FBX 2020 SDK)",
            "usd_exporter": "Blender built-in USD exporter (wm.usd_export)",
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "files": files,
        "open_questions": [dict(q) for q in OPEN_QUESTIONS],
    }
    if placeholders is not None:
        report["placeholders"] = placeholders
    if contract_note is not None:
        report["contract_note"] = contract_note
    if extra_sections:
        report.update(extra_sections)

    report_path = package_dir / "qa_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log(f"qa_report.json written: {report_path}")
    return report


class PlaceholderDimensionsError(RuntimeError):
    """Raised when a job carries `dims_placeholder: true` — the owner has not
    supplied real dimensions, so NO deliverable package may be emitted
    (GLM_BRIEF rule 9: dimensions are never inferred)."""


def package_delivery(
    job: JobCard,
    source_glb: Path,
    out_root: Path | str = Path("output/packages"),
    runner=None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Assemble output/packages/<JOB>/ per §4.1, run every gate, write
    qa_report.json. Returns the report dict. Raises loudly on export or
    parse failures — a package that cannot be audited must not ship."""
    log = log or (lambda msg: None)
    if job.dims_placeholder:
        raise PlaceholderDimensionsError(
            f"REFUSED — job {job.job_code} carries dims_placeholder: true "
            f"(dimensions {job.dims.length} x {job.dims.width} x "
            f"{job.dims.height} {job.dims.unit} are PLACEHOLDER stand-ins). "
            "No deliverable package is emitted until the owner supplies real "
            "dimensions (rule 9: never inferred)."
        )
    source_glb = Path(source_glb)
    if not source_glb.is_file():
        raise FileNotFoundError(f"source GLB not found: {source_glb}")
    if runner is None:
        from ..blender.runner import BlenderRunner

        runner = BlenderRunner()

    # Absolute from here down (see finish_delivery: relative paths make the
    # harness's Blender subprocess resolve image outputs blend-relative).
    package_dir = Path(out_root).resolve() / job.job_code
    package_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []

    def _record(path: Path, placeholder: bool = False, note: str = "") -> None:
        files.append({
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "placeholder": placeholder,
            "note": note,
        })

    # ── Model deliverables ──────────────────────────────────────────────────
    fbx_path = package_dir / f"{job.job_code}.fbx"
    fbx_res = runner.execute_op("export_fbx", {
        "input": str(source_glb),
        "path": str(fbx_path),
        "axis_up": _fbx_axes(job)[0],
        "axis_forward": _fbx_axes(job)[1],
    })
    _record(fbx_path, note="binary FBX exported from the source GLB "
                           "(triangulated: glTF stores triangles only)")

    usdz_path = package_dir / f"{job.job_code}_LP.usdz"
    usdz_res = runner.execute_op("export_usdz", {
        "input": str(source_glb),
        "path": str(usdz_path),
    })
    _record(usdz_path, note=f"export method: {usdz_res.get('method', '?')}")

    # ── PLACEHOLDERS until T3 (owner amendment 3) — logged, never silent ────
    log(f"PLACEHOLDER: LP and HP GLBs are byte-identical copies of the source "
        f"until T3 builds the real high/low-poly split ({job.job_code})")
    for suffix in ("_LP.glb", "_HP.glb"):
        glb_copy = package_dir / (job.job_code + suffix)
        glb_copy.write_bytes(source_glb.read_bytes())
        _record(glb_copy, placeholder=True,
                note="placeholder: identical to source GLB until T3 (HP/LP bake)")
    log(f"PLACEHOLDER: texture PNGs are synthetic flat fills until T3 bakes "
        f"real maps ({job.job_code})")
    _write_placeholder_textures(package_dir, job.job_code)
    for suffix in _PLACEHOLDER_TEXTURES:
        _record(package_dir / (job.job_code + suffix), placeholder=True,
                note="placeholder: synthetic flat fill until T3 (bake)")

    return _assemble_and_audit(
        job, package_dir, runner, log, files, fbx_path, usdz_path,
        usdz_method=usdz_res.get("method"),
        usdz_direct_error=usdz_res.get("direct_error") or None,
        source_note=str(source_glb),
        placeholders={
            "lp_hp_single_source": True,
            "textures_synthetic": True,
            "detail": "T3 (UV + HP/LP bake) not yet built: the LP and HP GLBs are "
                      "byte-identical copies of the source, and the texture PNGs are "
                      "synthetic flat fills. Replace before any real delivery.",
        },
    )


# Baked map name -> client deliverable suffix (contract §4.1 texture set).
_BAKE_MAP_FILES: dict[str, str] = {
    "basecolor": "_BaseColor.png",
    "normal": "_Normal.png",
    "roughness": "_Roughness.png",
    "metallic": "_Metallic.png",
    "ao": "_AO.png",
}


def _collect_render_metrics(
    views: dict, probes: list[dict], log: Callable[[str], None]
) -> dict[str, Any] | None:
    """Measure the rendered review views (Phase 8 item 2 — the §H fix).

    view_stats (balance + clipping) on every rendered view, plus the
    template's absolute-contrast probes (grey-level amplitude at the
    authored relief pitch — never a ratio). Probes fail CLOSED (an
    unmeasurable region reports valid=False + passed=False + reason, never
    a silent pass), but a probe failure is loud recorded evidence for the
    owner/reviewer, NOT a delivery refusal — the six client gates own
    refusal. Returns None when there is nothing to measure.
    """
    if not views and not probes:
        return None
    from ..render.metrics import measure_contrast_probe, view_stats

    view_results = {name: view_stats(path) for name, path in sorted(views.items())}
    probe_results: list[dict[str, Any]] = []
    for p in probes:
        path = views.get(p["view"])
        if path is None:
            r: dict[str, Any] = {"valid": False, "passed": False,
                                 "reason": f"view {p['view']!r} was not rendered"}
        else:
            r = measure_contrast_probe(
                path, tuple(p["region"]), tuple(p["cycles"]),
                band=tuple(p["band"]), min_amplitude=p["min_amplitude"],
                axes=p["axes"])
        probe_results.append({"name": p["name"], "view": p["view"], **r})
        if not r.get("valid"):
            log(f"contrast probe {p['name']} INVALID ({p['view']} view): "
                f"{r.get('reason', '?')} — recorded, not a delivery refusal")
        elif r.get("passed"):
            log(f"contrast probe {p['name']} PASS ({p['view']} view): "
                f"amplitude x {r['amplitude_x']} / y {r['amplitude_y']} grey "
                f"levels >= floor {p['min_amplitude']} (axes={p['axes']}, "
                f"detected cycles x {r['detected_cycles_x']} / "
                f"y {r['detected_cycles_y']})")
        else:
            log(f"contrast probe {p['name']} FAIL ({p['view']} view): "
                f"amplitude {r.get('amplitude')} grey levels < floor "
                f"{p['min_amplitude']} (x {r.get('amplitude_x')}, "
                f"y {r.get('amplitude_y')}) — flat relief, the rig or the "
                f"geometry lost the quilt")
    return {"views": view_results, "probes": probe_results}


def finish_delivery(
    job: JobCard,
    spec,
    out_root: Path | str = Path("output/packages"),
    runner=None,
    log: Callable[[str], None] | None = None,
    work_dir: Path | str | None = None,
    resolution: int | None = 1024,
    review_renders: bool = True,
    bake_timeout_sec: float = 300.0,
    bake_device: str = "auto",
) -> dict[str, Any]:
    """T3 full-quality finishing chain for a verified spec.

    prepare (quad-verify + UV atlas) → bake the real 5-map set from a
    high-poly detail shell → decimate the LP to the tier budget → export the
    deliverable FBX + USDZ → assemble the package + qa_report.json.

    ``resolution``: bake/atlas resolution in px. None (the CLI default) lets
    the job card's `texture_resolution` drive it (owner prompt, Phase 4);
    1024 when neither is set. 4K bakes need a larger bake_timeout_sec.

    ``bake_timeout_sec``: the subprocess timeout for the bake step. The
    300 s default is fine at 1K, but bake time scales ~with texel count —
    a 4K bake needs ~16x (the first 4K attempt silently died at 300 s
    after baking only the AO pass; see the round-2 session log). Pass a
    larger value (3600 is comfortable for 4K on this hardware) whenever
    ``resolution`` > 1024.

    ``bake_device``: Cycles compute device for the bake — "auto" (default;
    OptiX → CUDA → HIP → ONEAPI → METAL, CPU when none is present), a
    specific type ("optix", "cuda", ...), or "cpu". The device actually
    used is recorded in qa_report.json under ``finish.bake_device_resolved``
    (requested type, GPU/CPU, enabled device names, fallback reason) —
    a GPU bake must be a recorded fact, never an assumption.

    Owner decision (recorded in PROGRESS.md): the deliverable FBX is exported
    from the LIVE QUAD-CLEAN SCENE (scene.blend), not the triangulated GLB —
    the client n-gon gate is only meaningful on a non-triangulated mesh,
    triangulating would double the polycount against the tier ceiling, and
    their human QA judges artist topology. The LP GLB is the decimated
    export; the HP GLB is the pre-deletion detail shell.

    Mechanical evidence (bake stats, UV diagnostics, HP/LP tri counts,
    per-step wall clocks under ``finish.step_timings_sec`` — the §7
    throughput budgets are only checkable with numbers) travels in
    qa_report.json under ``finish`` — the operator is text-only:
    numbers, not eyeballs. Returns the report dict; raises loudly on any
    step failure (a package that cannot be audited must not ship).
    """
    log = log or (lambda msg: None)
    if runner is None:
        from ..blender.runner import BlenderRunner

        runner = BlenderRunner()

    from ..spec.resolver import resolve_spec_to_build_params

    # Phase 4: resolution precedence — explicit argument > the card's
    # texture_resolution (owner prompt) > 1024. Callers that want the card to
    # drive the bake pass resolution=None (the CLI's --res default).
    resolution = resolution if resolution is not None else job.effective_texture_resolution()

    # Absolute from here down: the harness runs Blender in a subprocess whose
    # image-path resolution is blend-relative — a relative out_root makes
    # bakes silently write nowhere (empirical, see _save_map in the harness).
    # `out_root` is the PACKAGES root (same contract as package_delivery):
    # the package lands at out_root/<JOB>; work/review artifacts at the
    # sibling "finish" dir, refusal evidence at the sibling "blocked" dir.
    out_root = Path(out_root).resolve()
    work = Path(work_dir).resolve() if work_dir else out_root.parent / "finish" / job.job_code
    maps_dir = work / "maps"
    scene_blend = work / "scene.blend"
    hp_glb = work / "hp.glb"
    lp_glb = work / "lp.glb"
    review_dir = work / "review"

    def _require(res: dict[str, Any], step: str) -> dict[str, Any]:
        if not res.get("success"):
            raise RuntimeError(f"finish_delivery step {step!r} failed: {res.get('error')}")
        return res

    # Per-step wall clocks (PLAN_AUTONOMOUS §7 states budgets; qa_report
    # records reality so any budget miss is a measured fact, not a feeling).
    timings: dict[str, float] = {}
    _t_chain_start = time.perf_counter()

    def _timed(step: str, fn):
        _t0 = time.perf_counter()
        try:
            return fn()
        finally:
            timings[step] = round(time.perf_counter() - _t0, 3)

    # ── 1. prepare: build, verify quads, UV atlas, save the live scene ──────
    build_params = resolve_spec_to_build_params(spec)
    prep = _timed("prepare_scene", lambda: _require(runner.execute_op("prepare_delivery_scene", {
        "build": build_params,
        "out_blend": str(scene_blend),
    }), "prepare_delivery_scene"))
    log(f"prepared quad-clean scene: {scene_blend} "
        f"(atlas pack_scale {prep.get('uv_atlas', {}).get('pack_scale', '?')})")

    # ── 2. bake: real 5-map texture set from the HP detail shell ────────────
    detail_map = {p["name"]: p["detail"] for p in build_params["spec"]["parts"]
                  if p.get("detail")}
    bake = _timed("bake_maps", lambda: _require(runner.execute_op("bake_maps", {
        "input": str(scene_blend),
        "out_dir": str(maps_dir),
        "maps": None,
        "resolution": resolution,
        "detail": detail_map,
        # micro weave (triplanar height maps -> Bump nodes) blends into the
        # baked normal map via the self-bake + whiteout pass (see harness)
        "detail_normal": True,
        "hp_glb": str(hp_glb),
        "save_blend": str(scene_blend),
        "device": bake_device,
    }, timeout_sec=bake_timeout_sec), "bake_maps"))
    log(f"baked maps ({bake.get('device', {}).get('device', '?')} via "
        f"{bake.get('device', {}).get('compute_device_type') or 'CPU'}): "
        f"{sorted(k for k, v in bake.get('maps', {}).items() if isinstance(v, dict) and 'stats' in v)}")

    # ── 3. LP: decimate the delivery scene to the tier budget ───────────────
    # Phase 4: the card's explicit ceiling overrides the tier table (and
    # unblocks 'complex'); the spec tri_budget remains the last-resort
    # fallback when no ceiling is known at all.
    budget = job.effective_polycount_ceiling()
    if budget is None:
        budget = int(getattr(spec, "tri_budget", 60_000))
    dec = _timed("decimate_lp", lambda: _require(runner.execute_op("decimate_to_budget", {
        "input": str(scene_blend),
        "output": str(lp_glb),
        "budget": budget,
    }), "decimate_to_budget"))
    log(f"LP exported: {dec.get('triangle_equivalent')} tri-eq "
        f"(budget {budget}, decimated={dec.get('decimated')})")

    def _render_review() -> tuple[list[str], dict[str, Any] | None]:
        # Close-ups (round 4) and contrast probes (Phase 8 item 2) come
        # from the spec's template — the finishing layer never decides
        # WHAT to frame or measure, only threads it through.
        closeups = [
            {"name": c.name, "part": c.part, "direction": c.direction,
             "pad": c.pad, "frame": c.frame}
            for c in (getattr(spec, "review_closeups", None) or [])
        ]
        probes = [
            {"name": p.name, "view": p.view, "region": list(p.region),
             "cycles": list(p.cycles), "band": list(p.band),
             "min_amplitude": p.min_amplitude, "axes": p.axes}
            for p in (getattr(spec, "contrast_probes", None) or [])
        ]
        rv = _timed("review_renders", lambda: _require(runner.execute_op("render_views", {
            "model_path": str(lp_glb),
            "output_dir": str(review_dir),
            "prefix": job.job_code,
            "closeups": closeups,
        }), "render_views"))
        if rv.get("closeup_skips"):
            log(f"review close-ups skipped: {rv['closeup_skips']}")
        files_rendered = sorted(str(p) for p in review_dir.glob("*.png"))
        render_metrics = _collect_render_metrics(rv.get("views", {}), probes, log)
        log(f"review renders awaiting owner review: {files_rendered}")
        return files_rendered, render_metrics

    # ── 3b. PLACEHOLDER-DIMENSION REFUSAL (owner's overnight order, T4) ──────
    # The pipeline is exercised (structural review renders are valid output)
    # but NO deliverable package is emitted: the dims are stand-ins until the
    # owner supplies real ones (rule 9 — never inferred, never a guessed
    # standard size). Evidence lands in output/blocked/<JOB>/qa_report.json.
    if job.dims_placeholder:
        review_files, render_metrics = _render_review()
        blocked_dir = out_root.parent / "blocked" / job.job_code
        blocked_dir.mkdir(parents=True, exist_ok=True)
        blocked_report = {
            "job_code": job.job_code,
            "refused": True,
            "refusal_reason": (
                "dims_placeholder: the job card's dimensions are PLACEHOLDER "
                "stand-ins (the owner has not supplied real values). No "
                "deliverable package was emitted — dimensions are never "
                "inferred (GLM_BRIEF rule 9)."
            ),
            "placeholder_dims": {
                "length": job.dims.length, "width": job.dims.width,
                "height": job.dims.height, "unit": job.dims.unit,
                "source": "job card stand-in values, NOT owner-supplied",
            },
            "gates": None,
            "finish": {
                "lp_tri_equivalent": dec.get("triangle_equivalent"),
                "hp_tri_equivalent": bake.get("hp_triangle_equivalent"),
                "lp_budget": budget,
                "texture_resolution": resolution,
                "bake_timeout_sec": bake_timeout_sec,
                "bake_device": bake_device,
                "bake_device_resolved": bake.get("device"),
                "step_timings_sec": {**timings,
                                     "total_chain": round(time.perf_counter() - _t_chain_start, 3)},
                "detail_parts": detail_map,
                "uv_atlas": prep.get("uv_atlas"),
                "uv_diagnostics": prep.get("uv"),
                "bake": bake.get("maps"),
                "review_renders": review_files,
                "render_metrics": render_metrics,
                "review_note": "Structural review only — band order, materials, "
                               "tape and label placement. Proportions render "
                               "correctly at any dims (fractions of H), but "
                               "silhouette review needs the real dimensions.",
            },
            "unblock": "Put the real L x W x H (explicit unit) into the job "
                       "card and remove dims_placeholder, then re-run.",
        }
        (blocked_dir / "qa_report.json").write_text(
            json.dumps(blocked_report, indent=2) + "\n", encoding="utf-8")
        for _ in range(3):
            log("REFUSED — PLACEHOLDER DIMENSIONS: no deliverable package "
                f"emitted for {job.job_code} (see {blocked_dir / 'qa_report.json'})")
        raise PlaceholderDimensionsError(
            f"REFUSED — job {job.job_code} carries dims_placeholder: true. "
            f"The chain ran (renders + evidence in {blocked_dir}) but no "
            f"package was emitted. Owner must supply real dimensions (rule 9)."
        )

    # ── 4. assemble deliverables ────────────────────────────────────────────
    package_dir = out_root / job.job_code
    package_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []

    def _record(path: Path, placeholder: bool = False, note: str = "") -> None:
        files.append({
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "placeholder": placeholder,
            "note": note,
        })

    fbx_path = package_dir / f"{job.job_code}.fbx"
    _timed("export_fbx", lambda: _require(runner.execute_op("export_fbx", {
        "input": str(scene_blend),
        "path": str(fbx_path),
        "axis_up": _fbx_axes(job)[0],
        "axis_forward": _fbx_axes(job)[1],
    }), "export_fbx"))
    _record(fbx_path, note="exported from the LIVE QUAD-CLEAN SCENE (owner "
                           "decision): quads preserved, n-gon gate meaningful, "
                           "polycount counted as authored")

    usdz_path = package_dir / f"{job.job_code}_LP.usdz"
    usdz_res = _timed("export_usdz", lambda: _require(runner.execute_op("export_usdz", {
        "input": str(lp_glb),
        "path": str(usdz_path),
    }), "export_usdz"))
    _record(usdz_path, note=f"export method: {usdz_res.get('method', '?')} "
                            f"(from the decimated LP GLB)")

    lp_pkg = package_dir / f"{job.job_code}_LP.glb"
    shutil.copy2(lp_glb, lp_pkg)
    _record(lp_pkg, note=f"decimated delivery mesh ({dec.get('triangle_equivalent')} "
                         f"tri-eq, budget {budget})")
    shutil.copy2(hp_glb, package_dir / f"{job.job_code}_HP.glb")
    _record(package_dir / f"{job.job_code}_HP.glb",
            note=f"high-poly detail shell ({bake.get('hp_triangle_equivalent')} tri-eq)")

    for map_name, suffix in _BAKE_MAP_FILES.items():
        src = maps_dir / f"{map_name}.png"
        if not src.is_file():
            raise FileNotFoundError(f"bake did not produce {map_name}.png "
                                    f"(expected at {src})")
        dst = package_dir / (job.job_code + suffix)
        shutil.copy2(src, dst)
        stats = bake.get("maps", {}).get(map_name, {}).get("stats", {})
        _record(dst, note=f"baked map ({map_name}): {json.dumps(stats)}")

    # ── 5. review renders for the owner (valid T3 output: awaits review) ────
    review_files: list[str] = []
    render_metrics: dict[str, Any] | None = None
    if review_renders:
        review_files, render_metrics = _render_review()

    # ── 6. close the timing ledger: assembly + total ────────────────────────
    finish_section = {
        "fbx_source": "live_quad_scene",
        "lp_tri_equivalent": dec.get("triangle_equivalent"),
        "hp_tri_equivalent": bake.get("hp_triangle_equivalent"),
        "lp_budget": budget,
        "lp_decimated": dec.get("decimated"),
        "texture_resolution": resolution,
        "bake_timeout_sec": bake_timeout_sec,
        "bake_device": bake_device,
        "bake_device_resolved": bake.get("device"),
        "step_timings_sec": {**timings,
                             "total_chain": round(time.perf_counter() - _t_chain_start, 3)},
        "detail_parts": detail_map,
        "uv_atlas": prep.get("uv_atlas"),
        "uv_diagnostics": prep.get("uv"),
        "bake": bake.get("maps"),
        "ao_method": bake.get("maps", {}).get("ao_method"),
        "review_renders": review_files,
        "render_metrics": render_metrics,
        "review_note": "Renders await owner review (T3 protocol: a list of "
                       "renders awaiting review is a valid output).",
    }

    return _assemble_and_audit(
        job, package_dir, runner, log, files, fbx_path, usdz_path,
        usdz_method=usdz_res.get("method"),
        usdz_direct_error=usdz_res.get("direct_error") or None,
        source_note=f"spec: {getattr(spec, 'name', '?')} via finish_delivery "
                    f"(scene: {scene_blend})",
        extra_sections={"finish": finish_section},
    )
