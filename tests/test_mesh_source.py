"""Blender-marked tests for the mesh-source contract (Phase 8 item 3):
ONE mechanical path (import → join → rescale → place) behind which neural
image-to-3d, imported assets and scans all satisfy — only the provenance
(method value) differs.

The "owner asset" for these tests is a real exported GLB with a deliberately
non-uniform aspect (0.30 x 0.10 x 0.20 m box), so per-axis FIT (bbox lands
exactly on target_size) and UNIFORM (aspect preserved, min factor) are
distinguishable by direct measurement of the built bounds — never by
inspecting the code path that ran.
"""

from pathlib import Path

import pytest

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


@pytest.fixture(scope="module")
def asset_glb(runner, tmp_path_factory):
    """A deliberately non-uniform 'owner asset': 0.30 x 0.10 x 0.20 m box."""
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import ObjectSpec

    tmp = tmp_path_factory.mktemp("asset")
    out = tmp / "asset.glb"
    spec = ObjectSpec.model_validate({
        "name": "asset",
        "parts": [{
            "name": "asset",
            "shape": "box",
            "dimensions": [0.30, 0.10, 0.20],
            "position": [0.0, 0.0, 0.10],  # center-anchored box, grounded
        }],
    })
    result = runner.execute_op(
        "build_from_spec", resolve_spec_to_build_params(spec, output_glb_path=str(out)))
    assert result["success"], result.get("error")
    assert out.is_file()
    return out


def _build_file_backed(runner, tmp_dir, mesh_path, method="imported", target=None,
                       mesh_scale=None, shape=None, position=None, position_mode=None):
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import ObjectSpec

    target = target or [0.60, 0.20, 0.40]
    part = {
        "name": "brought_in",
        "method": method,
        "mesh_path": str(mesh_path),
        "target_size": list(target),
        "dimensions": list(target),
    }
    if shape:
        part["shape"] = shape
    if mesh_scale:
        part["mesh_scale"] = mesh_scale
    if position:
        part["position"] = list(position)
    if position_mode:
        part["position_mode"] = position_mode
    spec = ObjectSpec.model_validate({"name": "brings_its_own", "parts": [part]})
    out = tmp_dir / f"{method}_{mesh_scale or 'fit'}_{shape or 'default'}.glb"
    result = runner.execute_op(
        "build_from_spec", resolve_spec_to_build_params(spec, output_glb_path=str(out)))
    return result, out


def test_imported_part_fit_lands_exactly_on_target(runner, asset_glb, tmp_path):
    """Default mesh_scale 'fit': the imported bbox is rescaled per-axis onto
    target_size — 2x on every axis here, so bounds must read exactly
    [0.60, 0.20, 0.40] m."""
    result, _ = _build_file_backed(runner, tmp_path, asset_glb,
                                   target=[0.60, 0.20, 0.40])
    assert result["success"], result.get("error")
    assert result["warnings"] == []
    dims = result["overall_bounds"]["dimensions"]
    assert dims == pytest.approx([0.60, 0.20, 0.40], abs=1e-4)


def test_imported_part_uniform_preserves_aspect(runner, asset_glb, tmp_path):
    """mesh_scale 'uniform': one factor (min of the per-axis ratios) — the
    0.30 x 0.10 x 0.20 asset at target [0.6, 0.6, 0.6] has ratios
    [2.0, 6.0, 3.0], so the factor is 2.0: bounds read [0.60, 0.20, 0.40],
    no axis exceeds its target, and the asset's 3:1:2 aspect survives."""
    result, _ = _build_file_backed(runner, tmp_path, asset_glb,
                                   target=[0.60, 0.60, 0.60], mesh_scale="uniform")
    assert result["success"], result.get("error")
    assert result["warnings"] == []
    bounds = result["overall_bounds"]
    dims = bounds["dimensions"]
    # the most-constrained axis lands exactly on its target; none exceeds
    assert dims[0] == pytest.approx(0.60, abs=1e-4)
    assert all(d <= t + 1e-4 for d, t in zip(dims, [0.60, 0.60, 0.60]))
    # aspect preserved: same ratios as the source asset (3 : 1 : 2)
    assert dims[1] / dims[0] == pytest.approx(0.10 / 0.30, abs=1e-3)
    assert dims[2] / dims[0] == pytest.approx(0.20 / 0.30, abs=1e-3)


