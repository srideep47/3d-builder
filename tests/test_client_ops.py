"""Blender-marked tests for the T1 topology harness ops (count_ngons,
topology_report) and the `validate` CLI. Skipped automatically when no
Blender installation is found.

These tests also carry two empirical facts T2 depends on:
- whether Blender's FBX export/import round trip preserves n-gon caps
  (a default 8-segment cylinder has two 8-gon caps);
- whether an FBX round trip preserves metric bounds closely enough for the
  client dimension gate (±0.01 in) to pass.
"""

import shutil
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.blender

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _get_runner():
    from src.blender.locate import locate_blender
    from src.blender.runner import BlenderRunner

    if locate_blender() is None:
        pytest.skip("Blender 3.3+ not found on this machine")
    return BlenderRunner()


@pytest.fixture(scope="module")
def runner():
    return _get_runner()


def _build_glb(runner, tmp_dir: Path, name: str, shape: str,
               dimensions: list[float], position: list[float]) -> Path:
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import ObjectSpec

    spec = ObjectSpec.model_validate({
        "name": name,
        "parts": [{"name": name, "shape": shape,
                   "dimensions": dimensions, "position": position}],
    })
    out = tmp_dir / f"{name}.glb"
    result = runner.execute_op(
        "build_from_spec", resolve_spec_to_build_params(spec, output_glb_path=str(out)))
    assert result["success"], result.get("error")
    assert out.is_file()
    return out


@pytest.fixture(scope="module")
def box_glb(runner, tmp_path_factory):
    """Grounded box, exactly 12 × 34 × 5 in (0.3048 × 0.8636 × 0.127 m).
    Box is center-anchored, so position z = half the height grounds it."""
    return _build_glb(runner, tmp_path_factory.mktemp("box"), "gatebox",
                      "box", [0.3048, 0.8636, 0.127], [0.0, 0.0, 0.0635])


@pytest.fixture(scope="module")
def ngon_fbx(runner, tmp_path_factory):
    """An FBX that genuinely contains n-gons: a raw 8-segment Blender
    cylinder (two 8-gon caps) exported straight to FBX via run_script —
    never routed through GLB, which triangulates everything."""
    tmp = tmp_path_factory.mktemp("ngon")
    fbx = (tmp / "ngon_cyl.fbx").as_posix()
    code = (
        "import bpy\n"
        "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
        "bpy.ops.mesh.primitive_cylinder_add(radius=0.1, depth=0.3, "
        "vertices=8, location=(0, 0, 0.15))\n"
        "obj = bpy.context.active_object\n"
        "obj.name = 'ngon_cyl'\n"
        f"bpy.ops.export_scene.fbx(filepath=r'{fbx}')\n"
        "RESULT = {'fbx': r'%s'}\n" % fbx
    )
    result = runner.execute_op("run_script", {"code": code})
    assert result["success"], result.get("error")
    return Path(fbx)


# ── op_count_ngons ───────────────────────────────────────────────────────────


def test_count_ngons_zero_on_box_glb(runner, box_glb):
    """glTF triangulates, so a shipped GLB can never carry n-gons — the
    strict gate target is the FBX (next test)."""
    r = runner.execute_op("count_ngons", {"model_path": str(box_glb)})
    assert r["success"] and r["ngon_count"] == 0


def test_count_ngons_detects_ngons_in_fbx(runner, ngon_fbx):
    """EMPIRICAL (feeds T2): Blender's FBX export preserves n-gon caps —
    an 8-segment cylinder ships with exactly two 8-gon caps. If Blender ever
    triangulates FBX output, this test failing is the early warning."""
    r = runner.execute_op("count_ngons", {"model_path": str(ngon_fbx)})
    assert r["success"]
    assert r["ngon_count"] == 2, f"expected 2 n-gon caps, got {r['ngon_count']}"


# ── op_topology_report ───────────────────────────────────────────────────────


def test_topology_report_box_glb(runner, box_glb):
    r = runner.execute_op("topology_report", {"model_path": str(box_glb)})
    assert r["success"]
    # glTF triangulates the 6-quad box into 12 triangles
    assert r["triangles"] == 12 and r["quads"] == 0 and r["ngons"] == 0
    assert r["triangle_equivalent"] == 12
    assert r["nonmanifold_edges"] == 0 and r["loose_vertices"] == 0
    dims = r["bounds"]["dimensions"]
    assert dims[0] == pytest.approx(0.3048, abs=1e-4)
    assert dims[1] == pytest.approx(0.8636, abs=1e-4)
    assert dims[2] == pytest.approx(0.127, abs=1e-4)
    assert r["bounds"]["min"][2] == pytest.approx(0.0, abs=1e-4)  # grounded


