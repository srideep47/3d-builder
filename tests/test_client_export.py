"""Blender-marked export verification (T2) — the owner's amendment 1 tests.

A Blender->FBX->Blender round trip is self-consistent even when the file is
wrong for a third party, so the axis convention is verified against things
that are NOT Blender:

- the FBX's own GlobalSettings declarations, parsed straight from the binary
  records by src/client/fbx_inspect.py (asserted explicitly, values pinned);
- the FBX's raw geometry + Model transforms resolved to world space by the
  same independent parser, matched against the ANALYTIC expected cloud of
  the chiral fixture (input/fixtures/chiral_test.spec.json) by signed axis
  permutation — exactly one mapping is valid, and its determinant is +1
  only if no handedness flip occurred;
- trimesh reading the source GLB (a second independent, non-Blender read of
  what we built — note trimesh reports glTF's native Y-up space);
- the boss position recomputed through each discovered mapping: the
  asymmetric feature must land on +X +Y where it was built.

Also covers USDZ export and the full `package` -> `validate` CLI round on
the coffee_table golden benchmark. Skipped automatically without Blender.
"""

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

pytestmark = pytest.mark.blender

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# input/fixtures/chiral_test.spec.json: base box 20 x 12 x 9 in centred on
# the origin and grounded; boss box 4 x 4 x 2 in centred at (+5 in, +3 in,
# 10 in) — off-centre on the two distinct horizontal axes, so a mirror or
# transposition cannot reproduce the point cloud.
CHIRAL_BOXES = [
    ((0.508, 0.3048, 0.2286), (0.0, 0.0, 0.1143)),
    ((0.1016, 0.1016, 0.0508), (0.127, 0.0762, 0.254)),
]
BOSS_TOP_CENTROID_M = (0.127, 0.0762, 0.2794)  # boss top-face corners sit at z=0.2794
# coffee_table golden benchmark: overall 1.2 x 0.6 x 0.4 m, in inches
COFFEE_DIMS_IN = {"length": 47.24409, "width": 23.62205, "height": 15.74803}


def _get_runner():
    from src.blender.locate import locate_blender
    from src.blender.runner import BlenderRunner

    if locate_blender() is None:
        pytest.skip("Blender 3.3+ not found on this machine")
    return BlenderRunner()


@pytest.fixture(scope="module")
def runner():
    return _get_runner()


@pytest.fixture(scope="module")
def chiral_glb(runner, tmp_path_factory):
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import ObjectSpec

    spec = ObjectSpec.model_validate(
        json.loads((PROJECT_ROOT / "input/fixtures/chiral_test.spec.json").read_text(encoding="utf-8"))
    )
    out = tmp_path_factory.mktemp("chiral") / "chiral.glb"
    result = runner.execute_op(
        "build_from_spec", resolve_spec_to_build_params(spec, output_glb_path=str(out)))
    assert result["success"], result.get("error")
    return out


@pytest.fixture(scope="module")
def chiral_fbx(runner, chiral_glb, tmp_path_factory):
    """The deliverable FBX exactly as package.py exports it (Y-up / -Z)."""
    out = tmp_path_factory.mktemp("chiral") / "chiral.fbx"
    result = runner.execute_op("export_fbx", {
        "input": str(chiral_glb), "path": str(out),
        "axis_up": "Y", "axis_forward": "-Z",
    })
    assert result["success"], result.get("error")
    return out


@pytest.fixture(scope="module")
def chiral_usdz(runner, chiral_glb, tmp_path_factory):
    out = tmp_path_factory.mktemp("chiral") / "chiral_LP.usdz"
    result = runner.execute_op("export_usdz", {"input": str(chiral_glb), "path": str(out)})
    assert result["success"], result.get("error")
    return Path(out), result


def _expected_cloud() -> np.ndarray:
    from src.client.fbx_inspect import box_corner_cloud

    return box_corner_cloud(CHIRAL_BOXES)


def _unique_mapping(dst: np.ndarray) -> dict:
    """The single signed permutation mapping the analytic Blender-space cloud
    onto ``dst`` — fails loudly if the fixture ever stops being chiral."""
    from src.client.fbx_inspect import find_axis_mapping

    mappings = find_axis_mapping(_expected_cloud(), dst, tol=1e-3)
    assert len(mappings) == 1, \
        f"chiral fixture must match exactly one mapping, got {[m['description'] for m in mappings]}"
    return mappings[0]


def _to_blender_space(dst: np.ndarray, mapping: dict) -> np.ndarray:
    """Invert file = s·M·blender + t (M is a signed permutation, orthogonal:
    its inverse is its transpose). Translation is reconstructed from the
    point-set means, which coincide because the GLB/FBX split vertices have
    uniform multiplicity per corner for this fixture."""
    M, s = mapping["matrix"], mapping["scale"]
    src = _expected_cloud()
    t = dst.mean(axis=0) - s * (M @ src.mean(axis=0))
    return ((dst - t) @ M) / s


