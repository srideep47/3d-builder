"""Dimension/grounding gate tests against synthetic measurement data."""

from src.spec.schema import (
    ConstraintSpec,
    MeasurementSpec,
    ObjectSpec,
    PartSpec,
)
from src.spec.validation import evaluate_dimension_gate


def _measure_data(leg_min_z: float = 0.0) -> dict:
    return {
        "overall": {
            "dimensions": [1.4, 0.7, 0.76],
            "min": [-0.7, -0.35, 0.0],
            "max": [0.7, 0.35, 0.76],
        },
        "parts": {
            "tabletop": {
                "dimensions": [1.4, 0.7, 0.04],
                "min": [-0.7, -0.35, 0.72],
                "max": [0.7, 0.35, 0.76],
                "center": [0.0, 0.0, 0.74],
            },
            "leg_fl": {
                "dimensions": [0.06, 0.06, 0.72],
                "min": [-0.65, -0.31, leg_min_z],
                "max": [-0.59, -0.25, leg_min_z + 0.72],
                "center": [-0.62, -0.28, leg_min_z + 0.36],
            },
        },
    }


def _spec() -> ObjectSpec:
    return ObjectSpec(
        name="desk",
        parts=[
            PartSpec(name="tabletop", dimensions=[1.4, 0.7, 0.04], position=[0, 0, 0.74]),
            PartSpec(name="leg_fl", shape="tapered_extrude", dimensions=[0.06, 0.06, 0.72]),
        ],
        measurements=[
            MeasurementSpec(name="width", target_value=1.4, applies_to="overall.width_x"),
            MeasurementSpec(name="height", target_value=0.76, applies_to="overall.height_z"),
            MeasurementSpec(name="top_at", target_value=0.76, applies_to="tabletop.top_z"),
        ],
        constraints=[ConstraintSpec(type="ground_contact", parts=["leg_fl"])],
    )


def test_all_measurements_pass():
    result = evaluate_dimension_gate(_spec(), _measure_data())
    assert result.passed
    assert result.failed_count == 0
    assert result.ground_contact_failures == []


def test_dimension_failure_reports_delta():
    spec = _spec()
    spec.measurements.append(
        MeasurementSpec(name="too_tall", target_value=0.80, applies_to="overall.height_z")
    )
    result = evaluate_dimension_gate(spec, _measure_data())
    assert not result.passed
    failed = [d for d in result.details if not d["passed"]]
    assert len(failed) == 1
    assert failed[0]["delta_mm"] == 40.0


def test_part_top_z_resolution():
    result = evaluate_dimension_gate(_spec(), _measure_data())
    top_at = [d for d in result.details if d["name"] == "top_at"][0]
    assert top_at["passed"]
    assert top_at["actual_m"] == 0.76


def test_ground_contact_violation_fails():
    result = evaluate_dimension_gate(_spec(), _measure_data(leg_min_z=0.006))
    assert not result.passed
    assert any("leg_fl" in f for f in result.ground_contact_failures)


def test_unmappable_applies_to_fails():
    spec = _spec()
    spec.measurements.append(
        MeasurementSpec(name="mystery", target_value=1.0, applies_to="nonexistent.width_x")
    )
    result = evaluate_dimension_gate(spec, _measure_data())
    assert not result.passed
    unmapped = [d for d in result.details if d["name"] == "mystery"][0]
    assert "Could not map" in unmapped["reason"]
