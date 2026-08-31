"""Assemble the client delivery package (§4.1) + qa_report.json audit record.

Orchestration lives here, never in the harness (rule 10); no product nouns
(rule 11) — this module knows file names, formats and the job card only.

T2 PLACEHOLDERS (owner amendment 3) — visible, never silent:
- Until T3 builds the real HP/LP split, ``<JOB>_LP.glb`` and ``<JOB>_HP.glb``
  are byte-identical copies of the source GLB.
- Until T3 bakes real maps, the five texture PNGs are synthetic flat fills.
Both facts are recorded in qa_report.json (``placeholders`` block + per-file
``placeholder`` flags) and logged by the caller's log callback.

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
import sys
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
# (independently parsed) next to this request.
FBX_AXIS_UP = "Y"
FBX_AXIS_FORWARD = "-Z"


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
    source_glb = Path(source_glb)
    if not source_glb.is_file():
        raise FileNotFoundError(f"source GLB not found: {source_glb}")
    if runner is None:
        from ..blender.runner import BlenderRunner

        runner = BlenderRunner()

    package_dir = Path(out_root) / job.job_code
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
        "axis_up": FBX_AXIS_UP,
        "axis_forward": FBX_AXIS_FORWARD,
    })
    _record(fbx_path, note="binary FBX exported from the source GLB "
                           "(triangulated: glTF stores triangles only)")

    # Independent, non-Blender verification of what was actually written
    # (owner amendment 1). A Blender re-import is self-consistent even when
    # the file is wrong for a third party — this parse is the third party.
    fbx_info = read_fbx_info(fbx_path)

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

    # ── Gates (same facts path as the validate CLI) ─────────────────────────
    topology = runner.execute_op("topology_report", {"model_path": str(fbx_path)})
    facts = MeshFacts.from_topology_report(topology)
    results = run_all_gates(package_dir, job, facts)

    # ── Cross-checks: harness vs the independent FBX parse ──────────────────
    # The independent parse resolves world space in the FILE's declared axis
    # system; the harness re-import reports Blender Z-up. Extents are compared
    # as a sorted multiset (axis-direction proof lives in the chiral export
    # test, which owns the signed-permutation machinery).
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
    report = {
        "schema": "threed-qa-report/1",
        "job_code": job.job_code,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "package_dir": str(package_dir),
        "source_glb": str(source_glb),
        "all_passed": all(r.passed for r in results),
        "job_card": job.model_dump(mode="json"),
        "gates": [r.to_dict() for r in results],
        "axis_convention": {
            "requested": {"axis_up": FBX_AXIS_UP, "axis_forward": FBX_AXIS_FORWARD},
            "written": fbx_info.axes.to_dict(),
            "fbx_version": fbx_info.version,
            "creator": fbx_info.creator,
            "verified_by": "independent binary-FBX header parse (src/client/fbx_inspect.py), not a Blender round trip",
        },
        "usdz": {
            "method": usdz_res.get("method"),
            "direct_error": usdz_res.get("direct_error") or None,
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
        "placeholders": {
            "lp_hp_single_source": True,
            "textures_synthetic": True,
            "detail": "T3 (UV + HP/LP bake) not yet built: the LP and HP GLBs are "
                      "byte-identical copies of the source, and the texture PNGs are "
                      "synthetic flat fills. Replace before any real delivery.",
        },
        "open_questions": [dict(q) for q in OPEN_QUESTIONS],
    }

    report_path = package_dir / "qa_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log(f"qa_report.json written: {report_path}")
    return report
