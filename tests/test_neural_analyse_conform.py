"""Neural analyse (§4.3) + conform (§4.4) tests — measured-fact gates on
generated GLBs, the S1 aspect refusal, the packed metallic-roughness
split, and the conform ObjectSpec contract. All synthetic trimesh
fixtures; no GPU, no Blender, no network.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image

from src.client.job import JobCard, JobDims
from src.neural.analyse import NeuralAnalyseReport, analyse_neural_mesh, aspect_deviation
from src.neural.conform import (
    ConformRefusal,
    build_conform_spec,
    check_aspect,
    split_packed_maps,
)
from src.spec.schema import GenerationMethod


def _card(length=0.8, width=0.6, height=0.12, unit="M", ceiling=None) -> JobCard:
    return JobCard(
        job_code="NEUR0001",
        dims=JobDims(length=length, width=width, height=height, unit=unit),
        product_class="test",
        complexity="simple",
        orientation="floor",
        reference_dir="input/reference",
        polycount_ceiling=ceiling,
    )


def _box_glb(path: Path, extents, texture: bool = False, metallic: float = 0.34):
    box = trimesh.creation.box(extents=extents)
    if texture:
        mr = Image.new("RGBA", (32, 32), (0, 190, int(metallic * 255), 255))
        albedo = Image.new("RGBA", (32, 32), (200, 180, 160, 255))
        mat = trimesh.visual.material.PBRMaterial(
            baseColorTexture=albedo, metallicRoughnessTexture=mr
        )
        box.visual = trimesh.visual.TextureVisuals(
            uv=np.zeros((len(box.vertices), 2)), material=mat
        )
    box.export(path)
    return path


def _two_body_glb(path: Path) -> Path:
    """Nested shells — the actual neural multi-shell case: a small body
    inside a bigger one, combined extents unchanged."""
    outer = trimesh.creation.box(extents=(0.8, 0.6, 0.12))
    inner = trimesh.creation.box(extents=(0.2, 0.2, 0.05)).apply_translation([0, 0, 0.03])
    trimesh.util.concatenate([outer, inner]).export(path)
    return path


def _open_glb(path: Path) -> Path:
    box = trimesh.creation.box(extents=(0.8, 0.6, 0.12))
    faces = box.faces[: len(box.faces) // 2]  # half the faces → open edges
    trimesh.Trimesh(vertices=box.vertices, faces=faces, process=False).export(path)
    return path


# ── analyse: the §4.3 measured-fact table ────────────────────────────────────


def test_analyse_clean_box_passes(tmp_path):
    glb = _box_glb(tmp_path / "clean.glb", (0.8, 0.6, 0.12))
    report = analyse_neural_mesh(glb, _card())
    assert report.passed, report.to_dict()
    by_name = {c["name"]: c for c in report.checks}
    assert by_name["triangles"]["value"] == 12
    assert by_name["open_edges_after_position_merge"]["value"] == 0
    assert by_name["bodies"]["value"] == 1
    assert by_name["aspect_ratio"]["passed"]
    # no textures on this fixture: maps recorded absent, metallic unknown,
    # nothing about maps or (undeclared) fabric gates
    assert report.maps["albedo"]["present"] is False
    assert report.metallic is None
    assert by_name["metallic_fabric"]["gating"] is False


def test_analyse_square_mesh_against_rectangular_card_fails_aspect(tmp_path):
    """The square-mattress case (§3.1): measured 1:0.999 vs card 1:0.75."""
    glb = _box_glb(tmp_path / "square.glb", (0.8, 0.8, 0.8))
    report = analyse_neural_mesh(glb, _card())
    assert not report.passed
    failed = [c["name"] for c in report.failed_checks()]
    assert failed == ["aspect_ratio"]
    aspect = next(c for c in report.checks if c["name"] == "aspect_ratio")
    assert aspect["value"]["measured_ratio"] == [1.0, 1.0, 1.0]
    assert aspect["value"]["card_ratio"] == [1.0, 0.75, 0.15]


def test_analyse_open_edges_fail_closed(tmp_path):
    glb = _open_glb(tmp_path / "open.glb")
    report = analyse_neural_mesh(glb, _card())
    by_name = {c["name"]: c for c in report.checks}
    assert by_name["open_edges_after_position_merge"]["value"] > 0
    assert not by_name["open_edges_after_position_merge"]["passed"]
    assert not report.passed


def test_analyse_records_multiple_bodies_without_gating(tmp_path):
    glb = _two_body_glb(tmp_path / "two.glb")
    report = analyse_neural_mesh(glb, _card())
    by_name = {c["name"]: c for c in report.checks}
    assert by_name["bodies"]["value"] == 2
    assert by_name["bodies"]["gating"] is False
    assert report.passed  # consolidation is conform's job, not a stop here


def test_analyse_triangles_vs_ceiling(tmp_path):
    glb = _box_glb(tmp_path / "box.glb", (0.8, 0.6, 0.12))
    report = analyse_neural_mesh(glb, _card(ceiling=10))
    by_name = {c["name"]: c for c in report.checks}
    assert by_name["triangles"]["gating"] and not by_name["triangles"]["passed"]
    assert not report.passed


def test_analyse_no_ceiling_records_without_gating(tmp_path):
    glb = _box_glb(tmp_path / "box.glb", (0.8, 0.6, 0.12))
    card = _card()
    card.complexity = "complex"  # no tier ceiling, no card ceiling → unknown
    report = analyse_neural_mesh(glb, card)
    by_name = {c["name"]: c for c in report.checks}
    assert by_name["triangles"]["gating"] is False
    assert by_name["triangles"]["threshold"] is None


def test_analyse_metallic_gate_on_declared_fabric(tmp_path):
    """§3.6: the mattress came back 34% metallic on fabric — the gate that
    catches it, and the record-only path when fabric is not declared."""
    glb = _box_glb(tmp_path / "tex.glb", (0.8, 0.6, 0.12), texture=True, metallic=0.34)
    report = analyse_neural_mesh(glb, _card(), declared_fabric=True)
    by_name = {c["name"]: c for c in report.checks}
    assert by_name["metallic_fabric"]["gating"]
    assert not by_name["metallic_fabric"]["passed"]
    assert report.metallic == pytest.approx(0.34, abs=0.01)
    assert not report.passed

    report2 = analyse_neural_mesh(glb, _card(), declared_fabric=False)
    by_name2 = {c["name"]: c for c in report2.checks}
    assert not by_name2["metallic_fabric"]["gating"]
    assert report2.passed


def test_analyse_maps_recorded_with_resolution(tmp_path):
    """§6 discipline: which of the five maps exist, at what resolution —
    never 'textures working' because a GLB got bigger."""
    glb = _box_glb(tmp_path / "tex.glb", (0.8, 0.6, 0.12), texture=True)
    report = analyse_neural_mesh(glb, _card())
    assert report.maps["albedo"] == {"present": True, "resolution": [32, 32]}
    assert report.maps["roughness"]["present"] is True
    assert report.maps["metallic"]["present"] is True
    # TRELLIS supplies 3 of 5 (§3.2): normal + AO absent, recorded not gated
    assert report.maps["normal"]["present"] is False
    assert report.maps["ao"]["present"] is False
    maps_check = next(c for c in report.checks if c["name"] == "maps_present")
    assert maps_check["gating"] is False


def test_aspect_deviation_helper():
    assert aspect_deviation([1, 1, 1], [1, 1, 1]) is not None
    assert abs(aspect_deviation([1, 1, 1], [1, 1, 1]).max()) < 1e-9
    assert aspect_deviation([0, 0, 0], [1, 1, 1]) is None
    dev = aspect_deviation([1.0, 0.999, 0.15], [1.0, 0.75, 0.15])
    assert dev[1] == pytest.approx((0.999 / 1.0 - 0.75) / 0.75, rel=0.01)


# ── conform: S1 refusal, sizing, retopology, material ────────────────────────


def test_check_aspect_ok_and_refusal():
    ok, msg = check_aspect([0.8, 0.6, 0.12], _card())
    assert ok and "within tolerance" in msg
    bad, msg = check_aspect([0.8, 0.8, 0.8], _card())
    assert not bad and "REFUSED (S1)" in msg and "per-axis" in msg


def test_conform_builds_spec_for_matching_mesh(tmp_path):
    glb = _box_glb(tmp_path / "clean.glb", (0.4, 0.3, 0.06))  # half-scale, same aspect
    spec, decisions = build_conform_spec(glb, _card())
    part = spec.parts[0]
    assert part.method == GenerationMethod.IMAGE_TO_3D
    assert Path(part.mesh_path).is_absolute()
    assert Path(part.mesh_path).exists()
    # card axis map L→X, W→Y, H→Z in metres
    assert part.target_size == [0.8, 0.6, 0.12]
    # voxel retopology in the measured-safe band, consolidating + re-quading
    assert part.retopology.tool == "voxel"
    assert 0.005 <= part.retopology.voxel_size <= 0.02
    assert "consolidates" in decisions["retopology"]
    assert "REFUSED" not in decisions["aspect"]


def test_conform_refuses_aspect_mismatch(tmp_path):
    """S1: refuse and report — never quietly distort to the card."""
    glb = _box_glb(tmp_path / "square.glb", (0.8, 0.8, 0.8))
    with pytest.raises(ConformRefusal, match=r"REFUSED \(S1\)"):
        build_conform_spec(glb, _card())


def test_conform_requad_option_uses_quadriflow(tmp_path):
    glb = _box_glb(tmp_path / "clean.glb", (0.8, 0.6, 0.12))
    spec, decisions = build_conform_spec(glb, _card(ceiling=25000), requad=True)
    assert spec.parts[0].retopology.tool == "quadriflow"
    assert spec.parts[0].retopology.target_faces == 25000
    assert "does NOT consolidate" in decisions["retopology"]


def test_conform_voxel_size_clamped_to_measured_band(tmp_path):
    # smallest axis 0.10 m → 0.10/64 = 0.0016 → clamped UP to the 0.005
    # hazard floor (docs/MESH_SOURCES.md §5.3: collapse at 0.004)
    glb = _box_glb(tmp_path / "thin.glb", (0.10, 0.10, 0.10))
    spec, _ = build_conform_spec(glb, _card(length=0.10, width=0.10, height=0.10))
    assert spec.parts[0].retopology.voxel_size == pytest.approx(0.005)


def test_conform_metallic_override_for_declared_fabric(tmp_path):
    glb = _box_glb(tmp_path / "tex.glb", (0.8, 0.6, 0.12), texture=True, metallic=0.34)
    report = analyse_neural_mesh(glb, _card())
    spec, decisions = build_conform_spec(
        glb, _card(), analyse_report=report, declared_fabric=True
    )
    assert spec.parts[0].material.metallic == 0.0
    assert "pinned to 0.0" in decisions["metallic"]

    spec2, _ = build_conform_spec(glb, _card(), analyse_report=report)
    assert spec2.parts[0].material.metallic == pytest.approx(0.34, abs=0.01)


def test_conform_missing_mesh_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_conform_spec(tmp_path / "nope.glb", _card())


# ── split_packed_maps (§4.4 step 5) ──────────────────────────────────────────


def test_split_packed_maps_writes_named_pngs(tmp_path):
    glb = _box_glb(tmp_path / "tex.glb", (0.8, 0.6, 0.12), texture=True, metallic=0.34)
    maps_dir = tmp_path / "maps"
    written = split_packed_maps(glb, maps_dir)
    assert set(written) == {"albedo", "roughness", "metallic"}
    for name, info in written.items():
        assert Path(info["path"]).is_file()
        assert info["resolution"] == [32, 32]
    # glTF convention: G = roughness, B = metallic — the split channels must
    # match the packed source values, not cross over
    rough = np.asarray(Image.open(maps_dir / "roughness.png"), dtype=np.float64) / 255.0
    metal = np.asarray(Image.open(maps_dir / "metallic.png"), dtype=np.float64) / 255.0
    assert rough.mean() == pytest.approx(190 / 255, abs=0.01)
    assert metal.mean() == pytest.approx(0.34, abs=0.01)


def test_split_packed_maps_no_textures_writes_nothing(tmp_path):
    glb = _box_glb(tmp_path / "bare.glb", (0.8, 0.6, 0.12))
    written = split_packed_maps(glb, tmp_path / "maps")
    assert written == {}


def test_conform_material_uses_split_maps_dir(tmp_path):
    glb = _box_glb(tmp_path / "tex.glb", (0.8, 0.6, 0.12), texture=True)
    maps_dir = tmp_path / "maps"
    split_packed_maps(glb, maps_dir)
    spec, decisions = build_conform_spec(glb, _card(), maps_dir=maps_dir)
    mat = spec.parts[0].material
    assert mat.texture_dir == str(maps_dir)
    assert mat.triplanar is False  # generated maps are not tileable scans
    assert decisions["maps_dir"] == str(maps_dir)
    assert "delivery HP→LP bake" in decisions["maps_note"]
