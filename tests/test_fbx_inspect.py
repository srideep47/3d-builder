"""Pure tests for the independent binary-FBX reader (src/client/fbx_inspect.py).

The writer/parser round trip here is the parser's unit test; the
Blender-marked tests in test_client_export.py then run the same parser
against real Blender output. The chirality machinery (find_axis_mapping) is
exercised with synthetic proper/improper transforms — a mirror must only
match det == -1 mappings (owner amendment 2).
"""

from pathlib import Path

import numpy as np
import pytest

from src.client.fbx_inspect import (
    FbxAxes,
    box_corner_cloud,
    build_minimal_fbx,
    find_axis_mapping,
    read_fbx_info,
)

# Keep in sync with input/fixtures/chiral_test.spec.json (20 x 12 x 11 in
# base + offset boss; exact inch multiples).
CHIRAL_BOXES = [
    ([0.508, 0.3048, 0.2286], [0.0, 0.0, 0.1143]),
    ([0.1016, 0.1016, 0.0508], [0.127, 0.0762, 0.254]),
]


@pytest.fixture
def expected_cloud() -> np.ndarray:
    return box_corner_cloud(CHIRAL_BOXES)


# ── Parser: header + GlobalSettings ──────────────────────────────────────────


def test_parses_version_creator_and_axes(tmp_path):
    axes = FbxAxes(up_axis=1, up_axis_sign=1, front_axis=2, front_axis_sign=-1,
                   coord_axis=0, coord_axis_sign=1, unit_scale_factor=2.5)
    p = tmp_path / "a.fbx"
    p.write_bytes(build_minimal_fbx(axes=axes, creator="unit-test-writer", version=7400))
    info = read_fbx_info(p)
    assert info.version == 7400
    assert info.creator == "unit-test-writer"
    assert info.axes.to_dict() == {
        "up_axis": "y", "up_axis_sign": 1,
        "front_axis": "z", "front_axis_sign": -1,
        "coord_axis": "x", "coord_axis_sign": 1,
        "unit_scale_factor": 2.5,
    }


def test_parses_version_7500_layout(tmp_path):
    """FBX >= 7500 uses 64-bit node headers — must not be misread."""
    p = tmp_path / "b.fbx"
    p.write_bytes(build_minimal_fbx(version=7500))
    info = read_fbx_info(p)
    assert info.version == 7500
    assert info.axes.up_axis == 1


def test_rejects_non_fbx(tmp_path):
    p = tmp_path / "garbage.fbx"
    p.write_bytes(b"this is not an fbx file, not even close")
    with pytest.raises(ValueError, match="not a binary FBX"):
        read_fbx_info(p)


# ── Parser: geometry ─────────────────────────────────────────────────────────


def test_parses_geometry_and_polygon_sizes(tmp_path):
    # one quad (verts 0-3) + one 5-gon (verts 4-8); the FBX PolygonVertexIndex
    # encodes each polygon's LAST index bit-inverted (negative).
    vertices = [
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [2, 0, 0], [3, 0, 0], [3.5, 1, 0], [2.5, 1.5, 0], [2, 1, 0],
    ]
    pvi = [0, 1, 2, ~3, 4, 5, 6, 7, ~8]
    p = tmp_path / "geo.fbx"
    p.write_bytes(build_minimal_fbx(vertices=vertices, polygon_vertex_index=pvi))
    info = read_fbx_info(p)
    assert len(info.geometries) == 1
    g = info.geometries[0]
    assert g.vertices.shape == (9, 3)
    assert g.polygon_sizes == [4, 5]
    assert info.ngon_count() == 1
    assert info.faces_total() == 2
    assert info.triangle_equivalent() == 5  # (4-2) + (5-2)


# ── Chirality machinery ──────────────────────────────────────────────────────


def test_find_axis_mapping_matches_proper_rotation(expected_cloud):
    # a known proper rotation, e.g. Blender Z-up -> FBX Y-up: (x, y, z) -> (x, z, -y)
    file_pts = expected_cloud[:, [0, 2, 1]] * np.array([1.0, 1.0, -1.0])
    matches = find_axis_mapping(file_pts, expected_cloud)
    assert len(matches) == 1
    assert matches[0]["det"] == 1
    assert matches[0]["scale"] == pytest.approx(1.0)
    # discovered mapping is the INVERSE of the applied rotation:
    # file = (x_b, z_b, -y_b)  =>  blender = (x_f, -z_f, y_f)
    assert matches[0]["description"] == "blender(x,y,z) = file(x, -z, y)"


def test_find_axis_mapping_detects_mirror(expected_cloud):
    """A mirrored asset matches ONLY det == -1 mappings — the caller asserts
    det == +1 and the mirror is caught (owner amendment 2)."""
    mirrored = expected_cloud * np.array([1.0, 1.0, -1.0])
    matches = find_axis_mapping(mirrored, expected_cloud)
    assert len(matches) == 1
    assert matches[0]["det"] == -1