def _assert_boss_on_correct_side(blender_hat: np.ndarray, source: str) -> None:
    """The boss (the asymmetric feature) must sit at +X +Y on top of the
    base — the exact failure mode of a mirror, which passes every dimension
    gate and only 'looks wrong'."""
    boss = blender_hat[blender_hat[:, 2] > 0.24]
    assert len(boss) >= 4, f"no vertices in the boss region ({source}) — fixture or mapping broken"
    centroid = boss.mean(axis=0)
    assert centroid[0] == pytest.approx(BOSS_TOP_CENTROID_M[0], abs=1e-3), \
        f"{source}: boss flipped across the Y-Z plane"
    assert centroid[1] == pytest.approx(BOSS_TOP_CENTROID_M[1], abs=1e-3), \
        f"{source}: boss flipped across the X-Z plane"
    assert centroid[2] == pytest.approx(BOSS_TOP_CENTROID_M[2], abs=1e-3)


# ── FBX GlobalSettings: the declared convention, pinned (amendment 1) ───────


def test_fbx_global_settings_declared_values(chiral_fbx):
    """The FBX must DECLARE Y-up in its own header. Values are pinned
    exactly (not just self-consistent) — these are the numbers recorded in
    PROGRESS.md; if the exporter ever drifts, this fails loudly."""
    from src.client.fbx_inspect import read_fbx_info

    info = read_fbx_info(chiral_fbx)
    ax = info.axes
    # Blender 4.5 binary FBX 7.4 with axis_up=Y, axis_forward=-Z declares:
    assert ax.up_axis == 1 and ax.up_axis_sign == 1          # +Y up
    assert ax.front_axis == 2 and ax.front_axis_sign == 1    # +Z front
    assert ax.coord_axis == 0 and ax.coord_axis_sign == 1    # +X right
    assert ax.unit_scale_factor == pytest.approx(1.0)        # FBX native unit (cm)
    assert info.version == 7400                              # Blender writes FBX 7.4
    assert "Blender" in info.creator


# ── Chirality: independent parses vs the analytic build (amendments 1 + 2) ──


def test_chiral_fbx_world_space_no_handedness_flip(chiral_fbx):
    """Independent FBX read (GlobalSettings + Model transforms + raw verts,
    cm normalised to metres) vs the analytic Blender-space cloud: exactly
    one signed permutation matches and it must have det == +1 — a mirrored
    export would only match det == -1. No convention is assumed: the
    mapping is discovered, then checked. The discovered conversion is the
    Y-up one the header declares: blender(x,y,z) = file(x, z, -y)."""
    from src.client.fbx_inspect import read_fbx_info

    dst = read_fbx_info(chiral_fbx).world_vertices()
    assert len(dst) >= 16  # split-per-face vertices duplicate corners
    m = _unique_mapping(dst)
    assert m["det"] == 1, f"handedness flip: only det==-1 mappings matched ({m['description']})"
    assert m["scale"] == pytest.approx(1.0, rel=0.01)  # metres in, metres out
    assert m["residual"] < 1e-3
    assert m["description"] == "blender(x,y,z) = file(x, z, -y)"


def test_chiral_fbx_boss_lands_on_correct_side(chiral_fbx):
    """The asymmetric feature must be where we built it (+X +Y) when the
    deliverable FBX is read back through the DISCOVERED mapping."""
    from src.client.fbx_inspect import read_fbx_info

    dst = read_fbx_info(chiral_fbx).world_vertices()
    _assert_boss_on_correct_side(_to_blender_space(dst, _unique_mapping(dst)), "FBX")


def test_discovered_mapping_agrees_with_declared_convention(chiral_fbx):
    """The mapping discovered from raw geometry must be the one the header
    declares: Blender's up (Z) maps onto the file's declared up axis with
    the declared sign, and Blender's forward (-Y) onto the declared front."""
    from src.client.fbx_inspect import read_fbx_info

    info = read_fbx_info(chiral_fbx)
    M = _unique_mapping(info.world_vertices())["matrix"]

    col_up = M[:, 2]  # image of Blender +Z under blender->file
    up_idx = int(np.argmax(np.abs(col_up)))
    assert up_idx == info.axes.up_axis
    assert np.sign(col_up[up_idx]) == info.axes.up_axis_sign

    col_fwd = -M[:, 1]  # Blender forward is -Y
    fwd_idx = int(np.argmax(np.abs(col_fwd)))
    assert fwd_idx == info.axes.front_axis
    assert np.sign(col_fwd[fwd_idx]) == info.axes.front_axis_sign


def test_chiral_glb_trimesh_cross_load(chiral_glb):
    """trimesh (non-Blender) reads the GLB deliverable: same unique det == +1
    mapping from the analytic cloud — the two independent reads of what we
    built agree — and the boss lands on the correct side after
    cross-loading. EMPIRICAL: trimesh reports the glTF file's NATIVE Y-up
    space (x, z_b, -y_b), not the pipeline's internal Z-up."""
    from src.agent.verifier import load_merged_mesh

    mesh = load_merged_mesh(str(chiral_glb))
    assert mesh is not None and len(mesh.vertices) > 0
    src = np.asarray(mesh.vertices, dtype=np.float64)

    m = _unique_mapping(src)
    assert m["det"] == 1
    assert m["scale"] == pytest.approx(1.0, rel=0.01)
    _assert_boss_on_correct_side(_to_blender_space(src, m), "GLB(trimesh)")


