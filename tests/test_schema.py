"""ObjectSpec v2 schema tests."""

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
