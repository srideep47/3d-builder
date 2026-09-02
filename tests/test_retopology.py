"""Blender-marked tests for the retopology spec block (Phase 8.5 R2,
docs/MESH_SOURCES.md §7-8): an optional per-part stage applied by the
harness in the LIVE scene after the import weld (a GLB round trip would
triangulate the quads), before rescale (target_size still lands exact).

Every assertion is a direct mesh fact from the op result — the report is
the only observable evidence, because the export re-splits.
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
def holed_scan_glb(runner, tmp_path_factory):
    """The scan stand-in from the R1 tests: flat-shaded icosphere, one face
    deleted (the hole), exported GLB — imports fully split, welds to 162
    verts / 319 tris / 3 boundary edges."""
    tmp = tmp_path_factory.mktemp("retopo_scan")
    out = tmp / "scan.glb"
    code = f"""
import bpy, bmesh
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=0.5)
obj = bpy.context.active_object
bm = bmesh.new()
bm.from_mesh(obj.data)
bm.faces.ensure_lookup_table()
bm.faces.remove(bm.faces[0])
bm.to_mesh(obj.data)
bm.free()
bpy.ops.export_scene.gltf(filepath=r"{out}", export_format="GLB")
"""
    res = runner.execute_op("run_script", {"code": code})
    assert res["success"], res.get("error")
    return out


@pytest.fixture(scope="module")
def voxel_box_blend(runner, holed_scan_glb, tmp_path_factory):
    """The deterministic NO-OP input: the scan remeshed at voxel_size 20 m
    collapses to the single bounding box (8 verts / 6 quads, measured).
    Saved as .blend so the import preserves the quads — through a GLB the
    import triangulates (12 tris) and any re-remesh 'changes' the mesh,
    hiding the fixed point. Re-importing this .blend and remeshing at the
    SAME voxel size returns the box unchanged: the guard must fire."""
    tmp = tmp_path_factory.mktemp("retopo_noop")
    out = tmp / "box20.blend"
    code = f"""
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=r"{holed_scan_glb}")
obj = [o for o in bpy.data.objects if o.type == "MESH"][0]
mod = obj.modifiers.new(name="V", type="REMESH")
mod.mode = "VOXEL"; mod.voxel_size = 20.0; mod.adaptivity = 0.0
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
bpy.ops.object.modifier_apply(modifier=mod.name)
RESULT = {{"verts": len(obj.data.vertices), "faces": len(obj.data.polygons)}}
bpy.ops.wm.save_as_mainfile(filepath=r"{out}")
"""
    res = runner.execute_op("run_script", {"code": code})
    assert res["success"], res.get("error")
    assert res["result"] == {"verts": 8, "faces": 6}
    return out


def _scan_spec(mesh_path, retopo=None):
    part = {
        "name": "body", "method": "scanned", "mesh_path": str(mesh_path),
        "target_size": [0.60, 0.20, 0.40], "dimensions": [0.60, 0.20, 0.40],
    }
    if retopo:
        part["retopology"] = retopo
    return {"name": "scan", "parts": [part]}


def test_quadriflow_block_retopologizes_and_closes_hole(runner, holed_scan_glb, tmp_path):
    """{tool: quadriflow, target_faces: 800}: the welded scan (319 tris,
    3 boundary edges — the hole) becomes an all-quad manifold (~800 faces,
    measured 816; QuadriFlow closes small holes) and the rescale still
    lands bounds exactly on target_size."""
    result = runner.execute_op("build_from_spec", {
        "spec": _scan_spec(holed_scan_glb, {"tool": "quadriflow", "target_faces": 800}),
        "output_path": str(tmp_path / "quad.glb"),
    })
    assert result["success"], result.get("error")
    report = result["retopology"]["body"]
    assert report["success"] is True
    assert report["tool"] == "quadriflow"
    assert report["params"] == {"target_faces": 800}
    assert report["before"] == {"verts": 162, "faces": 319, "quads": 0,
                                "tris": 319, "boundary_edges": 3}
    after = report["after"]
    assert after["quads"] == after["faces"] and after["tris"] == 0  # all quads
    assert 600 <= after["faces"] <= 1000  # measured 816 (target honored ~10%)
    assert after["boundary_edges"] == 0  # hole closed, watertight
    dims = result["overall_bounds"]["dimensions"]
    assert dims == pytest.approx([0.60, 0.20, 0.40], abs=1e-4)


def test_voxel_block_is_density_control(runner, holed_scan_glb, tmp_path):
    """{tool: voxel, voxel_size: 0.05}: all-quad manifold at a density set
    by the voxel size (measured 1,834 quads), hole closed, bounds exact."""
    result = runner.execute_op("build_from_spec", {
        "spec": _scan_spec(holed_scan_glb, {"tool": "voxel", "voxel_size": 0.05}),
        "output_path": str(tmp_path / "voxel.glb"),
    })
    assert result["success"], result.get("error")
    report = result["retopology"]["body"]
    assert report["tool"] == "voxel"
    assert report["params"] == {"voxel_size": 0.05}
    after = report["after"]
    assert after["quads"] == after["faces"] and after["tris"] == 0
    assert 1200 <= after["faces"] <= 2500  # measured 1834
    assert after["boundary_edges"] == 0
    dims = result["overall_bounds"]["dimensions"]
    assert dims == pytest.approx([0.60, 0.20, 0.40], abs=1e-4)


def test_noop_guard_fails_closed(runner, voxel_box_blend):
    """The measured QuadriFlow hazard class (silent no-op, docs §5.2) made
    deterministic: the voxel-20 box re-imported from .blend (quads intact,
    weld a no-op) remeshed at the SAME voxel size is the exact fixed point
    — 8 verts / 6 faces unchanged. The op must FAIL with the no-op error
    (the runner surfaces failed ops as BlenderExecutionError), never
    silently pass the un-retopologized mesh downstream."""
    from src.blender.runner import BlenderExecutionError

    with pytest.raises(BlenderExecutionError, match="silent no-op") as excinfo:
        runner.execute_op("build_from_spec", {
            "spec": _scan_spec(voxel_box_blend, {"tool": "voxel", "voxel_size": 20.0}),
        })
    assert "8 verts" in str(excinfo.value)  # names the measured facts


def test_harness_refuses_retology_on_parametric_raw_params(runner):
    """The schema refuses retopology on parametric parts; a raw
    build-params caller bypassing Pydantic must get the same refusal from
    the harness, not a silently dropped stage."""
    from src.blender.runner import BlenderExecutionError

    with pytest.raises(BlenderExecutionError) as excinfo:
        runner.execute_op("build_from_spec", {"spec": {"parts": [{
            "name": "b", "shape": "box", "dimensions": [0.1, 0.1, 0.1],
            "retopology": {"tool": "quadriflow", "target_faces": 1000},
        }]}})
    msg = str(excinfo.value)
    assert "retopology" in msg and "parametric" in msg


def test_prepare_threads_retology_quads_into_t3(runner, holed_scan_glb):
    """E2E through the T3 finish chain: prepare_delivery_scene calls
    op_build_from_spec in-process, so the retopologized QUADS reach the
    n-gon gate, the shared atlas and the diagnostics — a scan delivers
    quad-clean (0 tris, 0 n-gons) at uniform texel density."""
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import ObjectSpec

    spec = ObjectSpec.model_validate({
        "name": "scan_finish",
        "parts": [{
            "name": "body", "method": "scanned",
            "mesh_path": str(holed_scan_glb),
            "target_size": [0.60, 0.20, 0.40],
            "dimensions": [0.60, 0.20, 0.40],
            "retopology": {"tool": "quadriflow", "target_faces": 800},
        }],
    })
    result = runner.execute_op(
        "prepare_delivery_scene", {"build": resolve_spec_to_build_params(spec)})
    assert result["success"], result.get("error")
    assert result["retopology"]["body"]["success"] is True
    topo = result["topology"]
    assert topo["ngons"] == 0
    assert topo["triangles"] == 0  # quads all the way into the finish chain
    assert topo["quads"] >= 600
    uv = result["uv"]
    assert uv["islands_total"] <= 20  # measured 7
    assert uv["overlapping_island_pairs"] == 0
    assert uv["in_bounds"] is True
    assert uv["texel_density_texels_per_m"]["ratio"] < 1.05
