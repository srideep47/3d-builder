"""T4 placeholder-dimension refusal (owner's overnight order; rule 9).

The MAYA00053153 job card carries verbatim dashboard stand-in dims
(12 x 12 x 65 IN) with `dims_placeholder: true` — the owner's dimension
line arrived blank. The finishing chain still runs (structural review
renders are valid output) but NO deliverable package is emitted: evidence
lands in <out_root>/../blocked/<JOB>/qa_report.json and the caller gets
PlaceholderDimensionsError (the CLI turns it into exit code 2 + the
BLOCKED panel). A standard queen size must NEVER be inferred.
"""

import json
from pathlib import Path

import pytest

from src.client.job import load_job
from src.client.package import (PlaceholderDimensionsError, finish_delivery,
                                package_delivery)
from src.spec.template import compile_spec, load_template

PROJECT_ROOT = Path(__file__).resolve().parents[1]
JOB = PROJECT_ROOT / "input" / "jobs" / "MAYA00053153.yaml"
TEMPLATE = PROJECT_ROOT / "templates" / "mattress.yaml"


# ── the job card itself ──────────────────────────────────────────────────────


def test_job_card_carries_placeholder_flag():
    job = load_job(JOB)
    assert job.dims_placeholder is True
    # the dashboard stand-ins, quoted verbatim — NOT an inference
    assert (job.dims.length, job.dims.width, job.dims.height) == (12, 12, 65)
    assert job.dims.unit == "IN"


def test_compile_succeeds_under_placeholder_dims():
    """The chain is exercisable for structural review renders — the refusal
    happens at package emission, not at compile."""
    spec, _warnings = compile_spec(load_template(TEMPLATE), load_job(JOB))
    assert spec.parts
    assert any(m.applies_to == "overall.height_z" for m in spec.measurements)


# ── package_delivery: refuses at the entry, before any work ─────────────────


def test_package_delivery_refuses_placeholder_before_any_work(tmp_path):
    job = load_job(JOB)
    # a NONEXISTENT source file: the refusal must fire before the source is
    # even looked at — ordering is part of the contract
    with pytest.raises(PlaceholderDimensionsError, match="REFUSED"):
        package_delivery(job, tmp_path / "no_such_source.glb",
                         out_root=tmp_path / "packages")
    assert not (tmp_path / "packages" / job.job_code).exists()


# ── finish_delivery: chain runs for review, emission refused ─────────────────


class _ChainRecorder:
    """Stands in for BlenderRunner: records every op, returns plausible
    results, writes the review renders the real op would produce."""

    def __init__(self):
        self.ops = []

    def execute_op(self, op, params):
        self.ops.append(op)
        if op == "prepare_delivery_scene":
            # real observed values for the mattress at queen proportions
            # (scripts/probe_uv_islands.py, 2026-09-01): 2118 faces collapse
            # to 80 UV islands once _uv_face_groups merges UV-contiguous
            # faces; pack scale 0.75, texel-density ratio 1.0000
            return {"success": True, "uv_atlas": {"pack_scale": 0.75},
                    "uv": {"islands_total": 80}}
        if op == "bake_maps":
            return {"success": True,
                    "maps": {"basecolor": {"stats": {"std": 0.35}}},
                    "hp_triangle_equivalent": 201600}
        if op == "decimate_to_budget":
            return {"success": True, "triangle_equivalent": 3468,
                    "decimated": False}
        if op == "render_views":
            out = Path(params["output_dir"])
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{params.get('prefix', 'job')}_front.png").write_bytes(
                b"\x89PNG\r\n\x1a\n")
            return {"success": True}
        raise AssertionError(f"unexpected op {op!r} in the refusal path")


def test_finish_delivery_runs_chain_then_refuses(tmp_path):
    job = load_job(JOB)
    spec, _warnings = compile_spec(load_template(TEMPLATE), job)
    rec = _ChainRecorder()
    logs: list[str] = []

    with pytest.raises(PlaceholderDimensionsError, match="REFUSED"):
        finish_delivery(job, spec, out_root=tmp_path / "packages",
                        runner=rec, log=logs.append, resolution=64)

    # the chain RAN for structural review (build → atlas → bake → decimate
    # → review renders)...
    assert rec.ops == ["prepare_delivery_scene", "bake_maps",
                       "decimate_to_budget", "render_views"]
    # ...but no deliverable export ever happened
    assert "export_fbx" not in rec.ops and "export_usdz" not in rec.ops
    # loud refusal: three log lines
    assert sum(1 for m in logs if m.startswith("REFUSED")) == 3

    # evidence on disk in the BLOCKED dir (sibling of the packages root)
    blocked = tmp_path / "blocked" / job.job_code
    report = json.loads(
        (blocked / "qa_report.json").read_text(encoding="utf-8"))
    assert report["refused"] is True
    assert "dims_placeholder" in report["refusal_reason"]
    assert report["placeholder_dims"] == {
        "length": 12, "width": 12, "height": 65, "unit": "IN",
        "source": "job card stand-in values, NOT owner-supplied",
    }
    assert report["gates"] is None  # nothing shipped, no gates claimed
    fin = report["finish"]
    assert fin["lp_tri_equivalent"] == 3468
    assert fin["hp_tri_equivalent"] == 201600
    assert fin["review_renders"]  # structural review renders recorded
    # the quilt displacement evidence travels with the refusal report
    disp = fin["detail_parts"]["crown"]["displacement"]
    assert disp["pattern"] == "grid_diamond"
    assert disp["amplitude_m"] == pytest.approx(
        0.07 * (0.3048 - 2 * 0.016 * 0.3048) / 8)  # fraction of one quilt CELL
    assert disp["restrict"] == "up"
    assert "unblock" in report
    # and NO package directory was created
    assert not (tmp_path / "packages" / job.job_code).exists()


