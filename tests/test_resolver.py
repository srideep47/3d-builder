"""Resolver tests: unit conversion, preset resolution, payload shape."""

import pytest

from src.spec.schema import (
    BevelModifier,
    Modifiers,
    ObjectSpec,
    PartSpec,
    PBRMaterial,
    ShapeType,
)
from src.materials.pbr import get_preset_values
from src.spec.resolver import resolve_spec_to_build_params


def test_cm_units_convert_to_meters():
    spec = ObjectSpec(
        name="cm desk",
        units="cm",
        parts=[PartSpec(name="top", dimensions=[140.0, 70.0, 4.0], position=[0.0, 0.0, 74.0])],
    )
    params = resolve_spec_to_build_params(spec)
    part = params["spec"]["parts"][0]
    assert part["dimensions"] == pytest.approx([1.4, 0.7, 0.04])
    assert part["position"] == pytest.approx([0.0, 0.0, 0.74])


def test_mm_units_convert_profile_points():
    spec = ObjectSpec(
        name="vase",
        units="mm",
        parts=[
            PartSpec(
                name="body",
                shape="revolve_lathe",
                profile_points=[[0.0, 0.0], [80.0, 0.0], [100.0, 150.0], [0.0, 150.0]],
            )
        ],
    )
    params = resolve_spec_to_build_params(spec)
    profile = params["spec"]["parts"][0]["profile_points"]
    assert profile[1] == pytest.approx([0.08, 0.0])
    assert profile[2] == pytest.approx([0.1, 0.15])


def test_preset_resolution_with_explicit_override():
    spec = ObjectSpec(
        name="desk",
        parts=[
            PartSpec(
                name="top",
                dimensions=[1.4, 0.7, 0.04],
                material=PBRMaterial(preset="oak_wood", roughness=0.55),
            )
        ],
    )
    params = resolve_spec_to_build_params(spec)
    mat = params["spec"]["parts"][0]["material"]
    # Explicit roughness wins, color comes from the preset.
    assert mat["roughness"] == 0.55
    assert mat["color"] == [0.65, 0.45, 0.28]
    assert mat["metallic"] == 0.0


def test_preset_resolution_pure():
    spec = ObjectSpec(
        name="desk",
        parts=[
            PartSpec(name="leg", dimensions=[0.05, 0.05, 0.7], material=PBRMaterial(preset="chrome"))
        ],
    )
    params = resolve_spec_to_build_params(spec)
    mat = params["spec"]["parts"][0]["material"]
    assert mat["metallic"] == 1.0
    assert mat["roughness"] == 0.05


def test_modifier_payload_conversion():
    spec = ObjectSpec(
        name="array",
        units="cm",
        parts=[
            PartSpec(
                name="spoke",
                dimensions=[30.0, 2.0, 2.0],
                modifiers=Modifiers(bevel=BevelModifier(width=0.4, segments=2)),
            )
        ],
    )
    params = resolve_spec_to_build_params(spec)
    mods = params["spec"]["parts"][0]["modifiers"]
    assert mods["bevel"]["width"] == 0.004  # 4mm


def test_constraints_passed_through():
    spec = ObjectSpec(
        name="desk",
        parts=[PartSpec(name="leg", dimensions=[0.05, 0.05, 0.7])],
        constraints=[{"type": "ground_contact", "parts": ["leg"]}],
    )
    params = resolve_spec_to_build_params(spec)
    assert params["spec"]["constraints"][0]["type"] == "ground_contact"


def test_explicit_json_null_material_fields_are_treated_as_unset():
    """Phase 6 cold-path defect: the live analyst emitted "texture_size": null
    (and other explicit nulls). Pydantic marks those fields SET, but they carry
    no value — the preset must win instead of the resolver iterating None and
    raising TypeError before the harness ever runs."""
    spec = ObjectSpec(
        name="coat_stand",
        parts=[
            PartSpec(
                name="pole",
                dimensions=[0.056, 0.056, 1.66],
                material=PBRMaterial.model_validate({
                    "preset": "walnut_wood",
                    "texture_size": None,
                    "texture_dir": None,
                    "bump_strength": None,
                    "emission": None,
                }),
            )
        ],
    )
    params = resolve_spec_to_build_params(spec)
    mat = params["spec"]["parts"][0]["material"]
    preset = get_preset_values("walnut_wood")
    # Explicit nulls behave exactly like unset fields: preset values win and
    # no None payload reaches the harness.
    assert mat["roughness"] == preset["roughness"]
    assert mat["color"] == preset["color"]
    assert "texture_size" not in mat
    assert "texture_dir" not in mat
    assert "bump_strength" not in mat
    assert None not in mat.values()


def test_presetless_material_drops_explicit_nulls():
    """A material with no preset dumps without explicitly-null optionals —
    the harness sees clean defaults, never a None payload."""
    spec = ObjectSpec(
        name="plain",
        parts=[
            PartSpec(
                name="body",
                dimensions=[0.2, 0.2, 0.3],
                material=PBRMaterial.model_validate({
                    "texture_size": None, "texture_dir": None, "bump_strength": None,
                    "preset": None,
                }),
            )
        ],
    )
    params = resolve_spec_to_build_params(spec)
    mat = params["spec"]["parts"][0]["material"]
    assert mat == {} or None not in mat.values()
    assert "texture_size" not in mat


def test_extrude_caps_default_to_fan_for_delivery():
    """Phase 6 cold-path defect: extrude parts defaulted to n-gon caps, which
    the client delivery gate refuses (0 n-gons, strict). The default is now
    fan — n-gon-free by construction without analyst knowledge of the gate."""
    spec = ObjectSpec(
        name="caps_default",
        parts=[
            PartSpec(
                name="rib",
                shape=ShapeType.EXTRUDE,
                dimensions=[0.1, 0.1, 0.4],
                profile_points=[[0.05, 0], [0.05, 0.05], [0.0, 0.05],
                                [-0.05, 0.05], [-0.05, -0.05], [0.05, -0.05]],
            )
        ],
    )
    params = resolve_spec_to_build_params(spec)
    part = params["spec"]["parts"][0]
    assert part["caps"] == "fan"
