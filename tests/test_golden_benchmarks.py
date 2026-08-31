"""Golden benchmark builds — every benchmark spec must produce a GLB that
passes both the dimension gate and the mesh gate.

Dimensions are grounded in real-world standards (see input/benchmarks/README.md,
sources: dimensions.com). Marked `blender` — skipped when Blender is absent."""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.blender

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = PROJECT_ROOT / "input" / "benchmarks"

GOLDEN_SPECS = sorted(BENCH_DIR.glob("*.spec.json")) + [
    PROJECT_ROOT / "input" / "sample_desk.spec.json",
]


def _get_runner():
    from src.blender.locate import locate_blender
    from src.blender.runner import BlenderRunner

    if locate_blender() is None:
        pytest.skip("Blender 3.3+ not found on this machine")
    return BlenderRunner()


@pytest.mark.parametrize("spec_path", GOLDEN_SPECS, ids=lambda p: p.stem)
def test_golden_spec_builds_and_passes_gates(spec_path, tmp_path):
    from src.agent.verifier import Verifier, load_merged_mesh
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import ObjectSpec

    spec = ObjectSpec.model_validate(json.loads(spec_path.read_text(encoding="utf-8")))
    out = tmp_path / f"{spec_path.stem}.glb"

    runner = _get_runner()
    runner.execute_op("build_from_spec", resolve_spec_to_build_params(spec, output_glb_path=str(out)))
    assert out.exists(), f"build did not write {out}"

    measure = runner.execute_op("measure", {"model_path": str(out)})
    report = Verifier().verify_run(spec, measure, out)

    assert report.dimension_gate.passed, report.feedback_for_agent
    assert report.mesh_gate.passed, report.mesh_gate.errors
    assert report.mesh_gate.is_watertight, (
        f"{spec_path.stem} not watertight: {report.mesh_gate.warnings}"
    )
    assert report.mesh_gate.volume_m3 > 0, f"{spec_path.stem} has zero volume"

    mesh = load_merged_mesh(out)
    assert mesh is not None
    # Real-world scale sanity: nothing may collapse to a degenerate sliver.
    assert min(mesh.extents) > 0.01, f"{spec_path.stem} has a sub-centimeter axis"


@pytest.mark.parametrize("spec_path", GOLDEN_SPECS, ids=lambda p: p.stem)
def test_golden_spec_renders(spec_path, tmp_path):
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import ObjectSpec

    spec = ObjectSpec.model_validate(json.loads(spec_path.read_text(encoding="utf-8")))
    out = tmp_path / f"{spec_path.stem}.glb"
    renders = tmp_path / "renders"

    runner = _get_runner()
    runner.execute_op("build_from_spec", resolve_spec_to_build_params(spec, output_glb_path=str(out)))
    result = runner.execute_op(
        "render_views",
        {"model_path": str(out), "views": ["iso"], "output_dir": str(renders), "resolution": [256, 256]},
    )
    assert result["success"], result.get("error")
    assert Path(result["views"]["iso"]).exists()