def test_imported_part_places_relative_to_assembly(runner, asset_glb, tmp_path):
    """File-backed parts honor the position contract like any other part —
    tested RELATIVELY, because op_build_from_spec's center_origin_bottom
    (assembly ground normalization) re-centers the whole model at origin
    afterwards, so absolute positions never survive a build. An anchor box
    occupies z 0..0.4; the imported part, position_mode 'base' at z=0.4,
    must sit exactly on top of it."""
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import ObjectSpec

    spec = ObjectSpec.model_validate({
        "name": "assembly",
        "parts": [
            {   # center-anchored box occupying z 0..0.4, x/y -0.2..0.2
                "name": "anchor", "shape": "box",
                "dimensions": [0.4, 0.4, 0.4], "position": [0.0, 0.0, 0.2],
            },
            {
                "name": "brought_in", "method": "imported",
                "mesh_path": str(asset_glb),
                "target_size": [0.60, 0.20, 0.40],
                "dimensions": [0.60, 0.20, 0.40],
                "position": [0.0, 0.0, 0.4], "position_mode": "base",
            },
        ],
    })
    out = tmp_path / "assembly.glb"
    result = runner.execute_op(
        "build_from_spec", resolve_spec_to_build_params(spec, output_glb_path=str(out)))
    assert result["success"], result.get("error")
    assert result["warnings"] == []

    # fresh process, per the one-Blender-process-per-op rule
    res = runner.execute_op("measure", {"model_path": str(out)})
    assert res["success"], res.get("error")
    parts = res["parts"]
    assert set(parts) == {"anchor", "brought_in"}
    assert parts["anchor"]["max"][2] == pytest.approx(0.4, abs=1e-4)
    # base mode: the imported part's bottom sits exactly on the anchor's top
    assert parts["brought_in"]["min"][2] == pytest.approx(0.4, abs=1e-4)
    assert parts["brought_in"]["max"][2] == pytest.approx(0.8, abs=1e-4)
    # and its own bounds still read the fit-rescaled target
    assert parts["brought_in"]["dimensions"] == pytest.approx([0.60, 0.20, 0.40], abs=1e-4)


def test_scanned_part_builds_through_the_same_path(runner, asset_glb, tmp_path):
    """method 'scanned' is provenance, not mechanics: same import path, same
    rescale contract (a scan file is indistinguishable from an asset file to
    the harness — retopology, Phase 8 item 4, is where provenance starts to
    matter)."""
    result, _ = _build_file_backed(runner, tmp_path, asset_glb, method="scanned",
                                   target=[0.60, 0.20, 0.40])
    assert result["success"], result.get("error")
    assert result["warnings"] == []
    dims = result["overall_bounds"]["dimensions"]
    assert dims == pytest.approx([0.60, 0.20, 0.40], abs=1e-4)


def test_organic_shape_with_authored_mesh_imports(runner, asset_glb, tmp_path):
    """An 'organic' part whose mesh exists as a file imports it — the
    file-backed dispatch is keyed on method, and an organic asset is
    legitimately imported rather than regenerated."""
    result, _ = _build_file_backed(runner, tmp_path, asset_glb,
                                   method="imported", shape="organic",
                                   target=[0.60, 0.20, 0.40])
    assert result["success"], result.get("error")
    assert result["warnings"] == []
    dims = result["overall_bounds"]["dimensions"]
    assert dims == pytest.approx([0.60, 0.20, 0.40], abs=1e-4)


def test_missing_mesh_file_skips_loudly_not_fatal(runner, tmp_path):
    """A file-backed part whose mesh file does not exist is SKIPPED with a
    warning naming the path — the build does not crash (the loop fires
    mesh_source_error and the gates fail honestly downstream)."""
    ghost = tmp_path / "does_not_exist.glb"
    result, _ = _build_file_backed(runner, tmp_path, ghost,
                                   target=[0.60, 0.20, 0.40])
    assert result["success"]  # the op survives; the part is absent
    assert result["parts_created"] == 0
    assert any("mesh file not found" in w for w in result["warnings"]), result["warnings"]
    assert any("does_not_exist.glb" in w for w in result["warnings"])
