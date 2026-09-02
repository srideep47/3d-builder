"""ObjectSpec v2 schema tests."""

import pytest
from pydantic import ValidationError

from src.spec.schema import (
    BevelModifier,
    LinearArrayModifier,
    Modifiers,
    ObjectSpec,
    PartSpec,
    RadialArrayModifier,
)
from src.spec.validation import validate_spec_structure


def test_minimal_valid_spec():
    spec = ObjectSpec(name="box", parts=[PartSpec(name="body", dimensions=[1.0, 1.0, 1.0])])
    assert spec.schema_name == "threed-objectspec"
    assert spec.schema_version == "2.0.0"
    assert spec.parts[0].shape.value == "rounded_box"  # default shape
    assert spec.parts[0].smooth_shade is False


def test_image_to_3d_part_fields():
    part = PartSpec(
        name="cushion",
        method="image_to_3d",
        image_crop="chair.jpg#cushion",
        target_size=[0.48, 0.46, 0.04],
    )
    spec = ObjectSpec(name="chair", parts=[part])
    assert spec.parts[0].method.value == "image_to_3d"
    assert spec.parts[0].mesh_path is None


def test_modifier_container_roundtrip():
    mods = Modifiers(
        bevel=BevelModifier(width=0.004, segments=3),
        radial_array=RadialArrayModifier(count=5, axis="z"),
        linear_array=LinearArrayModifier(count=3, spacing=0.09),
    )
    part = PartSpec(name="slat", dimensions=[0.5, 0.02, 0.1], modifiers=mods)
    assert part.modifiers.radial_array.count == 5
    assert part.modifiers.linear_array.direction == [0.0, 0.0, 1.0]


def test_position_mode_field():
    part = PartSpec(name="leg", shape="tapered_extrude", dimensions=[0.06, 0.06, 0.72], position_mode="base")
    assert part.position_mode == "base"


def test_structural_validation_catches_duplicates_and_bad_dims():
    spec = ObjectSpec(
        name="bad",
        parts=[
            PartSpec(name="a", dimensions=[1.0, 1.0, 1.0]),
            PartSpec(name="a", dimensions=[1.0, 1.0, 1.0]),
            PartSpec(name="b", dimensions=[0.0, 1.0, 1.0]),
        ],
    )
    errors = validate_spec_structure(spec)
    assert any("Duplicate part name" in e for e in errors)
    assert any("invalid dimensions" in e for e in errors)


# ── Mesh-source contract (Phase 8 item 3) ───────────────────────────────────


def test_imported_part_requires_mesh_path_and_target_size():
    """imported/scanned parts carry authored files: the file IS the geometry
    (mesh_path) and the size is owner-stated (target_size — file units are
    never trusted, the rule-9 spirit). Both missing = fail-closed."""
    with pytest.raises(ValidationError, match="mesh_path is required"):
        PartSpec(name="asset", method="imported", target_size=[0.3, 0.3, 0.2])
    with pytest.raises(ValidationError, match="target_size is required"):
        PartSpec(name="asset", method="imported", mesh_path="assets/foo.glb")
    with pytest.raises(ValidationError, match="mesh_path is required"):
        PartSpec(name="scan", method="scanned", target_size=[0.3, 0.3, 0.2])
    with pytest.raises(ValidationError, match="target_size is required"):
        PartSpec(name="scan", method="scanned", mesh_path="scans/foo.ply")


def test_parametric_part_rejects_mesh_path():
    """One part, one geometry source: a parametric/script part carrying
    mesh_path declares two sources and must be refused."""
    with pytest.raises(ValidationError, match="exactly one geometry source"):
        PartSpec(name="body", method="parametric", mesh_path="assets/foo.glb")
    with pytest.raises(ValidationError, match="exactly one geometry source"):
        PartSpec(name="body", method="custom_script", mesh_path="assets/foo.glb")


def test_source_entitled_fields_only():
    """image_crop belongs to image_to_3d; code belongs to custom_script —
    a part carrying another source's fields is lying about its source."""
    with pytest.raises(ValidationError, match="image_crop"):
        PartSpec(name="body", method="parametric", image_crop="photo.jpg#crop")
    with pytest.raises(ValidationError, match="code is executed only"):
        PartSpec(name="body", method="parametric", code="import bpy")


def test_file_backed_flag_and_mesh_scale():
    """is_file_backed is the contract's mechanical predicate; mesh_scale has
    exactly two modes and file-backed parts accept either."""
    from src.spec.schema import GenerationMethod

    for m in ("image_to_3d", "imported", "scanned"):
        assert GenerationMethod(m).is_file_backed is True
    for m in ("parametric", "custom_script"):
        assert GenerationMethod(m).is_file_backed is False

    part = PartSpec(
        name="scan", method="scanned",
        mesh_path="scans/foo.ply", target_size=[0.4, 0.4, 0.1],
        mesh_scale="uniform",
    )
    assert part.mesh_scale == "uniform"
    with pytest.raises(ValidationError):
        PartSpec(
            name="scan", method="scanned",
            mesh_path="scans/foo.ply", target_size=[0.4, 0.4, 0.1],
            mesh_scale="stretch",
        )


def test_target_size_must_be_positive_triple():
    with pytest.raises(ValidationError, match="3 positive values"):
        PartSpec(name="scan", method="scanned",
                 mesh_path="scans/foo.ply", target_size=[0.4, 0.0, 0.1])
    with pytest.raises(ValidationError, match="3 positive values"):
        PartSpec(name="scan", method="scanned",
                 mesh_path="scans/foo.ply", target_size=[0.4, 0.1])
