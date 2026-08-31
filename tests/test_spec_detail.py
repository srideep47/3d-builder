"""Pure (offline) tests for the ObjectSpec detail block (T3): schema
validation, unit conversion through the resolver, and passthrough shape.
The displacement patterns themselves are pure math executed inside the
Blender harness; their baked OUTPUT is pinned in tests/test_delivery_finish.py.
"""

import pytest

from src.spec.resolver import resolve_spec_to_build_params
from src.spec.schema import DetailSpec, DisplacementSpec, ObjectSpec


def _spec_with_detail(detail: dict, units: str = "mm") -> ObjectSpec:
    return ObjectSpec.model_validate({
        "name": "detail fixture",
        "units": units,
        "parts": [{
            "name": "panel", "shape": "box",
            "dimensions": [100, 100, 10],
            "detail": detail,
        }],
    })


# ── schema ───────────────────────────────────────────────────────────────────


def test_displacement_patterns_are_enum_validated():
    assert DisplacementSpec(pattern="grid_diamond", amplitude=1.0).pattern == "grid_diamond"
    with pytest.raises(ValueError):
        DisplacementSpec(pattern="quilt", amplitude=1.0)  # product noun is NOT a pattern


def test_detail_defaults():
    d = DetailSpec()
    assert d.bevel_width is None and d.subdivision_levels is None
    assert d.displacement is None


def test_detail_is_optional():
    spec = ObjectSpec.model_validate({"name": "x", "parts": [{"name": "p", "shape": "box"}]})
    assert spec.parts[0].detail is None


# ── resolver passthrough ─────────────────────────────────────────────────────


def test_resolver_converts_units_and_passes_detail_through():
    spec = _spec_with_detail({
        "bevel_width": 0.5,               # mm
        "subdivision_levels": 3,
        "displacement": {"pattern": "grid_diamond", "amplitude": 1.5,
                          "frequency": 6, "restrict": "up", "seed": 7},
    })
    params = resolve_spec_to_build_params(spec)
    detail = params["spec"]["parts"][0]["detail"]
    assert detail["bevel_width"] == pytest.approx(0.0005)      # mm -> m
    assert detail["subdivision_levels"] == 3
    disp = detail["displacement"]
    assert disp["pattern"] == "grid_diamond"
    assert disp["amplitude_m"] == pytest.approx(0.0015)        # mm -> m
    assert disp["frequency"] == 6.0
    assert disp["restrict"] == "up"
    assert disp["seed"] == 7


def test_resolver_omits_detail_when_absent():
    spec = ObjectSpec.model_validate({
        "name": "x", "units": "meters",
        "parts": [{"name": "p", "shape": "box", "dimensions": [1, 1, 1]}],
    })
    params = resolve_spec_to_build_params(spec)
    assert "detail" not in params["spec"]["parts"][0]


def test_resolver_inch_amplitude_conversion():
    spec = _spec_with_detail({"displacement": {"pattern": "bumps", "amplitude": 0.25}},
                             units="inches")
    params = resolve_spec_to_build_params(spec)
    assert params["spec"]["parts"][0]["detail"]["displacement"]["amplitude_m"] \
        == pytest.approx(0.25 * 0.0254)


def test_displacement_defaults_round_trip():
    spec = _spec_with_detail({"displacement": {"pattern": "noise", "amplitude": 2}})
    params = resolve_spec_to_build_params(spec)
    disp = params["spec"]["parts"][0]["detail"]["displacement"]
    assert disp["frequency"] == 8.0
    assert disp["axis"] == "z"
    assert disp["exponent"] == 1.0
    assert disp["restrict"] == "none"
