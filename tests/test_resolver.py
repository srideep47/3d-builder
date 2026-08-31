"""Resolver tests: unit conversion, preset resolution, payload shape."""

import pytest

from src.spec.schema import (
    BevelModifier,
    Modifiers,
    ObjectSpec,
    PartSpec,
    PBRMaterial,
)
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