def test_find_axis_mapping_allows_scale_and_translation(expected_cloud):
    scaled = expected_cloud * 100.0 + np.array([5.0, -3.0, 2.0])
    matches = find_axis_mapping(scaled, expected_cloud)
    assert len(matches) == 1
    assert matches[0]["det"] == 1
    assert matches[0]["scale"] == pytest.approx(0.01)


def test_find_axis_mapping_rejects_wrong_cloud(expected_cloud):
    """A different SHAPE must not match at all. (Moving the boss to the
    opposite corner is NOT a valid 'wrong' fixture — that is a proper 180°
    rotation, legitimately matchable. Changing the boss size changes the
    extent ratios, which no uniform-scale permutation can explain.)"""
    wrong = box_corner_cloud([
        CHIRAL_BOXES[0],
        ([0.1524, 0.1016, 0.0508], [0.127, 0.0762, 0.254]),  # wider boss
    ])
    assert find_axis_mapping(wrong, expected_cloud) == []


# ── World-space resolution (Model transforms + Connections + units) ─────────


def _write_fbx_with_model(tmp_path, vertices, models, axes, name="world.fbx"):
    p = tmp_path / name
    p.write_bytes(build_minimal_fbx(
        axes=axes, vertices=vertices, polygon_vertex_index=[0, 1, 2, -4],
        models=models, geometry_uid=5001))
    return p


def test_world_vertices_applies_model_translation(tmp_path):
    """Geometry vertices + connected Model's LclTranslation, in file units
    (UnitScaleFactor 100 => one file unit is a metre)."""
    axes = FbxAxes(1, 1, 2, 1, 0, 1, 100.0)
    p = _write_fbx_with_model(
        tmp_path, [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
        [{"uid": 1001, "name": "m", "translation": (10.0, 20.0, 30.0), "geometry_uid": 5001}],
        axes)
    world = read_fbx_info(p).world_vertices()
    assert np.allclose(world, [[10, 20, 30], [11, 20, 30], [10, 21, 30], [10, 20, 31]])


def test_world_vertices_normalises_centimetres(tmp_path):
    """FBX's native unit is the centimetre: UnitScaleFactor 1.0 means the
    values ARE centimetres (Blender's exporter writes exactly this)."""
    axes = FbxAxes(1, 1, 2, 1, 0, 1, 1.0)
    p = _write_fbx_with_model(
        tmp_path, [[0, 0, 0], [100, 0, 0], [0, 100, 0], [0, 0, 100]],
        [{"uid": 1001, "name": "m", "translation": (254.0, 0.0, 0.0), "geometry_uid": 5001}],
        axes)
    world = read_fbx_info(p).world_vertices()
    assert np.allclose(world, [[2.54, 0, 0], [3.54, 0, 0], [2.54, 1.0, 0], [2.54, 0, 1.0]])
    # raw mode keeps file units untouched
    raw = read_fbx_info(p).world_vertices(in_metres=False)
    assert raw[0] == pytest.approx([254.0, 0.0, 0.0])


def test_world_vertices_composes_parent_chain_and_rotation(tmp_path):
    """FBX local transform is T @ R @ S, world = parent_world @ local
    (InheritType RrSs): a child translated (1, 0, 0) under a parent rotated
    90 deg about Z has its translation rotated too - a vertex at the child
    origin lands at (0, 1, 0), and (1, 0, 0) lands at (0, 2, 0)."""
    axes = FbxAxes(1, 1, 2, 1, 0, 1, 100.0)
    p = _write_fbx_with_model(
        tmp_path, [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
        [{"uid": 1001, "name": "parent", "rotation": (0.0, 0.0, 90.0)},
         {"uid": 1002, "name": "child", "translation": (1.0, 0.0, 0.0),
          "parent_uid": 1001, "geometry_uid": 5001}],
        axes)
    world = read_fbx_info(p).world_vertices()
    assert np.allclose(world, [[0, 1, 0], [0, 2, 0], [-1, 1, 0], [0, 1, 1]])


def test_world_vertices_without_models_returns_raw(tmp_path):
    """No Models/Connections (e.g. the minimal writer's plain form): the
    geometry is already in file space — identity transform."""
    axes = FbxAxes(1, 1, 2, 1, 0, 1, 100.0)
    p = tmp_path / "raw.fbx"
    p.write_bytes(build_minimal_fbx(
        axes=axes, vertices=[[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]],
        polygon_vertex_index=[0, 1, 2, -4]))
    info = read_fbx_info(p)
    assert info.models == {} and info.connections == []
    assert np.allclose(info.world_vertices(), [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]])


def test_world_extents_sorted_descending(tmp_path):
    axes = FbxAxes(1, 1, 2, 1, 0, 1, 100.0)
    p = _write_fbx_with_model(
        tmp_path, [[0, 0, 0], [0.2, 0, 0], [0, 0.1, 0], [0, 0, 0.3]],
        [{"uid": 1001, "name": "m", "translation": (0.0, 0.0, 0.0), "geometry_uid": 5001}],
        axes)
    assert read_fbx_info(p).world_extents_m() == pytest.approx([0.3, 0.2, 0.1])