# ── the CLI wiring: PlaceholderDimensionsError -> exit code 2 ────────────────


def test_cli_package_template_placeholder_exits_2(tmp_path, monkeypatch):
    """`package --template` on a placeholder-dims job card must exit 2 with
    the BLOCKED panel — without needing Blender for the wiring itself."""
    import src.blender.runner as runner_mod
    import src.client.package as pkg_mod

    class _StubRunner:
        is_available = True

    def _refuse(*_a, **_k):
        raise PlaceholderDimensionsError("REFUSED — wiring test")

    monkeypatch.setattr(runner_mod, "BlenderRunner", _StubRunner)
    monkeypatch.setattr(pkg_mod, "finish_delivery", _refuse)

    from typer.testing import CliRunner

    from src.cli import app
    result = CliRunner().invoke(app, [
        "package", "--job", str(JOB), "--template", str(TEMPLATE),
        "--out-root", str(tmp_path / "packages"),
    ])
    assert result.exit_code == 2, result.output
    assert "BLOCKED" in result.output


# ── the control: real owner-supplied dims -> package IS emitted ──────────────


@pytest.mark.blender
def test_finish_delivery_real_dims_emits_package(tmp_path):
    """Same entry point, flag flipped: with REAL dimensions the template
    path emits a full package + gates (the mirror of the refusal above).
    Minimal template + 256px bakes keep this under ~2 minutes."""
    from src.blender.locate import locate_blender

    if locate_blender() is None:
        pytest.skip("Blender 3.3+ not found on this machine")
    from src.blender.runner import BlenderRunner

    from src.client.job import JobCard, JobDims
    from src.spec.template import (FootprintSpec, SurfaceSpec, TapeEdgeSpec,
                                   TemplateBand, TemplateSpec)

    tpl = TemplateSpec(
        product_class="mattress",
        footprint=FootprintSpec(exponent=4.0, segments=16),
        bands=[TemplateBand(name="top", height_fraction=0.6, material="shell"),
               TemplateBand(name="bottom", height_fraction=0.4, material="shell")],
        tape_edges=[TapeEdgeSpec(at_boundary_below="top",
                                 width_fraction=0.05,
                                 protrusion_fraction=0.03, material="trim")],
        textures={"shell": SurfaceSpec(base="flat", roughness=0.8),
                  "trim": SurfaceSpec(base="flat", roughness=0.6)},
    )
    job = JobCard(job_code="REALDIM1", product_class="mattress",
                  dims=JobDims(length=1.0, width=0.6, height=0.2, unit="m"),
                  complexity="simple", orientation="floor",
                  reference_dir=PROJECT_ROOT / "input" / "reference")
    spec, warnings = compile_spec(tpl, job)
    assert warnings == []

    report = finish_delivery(job, spec, out_root=tmp_path / "packages",
                             runner=BlenderRunner(), resolution=256,
                             review_renders=False, log=lambda _m: None)
    pkg = tmp_path / "packages" / "REALDIM1"
    assert pkg.is_dir()
    for name in ("REALDIM1.fbx", "REALDIM1_LP.usdz", "REALDIM1_LP.glb",
                 "REALDIM1_HP.glb", "REALDIM1_BaseColor.png",
                 "REALDIM1_Normal.png", "REALDIM1_Roughness.png",
                 "REALDIM1_Metallic.png", "REALDIM1_AO.png",
                 "qa_report.json"):
        assert (pkg / name).is_file(), name
    assert report["all_passed"], json.dumps(report["gates"], indent=1)
    fin = report["finish"]
    assert fin["fbx_source"] == "live_quad_scene"
    assert fin["lp_tri_equivalent"] > 0
    # no blocked evidence for a real-dims run
    assert not (tmp_path / "blocked" / "REALDIM1").exists()
