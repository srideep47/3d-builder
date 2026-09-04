"""Mesh-source contract (Phase 8 item 3) — pure (non-Blender) pins:

- the resolver threads every file-backed field (method, mesh_path,
  target_size in spec units → metres, mesh_scale) into build params, while
  parametric parts keep emitting byte-identical historic params (default
  fields omitted);
- the analyst and corrector prompts document the file-backed methods —
  the shape-enum/prompt/harness sync invariant extended to methods.
"""

import json

import pytest

from src.agent.prompts import ANALYST_SYSTEM_PROMPT, CORRECTOR_SYSTEM_PROMPT
from src.spec.resolver import resolve_spec_to_build_params
from src.spec.schema import ObjectSpec, PartSpec


def _imported_spec(**part_kwargs) -> ObjectSpec:
    part = {
        "name": "brought_in",
        "method": "imported",
        "shape": "organic",
        "mesh_path": "assets/cushion.glb",
        "target_size": [400.0, 400.0, 100.0],  # mm — spec units
        "dimensions": [400.0, 400.0, 100.0],
    }
    part.update(part_kwargs)
    return ObjectSpec.model_validate({
        "name": "contract",
        "units": "mm",
        "parts": [part],
    })


def test_resolver_threads_file_backed_fields():
    params = resolve_spec_to_build_params(
        _imported_spec(mesh_scale="uniform"), output_glb_path="out.glb")
    p = params["spec"]["parts"][0]
    assert p["method"] == "imported"
    assert p["mesh_path"] == "assets/cushion.glb"
    assert p["target_size"] == [0.4, 0.4, 0.1]  # mm → m at the boundary
    assert p["mesh_scale"] == "uniform"


def test_resolver_defaults_stay_omitted():
    """Historic params are byte-identical: a parametric part emits neither
    method nor mesh_scale; a 'fit' file-backed part omits mesh_scale."""
    params = resolve_spec_to_build_params(
        ObjectSpec.model_validate({"name": "m", "parts": [
            {"name": "body", "dimensions": [1.0, 1.0, 1.0]}]}),
        output_glb_path="out.glb")
    p = params["spec"]["parts"][0]
    assert "method" not in p
    assert "mesh_scale" not in p

    params = resolve_spec_to_build_params(
        _imported_spec(), output_glb_path="out.glb")  # mesh_scale default fit
    p = params["spec"]["parts"][0]
    assert p["method"] == "imported"
    assert "mesh_scale" not in p


def test_spec_json_round_trip_preserves_source():
    """A spec written to disk (run dirs, package --spec) and re-read keeps
    its mesh-source declaration whole — provenance survives the boundary."""
    spec = _imported_spec(mesh_scale="uniform")
    spec.parts[0].mesh_path = r"D:\assets\cushion.glb"
    revived = ObjectSpec.model_validate(json.loads(spec.model_dump_json()))
    p = revived.parts[0]
    assert p.method.value == "imported"
    assert p.mesh_path == r"D:\assets\cushion.glb"
    assert p.target_size == [400.0, 400.0, 100.0]
    assert p.mesh_scale == "uniform"


def test_prompts_document_file_backed_methods():
    """The analyst must know imported/scanned exist (else it can never use
    them); the corrector must know file-backed parts are fixed via
    target_size and must not drop the file fields on rewrite."""
    for token in ("imported", "scanned", "mesh_path", "mesh_scale", "uniform"):
        assert token in ANALYST_SYSTEM_PROMPT, token
    for token in ("imported", "scanned", "mesh_path", "mesh_scale"):
        assert token in CORRECTOR_SYSTEM_PROMPT, token
    # the analyst is told file units are never trusted (rule-9 spirit)
    assert "file\n  units are never trusted" in ANALYST_SYSTEM_PROMPT or \
        "file units are never trusted" in ANALYST_SYSTEM_PROMPT


# ── R2: the retopology block (Phase 8.5, docs/MESH_SOURCES.md §8) ─────────────

def test_resolver_threads_retology_block_with_unit_conversion():
    """The block rides into build params for the harness; voxel_size is a
    length in SPEC UNITS and converts like every other length (mm spec: 6
    -> 0.006 m), target_faces is a count and passes untouched."""
    params = resolve_spec_to_build_params(_imported_spec(
        retopology={"tool": "quadriflow", "target_faces": 8000}))
    assert params["spec"]["parts"][0]["retopology"] == {
        "tool": "quadriflow", "target_faces": 8000}

    params = resolve_spec_to_build_params(_imported_spec(
        retopology={"tool": "voxel", "voxel_size": 6.0}))  # mm
    assert params["spec"]["parts"][0]["retopology"] == {
        "tool": "voxel", "voxel_size": pytest.approx(0.006)}


def test_schema_refuses_retology_misuse():
    """Fail-closed at the schema: a tool without its parameter, an unknown
    tool, and retopology on a parametric part (born quad-clean — the block
    is for file-backed geometry only) are all refused."""
    for bad in ({"tool": "quadriflow"}, {"tool": "voxel"},
                {"tool": "magic", "target_faces": 1000}):
        with pytest.raises(Exception):
            PartSpec.model_validate({
                "name": "p", "method": "imported", "shape": "organic",
                "mesh_path": "a.glb", "target_size": [1, 1, 1],
                "retopology": bad,
            })
    # parametric part carrying the block
    with pytest.raises(Exception):
        ObjectSpec.model_validate({
            "name": "t",
            "parts": [{"name": "b", "shape": "box",
                       "dimensions": [0.1, 0.1, 0.1],
                       "retopology": {"tool": "quadriflow", "target_faces": 1000}}],
        })


def test_spec_json_round_trip_preserves_retology():
    """The block survives the run-dir boundary (spec.json re-read) — the
    corrector rewrites specs and must never silently lose the stage."""
    spec = _imported_spec(retopology={"tool": "voxel", "voxel_size": 6.0})
    revived = ObjectSpec.model_validate(json.loads(spec.model_dump_json()))
    assert revived.parts[0].retopology is not None
    assert revived.parts[0].retopology.tool == "voxel"
    assert revived.parts[0].retopology.voxel_size == 6.0


def test_prompts_document_retology():
    """The analyst must know the block exists (else dense scans can never be
    retopologized); the corrector's do-not-drop list must include it (a
    dropped stage is a silent polycount regression)."""
    for token in ("retopology", "quadriflow", "target_faces", "voxel"):
        assert token in ANALYST_SYSTEM_PROMPT, token
    assert "retopology" in CORRECTOR_SYSTEM_PROMPT
    assert '"retopology"' in CORRECTOR_SYSTEM_PROMPT
