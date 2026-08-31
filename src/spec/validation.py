"""ObjectSpec v2 validation and hierarchical measurement gate evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schema import ObjectSpec

_AXIS_BY_ATTR = {
    "width": 0,
    "width_x": 0,
    "x": 0,
    "size_x": 0,
    "depth": 1,
    "depth_y": 1,
    "y": 1,
    "size_y": 1,
    "height": 2,
    "height_z": 2,
    "z": 2,
    "size_z": 2,
}


@dataclass
class DimensionGateResult:
    passed: bool
    measurements_checked: int
    passed_count: int
    failed_count: int
    details: list[dict[str, Any]]
    max_delta_m: float
    ground_contact_passed: bool = True
    ground_contact_min_z: float = 0.0
    ground_contact_failures: list[str] = field(default_factory=list)


def _resolve_part_value(part_data: dict[str, Any], attr: str) -> float | None:
    """Map an applies_to attribute to a measured value for one part."""
    attr = attr.lower().strip()
    if attr in ("top_z", "max_z"):
        return part_data.get("max", [0.0, 0.0, 0.0])[2]
    if attr in ("bottom_z", "min_z"):
        return part_data.get("min", [0.0, 0.0, 0.0])[2]
    axis = _AXIS_BY_ATTR.get(attr)
    if axis is None:
        return None
    dims = part_data.get("dimensions", [0.0, 0.0, 0.0])
    return dims[axis] if axis < len(dims) else None


def validate_spec_structure(spec: ObjectSpec) -> list[str]:
    """Validates structural integrity of an ObjectSpec v2."""
    errors: list[str] = []
    part_names = set()

    for i, p in enumerate(spec.parts):
        if not p.name:
            errors.append(f"Part at index {i} has no name")
        elif p.name in part_names:
            errors.append(f"Duplicate part name: '{p.name}'")
        part_names.add(p.name)

        if len(p.dimensions) != 3 or any(d <= 0 for d in p.dimensions):
            errors.append(f"Part '{p.name}' has invalid dimensions {p.dimensions}. All 3 values must be > 0.")

    for m in spec.measurements:
        if m.target_value <= 0:
            errors.append(f"Measurement '{m.name}' has target value <= 0: {m.target_value}")

    return errors


def evaluate_dimension_gate(spec: ObjectSpec, measurement_data: dict[str, Any]) -> DimensionGateResult:
    """Compares measured geometry against spec measurements and verifies ground contact."""
    overall = measurement_data.get("overall", {})
    parts_data = measurement_data.get("parts", {})

    overall_dims = overall.get("dimensions", [0.0, 0.0, 0.0])
    overall_min = overall.get("min", [0.0, 0.0, 0.0])

    overall_map = {
        "overall.width": overall_dims[0] if len(overall_dims) > 0 else 0.0,
        "overall.width_x": overall_dims[0] if len(overall_dims) > 0 else 0.0,
        "overall.depth": overall_dims[1] if len(overall_dims) > 1 else 0.0,
        "overall.depth_y": overall_dims[1] if len(overall_dims) > 1 else 0.0,
        "overall.height": overall_dims[2] if len(overall_dims) > 2 else 0.0,
        "overall.height_z": overall_dims[2] if len(overall_dims) > 2 else 0.0,
    }

    details: list[dict[str, Any]] = []
    passed_count = 0
    failed_count = 0
    max_delta = 0.0

    for m in spec.measurements:
        target_m = m.unit.to_meters(m.target_value)
        tol_m = m.tolerance_m or spec.tolerance_m or 0.001
        applies = m.applies_to.lower().strip()

        actual_val = None
        if applies in overall_map:
            actual_val = overall_map[applies]
        elif applies.startswith("overall"):
            name_low = m.name.lower()
            if "width" in name_low or "x" in name_low:
                actual_val = overall_dims[0]
            elif "depth" in name_low or "y" in name_low:
                actual_val = overall_dims[1]
            elif "height" in name_low or "z" in name_low:
                actual_val = overall_dims[2]
        else:
            # Part-level measurement (e.g. "seat_cushion.width_x", "leg_1.top_z")
            tokens = applies.split(".")
            part_name = tokens[0]
            attr = tokens[1] if len(tokens) > 1 else "height_z"

            matched_part = parts_data.get(part_name)
            if matched_part is None:
                for k, v in parts_data.items():
                    if k.lower() == part_name.lower():
                        matched_part = v
                        break

            if matched_part is not None:
                actual_val = _resolve_part_value(matched_part, attr)

        if actual_val is None:
            failed_count += 1
            details.append({
                "name": m.name,
                "target_m": round(target_m, 5),
                "actual_m": None,
                "delta_m": None,
                "tolerance_m": round(tol_m, 5),
                "passed": False,
                "reason": f"Could not map '{m.applies_to}' to measured scene geometry",
            })
            continue

        delta = abs(actual_val - target_m)
        max_delta = max(max_delta, delta)
        passed = delta <= tol_m

        if passed:
            passed_count += 1
        else:
            failed_count += 1

        details.append({
            "name": m.name,
            "target_m": round(target_m, 5),
            "actual_m": round(actual_val, 5),
            "delta_m": round(delta, 5),
            "tolerance_m": round(tol_m, 5),
            "passed": passed,
            "delta_mm": round(delta * 1000.0, 2),
        })

    # Ground contact: the whole model must touch Z=0 within 1mm...
    min_z = overall_min[2] if len(overall_min) > 2 else 0.0
    ground_contact_passed = abs(min_z) <= 0.001

    # ...and every part declared in a ground_contact constraint must sit at Z=0.
    ground_failures: list[str] = []
    ground_tol = max(float(spec.tolerance_m or 0.001), 0.0005)
    for constraint in spec.constraints or []:
        if str(constraint.type).lower() != "ground_contact":
            continue
        for pname in constraint.parts:
            part = parts_data.get(pname)
            if part is None:
                for k, v in parts_data.items():
                    if k.lower() == pname.lower():
                        part = v
                        break
            if part is None:
                ground_failures.append(f"ground_contact part '{pname}' not found in built model")
                continue
            pz = part.get("min", [0.0, 0.0, 0.0])[2]
            if abs(pz) > ground_tol:
                ground_failures.append(
                    f"Part '{pname}' sits at z={pz:.4f} m but ground_contact requires z=0 (±{ground_tol * 1000:.1f} mm)"
                )

    all_passed = (
        failed_count == 0
        and (len(spec.measurements) == 0 or passed_count > 0)
        and ground_contact_passed
        and not ground_failures
    )
    return DimensionGateResult(
        passed=all_passed,
        measurements_checked=len(spec.measurements),
        passed_count=passed_count,
        failed_count=failed_count,
        details=details,
        max_delta_m=max_delta,
        ground_contact_passed=ground_contact_passed and not ground_failures,
        ground_contact_min_z=min_z,
        ground_contact_failures=ground_failures,
    )