def test_topology_report_fbx_with_ngons(runner, ngon_fbx):
    r = runner.execute_op("topology_report", {"model_path": str(ngon_fbx)})
    assert r["success"]
    assert r["ngons"] == 2 and r["quads"] == 8 and r["triangles"] == 0
    # 8 quads → 16 tris + 2 × (8−2) = 12 → 28 triangle-equivalent
    assert r["triangle_equivalent"] == 28
    dims = r["bounds"]["dimensions"]
    assert dims[0] == pytest.approx(0.2, abs=1e-3)
    assert dims[1] == pytest.approx(0.2, abs=1e-3)
    assert dims[2] == pytest.approx(0.3, abs=1e-3)
    assert r["bounds"]["min"][2] == pytest.approx(0.0, abs=1e-3)  # grounded


# ── validate CLI end-to-end ──────────────────────────────────────────────────


def _assemble_package(pkg: Path, job_code: str, fbx: Path, glb: Path) -> Path:
    pkg.mkdir(parents=True, exist_ok=True)
    shutil.copy2(fbx, pkg / f"{job_code}.fbx")
    shutil.copy2(glb, pkg / f"{job_code}_LP.glb")
    shutil.copy2(glb, pkg / f"{job_code}_HP.glb")
    # USDZ content is T2's export problem; the size gate is presence-only
    # until the client answers the cap question.
    (pkg / f"{job_code}_LP.usdz").write_bytes(b"usdz-placeholder")
    for tex in ("_BaseColor", "_Normal", "_Roughness", "_Metallic", "_AO"):
        (pkg / f"{job_code}{tex}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    return pkg


def test_validate_cli_all_gates_green(runner, box_glb, tmp_path):
    from typer.testing import CliRunner

    from src.cli import app as cli_app

    # GLB → FBX via the convert op (fresh process, rule 1)
    fbx = tmp_path / "E2EJOB0001.fbx"
    conv = runner.execute_op("convert", {"input": str(box_glb), "output": str(fbx)})
    assert conv["success"], conv.get("error")

    pkg = _assemble_package(tmp_path / "packages" / "E2EJOB0001",
                            "E2EJOB0001", fbx, box_glb)
    job_yaml = tmp_path / "job.yaml"
    job_yaml.write_text(yaml.safe_dump({
        "job_code": "E2EJOB0001",
        "dims": {"length": 12.0, "width": 34.0, "height": 5.0, "unit": "in"},
        "complexity": "simple",
        "orientation": "floor",
        "product_class": "widget",
        "part_scope": "e2e fixture",
        "reference_dir": "input/refs",
    }), encoding="utf-8")

    result = CliRunner().invoke(cli_app, ["validate", str(pkg), "--job", str(job_yaml)])
    assert result.exit_code == 0, result.output
    assert "ALL GATES PASSED" in result.output


def test_validate_cli_fails_on_broken_package(runner, box_glb, tmp_path):
    from typer.testing import CliRunner

    from src.cli import app as cli_app

    fbx = tmp_path / "E2EJOB0002.fbx"
    conv = runner.execute_op("convert", {"input": str(box_glb), "output": str(fbx)})
    assert conv["success"]

    pkg = _assemble_package(tmp_path / "packages" / "E2EJOB0002",
                            "E2EJOB0002", fbx, box_glb)
    # Break exactly one thing the client would reject: an oversized FBX
    (pkg / "E2EJOB0002.fbx").write_bytes((pkg / "E2EJOB0002.fbx").read_bytes()
                                         + b"\0" * 10_000_001)
    job_yaml = tmp_path / "job.yaml"
    job_yaml.write_text(yaml.safe_dump({
        "job_code": "E2EJOB0002",
        "dims": {"length": 12.0, "width": 34.0, "height": 5.0, "unit": "in"},
        "complexity": "simple",
        "orientation": "floor",
        "product_class": "widget",
        "part_scope": "e2e fixture",
        "reference_dir": "input/refs",
    }), encoding="utf-8")

    result = CliRunner().invoke(cli_app, ["validate", str(pkg), "--job", str(job_yaml)])
    assert result.exit_code == 1
    assert "GATE(S) FAILED" in result.output
    # the FBX's own ~13 KB + 10,000,001 padding bytes lands just over the cap
    assert "E2EJOB0002.fbx:" in result.output and "> 10MB" in result.output
