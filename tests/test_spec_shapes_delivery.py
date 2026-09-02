"""Spec-flow shape vocabulary vs the delivery n-gon gate (blender-marked;
auto-skip without Blender).

Phase 6 cold-path defect, found by the first live end-to-end package: the
analyst's spec built 10 cylinders whose primitive NGON caps put 20 n-gons in
the delivery scene — `prepare_delivery_scene` refuses (client gate: 0 n-gons,
strict) and the package dies after the build already converged. The shape
builders must be n-gon-free by construction:

- cylinder / tapered_cylinder / cone caps are TRIFAN fills (same
  triangle-equivalent count as the n-gon they replace — the tri ceiling is
  unaffected);
- extrude parts default to fan caps at the schema level.

The TRIFAN change exposed a second cold-path defect, pinned here too: the
EXACT boolean solver emits coincident-but-distinct vertex pairs joined by
zero-length edges when it cuts through a fan-cap ring. apply_boolean now
dissolves them (_weld_solver_duplicates) — otherwise glTF tessellation
ships zero-area triangles whose welded edges read non-manifold.
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.blender

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def runner():
    from src.blender.locate import locate_blender
    from src.blender.runner import BlenderRunner

    if locate_blender() is None:
        pytest.skip("Blender 3.3+ not found on this machine")
    return BlenderRunner()


@pytest.fixture(scope="module")
def objects(runner, tmp_path_factory):
    """One spec exercising the cap-bearing shapes from the analyst vocabulary,
    built into a live .blend (quads preserved — a GLB round trip would
    triangulate and make the n-gon gate vacuous)."""
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import ObjectSpec, PartSpec, ShapeType

    spec = ObjectSpec(
        name="shape_vocab_ngon_probe",
        parts=[
            PartSpec(name="pole", shape=ShapeType.CYLINDER,
                     dimensions=[0.05, 0.05, 1.0], position=[0, 0, 0.5]),
            PartSpec(name="tip", shape=ShapeType.CONE,
                     dimensions=[0.1, 0.1, 0.2], position=[0, 0, 1.1]),
            PartSpec(name="taper", shape=ShapeType.TAPERED_CYLINDER,
                     dimensions=[0.1, 0.1, 0.3], top_scale=[0.5, 0.5],
                     position=[0.3, 0, 0.15]),
            PartSpec(name="ball", shape=ShapeType.SPHERE,
                     dimensions=[0.1, 0.1, 0.1], position=[-0.3, 0, 0.05]),
            PartSpec(name="rib", shape=ShapeType.EXTRUDE,
                     dimensions=[0.1, 0.1, 0.4],
                     profile_points=[[0.05, 0], [0.05, 0.05], [0.0, 0.05],
                                     [-0.05, 0.05], [-0.05, -0.05], [0.05, -0.05]],
                     position=[0, 0.3, 0], position_mode="base"),
        ],
    )
    blend = tmp_path_factory.mktemp("ngon_probe") / "probe.blend"
    res = runner.execute_op(
        "build_from_spec",
        resolve_spec_to_build_params(spec, output_glb_path=str(blend)))
    assert res["success"], res.get("error")

    code = r"""
import bpy
from mathutils import Vector
bpy.context.view_layer.update()
out = {}
for o in bpy.data.objects:
    if o.type != "MESH":
        continue
    me = o.data
    ngon = sum(1 for p in me.polygons if len(p.vertices) > 4)
    use = {}
    for p in me.polygons:
        for e in p.edge_keys:
            use[e] = use.get(e, 0) + 1
    out[o.name] = {
        "ngon": ngon,
        "faces": len(me.polygons),
        "boundary_edges": sum(1 for n in use.values() if n == 1),
        "nonmanifold_edges": sum(1 for n in use.values() if n > 2),
    }
RESULT = out
"""
    res = runner.execute_op("run_script", {"code": code, "input": str(blend)})
    assert res["success"], res.get("error")
    return res["result"]


def test_every_vocabulary_shape_is_ngon_free(objects):
    """The Phase 6 defect: cylinder/cone NGON caps. TRIFAN fills and fan-cap
    extrudes keep the strict delivery gate green by construction."""
    assert set(objects) == {"pole", "tip", "taper", "ball", "rib"}
    for name, o in objects.items():
        assert o["ngon"] == 0, (name, o["ngon"])
        assert o["faces"] > 0, name


def test_every_vocabulary_shape_is_a_closed_solid(objects):
    for name, o in objects.items():
        assert o["boundary_edges"] == 0, (name, o["boundary_edges"])
        assert o["nonmanifold_edges"] == 0, (name, o["nonmanifold_edges"])


@pytest.fixture(scope="module")
def boolean_glb(runner, tmp_path_factory):
    """Mug-geometry boolean: TRIFAN-cap cylinder minus a TRIFAN-cap cut whose
    cap ring sits inside the body. This is the shape whose EXACT-solver output
    carried 24 coincident-but-distinct vertex pairs joined by zero-length
    edges — edge-closed live, but glTF tessellation turned them into 48
    zero-area triangles whose welded edges read non-manifold (the coffee_mug
    golden benchmark regression after the TRIFAN cap change)."""
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import (BooleanModifier, Modifiers, ObjectSpec,
                                 PartSpec, ShapeType)

    spec = ObjectSpec(
        name="boolean_weld_probe",
        parts=[
            PartSpec(name="body", shape=ShapeType.CYLINDER,
                     dimensions=[0.095, 0.095, 0.1], position=[0, 0, 0.05],
                     modifiers=Modifiers(boolean=BooleanModifier(
                         operation="difference", target_part="cavity"))),
            PartSpec(name="cavity", shape=ShapeType.CYLINDER,
                     dimensions=[0.083, 0.083, 0.088], position=[0, 0, 0.058]),
        ],
    )
    glb = tmp_path_factory.mktemp("boolean_probe") / "probe.glb"
    res = runner.execute_op(
        "build_from_spec",
        resolve_spec_to_build_params(spec, output_glb_path=str(glb)))
    assert res["success"], res.get("error")
    assert res["parts_created"] == 1  # the cavity tool is consumed by the difference
    return glb


def test_boolean_result_welds_watertight_after_gltf(boolean_glb):
    """The regression pin: the merged process=True mesh must be watertight —
    _weld_solver_duplicates collapses the solver's zero-length edges before
    export instead of leaving them to become degenerate triangles."""
    from src.agent.verifier import load_merged_mesh

    m = load_merged_mesh(boolean_glb)
    assert m is not None
    assert m.is_watertight, "boolean output must weld watertight after glTF"


def test_boolean_result_exports_no_zero_area_faces(boolean_glb):
    """Zero-area triangles are invisible to the n-gon gate but corrupt every
    welded edge-degree check; none may ship."""
    import numpy as np

    from src.agent.verifier import load_merged_mesh

    m = load_merged_mesh(boolean_glb)
    assert m is not None
    degenerate = int((np.asarray(m.area_faces) < 1e-12).sum())
    assert degenerate == 0, f"{degenerate} zero-area faces in exported GLB"