def test_chiral_glb_trimesh_extents(chiral_glb):
    """Cross-load extents in the glTF file's own Y-up space: height rides
    the Y axis (0.2794 m = 11 in). This is what a glTF consumer (their
    Babylon viewer) natively sees — NOT the pipeline's internal Z-up."""
    from src.agent.verifier import load_merged_mesh

    mesh = load_merged_mesh(str(chiral_glb))
    ext = mesh.extents
    assert ext[0] == pytest.approx(0.508, abs=1e-4)   # length on X
    assert ext[1] == pytest.approx(0.2794, abs=1e-4)  # HEIGHT on Y (glTF up)
    assert ext[2] == pytest.approx(0.3048, abs=1e-4)  # width on Z


def test_chiral_fbx_blender_reimport_bounds(runner, chiral_fbx):
    """Blender re-import (a real consumer of the file, though not proof of
    the convention — see above): exact extents, grounded, all triangles
    (the FBX came from the GLB, and glTF stores triangles only)."""
    r = runner.execute_op("topology_report", {"model_path": str(chiral_fbx)})
    assert r["success"], r.get("error")
    dims = r["bounds"]["dimensions"]
    assert dims[0] == pytest.approx(0.508, abs=1e-4)
    assert dims[1] == pytest.approx(0.3048, abs=1e-4)
    assert dims[2] == pytest.approx(0.2794, abs=1e-4)
    assert r["bounds"]["min"][2] == pytest.approx(0.0, abs=1e-4)
    assert r["ngons"] == 0
    assert r["triangles"] > 0 and r["quads"] == 0


# ── USDZ ────────────────────────────────────────────────────────────────────


def test_usdz_export_method_and_structure(chiral_usdz):
    """Empirical: does Blender 4.5 write .usdz directly (wm.usd_export) or
    does the harness fall back to the stored-zip path? Either way the file
    must be a valid uncompressed zip with a USD layer — recorded, not
    assumed. The method + structure land in PROGRESS.md."""
    from src.client.package import usdz_structure_report

    path, result = chiral_usdz
    assert path.is_file()
    assert result["method"] in ("direct", "zip-fallback"), result
    report = usdz_structure_report(path)
    assert report["exists"] is True
    assert "error" not in report, report.get("error")
    assert report["members"], "USDZ must contain at least one member"
    assert report["compressed"] is False, "USDZ entries must be stored (uncompressed)"
    assert report["has_layer"] is True, "USDZ must contain a USD layer"


# ── Golden-benchmark e2e: package -> validate (T2 exit criterion) ───────────


@pytest.fixture(scope="module")
def coffee_glb(runner, tmp_path_factory):
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import ObjectSpec

    spec = ObjectSpec.model_validate(
        json.loads((PROJECT_ROOT / "input/benchmarks/coffee_table.spec.json").read_text(encoding="utf-8"))
    )
    out = tmp_path_factory.mktemp("coffee") / "coffee_table.glb"
    result = runner.execute_op(
        "build_from_spec", resolve_spec_to_build_params(spec, output_glb_path=str(out)))
    assert result["success"], result.get("error")
    return out


def test_package_cli_coffee_table_end_to_end(runner, coffee_glb, tmp_path):
    """T2 exit criterion: the golden coffee-table benchmark goes through the
    full `package` CLI (all six gates green, qa_report written, placeholders
    flagged) and the resulting package independently passes `validate`."""
    from typer.testing import CliRunner

    from src.cli import app as cli_app

    job_yaml = tmp_path / "job.yaml"
    job_yaml.write_text(yaml.safe_dump({
        "job_code": "COFFEE0001",
        "dims": dict(COFFEE_DIMS_IN, unit="in"),
        "complexity": "simple",
        "orientation": "floor",
        "product_class": "coffee_table",
        "part_scope": "golden benchmark e2e",
        "reference_dir": "input/benchmarks",
    }), encoding="utf-8")

    out_root = tmp_path / "packages"
    result = CliRunner().invoke(cli_app, [
        "package", str(coffee_glb), "--job", str(job_yaml), "--out-root", str(out_root)])
    assert result.exit_code == 0, result.output
    assert "ALL GATES PASSED" in result.output
    assert "Placeholder Warning" in result.output  # T3 placeholders, loud

    package_dir = out_root / "COFFEE0001"
    report = json.loads((package_dir / "qa_report.json").read_text(encoding="utf-8"))
    assert report["all_passed"] is True
    assert report["placeholders"]["lp_hp_single_source"] is True
    assert report["axis_convention"]["written"]["up_axis"] == "y"
    assert report["cross_checks"]["agree"] is True
    assert report["usdz"]["method"] in ("direct", "zip-fallback")

    # Independent re-validation of the assembled package on disk.
    revalidate = CliRunner().invoke(cli_app, ["validate", str(package_dir), "--job", str(job_yaml)])
    assert revalidate.exit_code == 0, revalidate.output
    assert "ALL GATES PASSED" in revalidate.output
