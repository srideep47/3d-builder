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


# ── Phase 6: applies_to grammar is checked pre-build ────────────────────────


def test_structure_check_rejects_unmeasurable_attribute():
    """Phase 6 cold-path defect: the live analyst authored
    'upper_peg_ring.position_z' — parses, but the gate can never measure it,
    so the corrector chased a fake 'Delta 0mm' for six iterations. The
    structure check must catch this before the first build."""
    from src.spec.validation import validate_spec_structure

    spec = _spec()
    spec.measurements.append(
        MeasurementSpec(name="ring_center", target_value=1.6,
                        applies_to="tabletop.position_z")
    )
    errors = validate_spec_structure(spec)
    assert any("cannot measure" in e and "position_z" in e
               for e in errors), errors


def test_structure_check_rejects_unknown_part_target():
    from src.spec.validation import validate_spec_structure

    spec = _spec()
    spec.measurements.append(
        MeasurementSpec(name="ghost", target_value=0.5,
                        applies_to="ghost_part.width_x")
    )
    errors = validate_spec_structure(spec)
    assert any("unknown part" in e and "ghost_part" in e
               for e in errors), errors


def test_structure_check_still_accepts_legacy_forms():
    """Bare part names (default height_z), bare 'overall' (name-heuristic
    axis), and every documented grammar form stay legal."""
    from src.spec.validation import validate_spec_structure

    spec = _spec()
    spec.measurements.extend([
        MeasurementSpec(name="leg_h", target_value=0.72, applies_to="leg_fl"),
        MeasurementSpec(name="overall_bare", target_value=1.4, applies_to="overall"),
        MeasurementSpec(name="leg_top", target_value=0.72, applies_to="leg_fl.top_z"),
        MeasurementSpec(name="leg_bottom", target_value=0.05, applies_to="leg_fl.bottom_z"),
    ])
    assert validate_spec_structure(spec) == []


def test_unmeasurable_feedback_names_the_reason_not_a_fake_delta(monkeypatch):
    """Second line of defense: if an unmeasurable target still reaches
    verify_run (grammar check missed it, or the measurement data lacks the
    part), the corrector's feedback must say UNMEASURABLE with the mapping
    reason — never 'Actual Nonem, Delta 0mm', a fake zero delta that reads
    as a pass and sends the corrector in circles."""
    from src.agent.verifier import MeshGateResult, Verifier

    # A passing mesh gate isolates the dimension-feedback path; no GLB needed.
    monkeypatch.setattr(
        Verifier, "evaluate_mesh",
        lambda self, glb_path, tri_budget=None: MeshGateResult(
            passed=True, is_watertight=True, faces_count=10, vertices_count=10,
            bounding_box_m=[1, 1, 1], volume_m3=1.0, warnings=[], errors=[]),
    )

    spec = _spec()
    spec.measurements.append(
        MeasurementSpec(name="ring_center", target_value=1.6,
                        applies_to="tabletop.position_z")
    )
    report = Verifier().verify_run(spec, _measure_data(), glb_path="unused.glb")

    detail = next(d for d in report.dimension_gate.details
                  if d["name"] == "ring_center")
    assert detail["actual_m"] is None
    assert detail.get("reason"), "gate must record why the target is unmeasurable"

    feedback = report.feedback_for_agent
    assert "UNMEASURABLE" in feedback
    assert "position_z" in feedback or "Could not map" in feedback
    assert "measurement grammar" in feedback  # tells the corrector how to fix it
    assert "Nonem" not in feedback
    assert "Delta 0mm" not in feedback
