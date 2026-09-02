"""Verifier module — enforces mesh quality gates and dimension tolerances."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import trimesh

from ..client.units import to_metres
from ..spec.schema import ObjectSpec
from ..spec.validation import DimensionGateResult, evaluate_dimension_gate

if TYPE_CHECKING:
    from ..client.job import JobCard


def load_merged_mesh(path: str | Path) -> trimesh.Trimesh | None:
    """Load a GLB as a single world-space mesh suitable for gate checks.

    glTF stores part positions as node transforms and splits vertices per
    normal/UV attribute, so the mesh must be flattened through the scene graph
    and rebuilt through the constructor (which merges coincident vertices).
    trimesh 5.0's instance-level merge_vertices() does not merge scene-loaded
    geometry, so the constructor path is required."""
    scene_or_mesh = trimesh.load(str(path))
    if isinstance(scene_or_mesh, trimesh.Scene):
        if not scene_or_mesh.geometry:
            return None
        mesh = scene_or_mesh.to_mesh()
    else:
        mesh = scene_or_mesh
    return trimesh.Trimesh(
        vertices=np.asarray(mesh.vertices), faces=np.asarray(mesh.faces), process=True
    )


def evaluate_card_axis_gate(
    job_card: "JobCard", measurement_data: dict[str, Any]
) -> tuple[bool, list[dict[str, Any]], list[str]]:
    """Delivery-axis check against the client job card (Phase 7 cold-path fix).

    The spec's own measurements are authored by the analyst, so the internal
    dimension gate verifies whatever axis binding the analyst DECLARED — a
    spec that binds "length" to the Y extent passes its own gate while the
    client card (L→X, W→Y, H→Z) fails at package time with a 90° swap. This
    gate compares the measured overall extents against the card's dims
    through the card's axis map, so the mismatch surfaces INSIDE the loop
    where the corrector can fix it, instead of after the finish chain.

    Returns (passed, details, feedback_lines). Tolerance is the CARD's
    delivery tolerance (job.dim_tolerance, default 0.01 in the card's
    declared unit) — the same figure the client's Dimensions gate applies
    at package time — so an internally-green build (spec tolerance ±1 mm)
    is driven to client-green INSIDE the loop instead of failing the
    package step by e.g. +0.1 mm."""
    overall_dims = (measurement_data.get("overall", {}) or {}).get(
        "dimensions", [0.0, 0.0, 0.0])
    axis_index = {"x": 0, "y": 1, "z": 2}
    targets = {
        axis_index[str(job_card.axis_map.length).lower()]:
            to_metres(job_card.dims.length, job_card.dims.unit),
        axis_index[str(job_card.axis_map.width).lower()]:
            to_metres(job_card.dims.width, job_card.dims.unit),
        axis_index[str(job_card.axis_map.height).lower()]:
            to_metres(job_card.dims.height, job_card.dims.unit),
    }
    tol_m = job_card.dim_tolerance_m()
    names = {0: "length", 1: "width", 2: "height"}
    details: list[dict[str, Any]] = []
    feedback: list[str] = []
    passed = True
    for idx in sorted(targets):
        target = targets[idx]
        actual = overall_dims[idx] if len(overall_dims) > idx else 0.0
        delta = abs(actual - target)
        ok = delta <= tol_m
        passed = passed and ok
        details.append({
            "name": f"job card {names[idx]} (axis {'XYZ'[idx]})",
            "target_m": round(target, 5),
            "actual_m": round(actual, 5),
            "delta_m": round(delta, 5),
            "delta_mm": round(delta * 1000, 3),
            "tolerance_m": tol_m,
            "passed": ok,
        })
        if not ok:
            feedback.append(
                f" - Job card axis mismatch: {'XYZ'[idx]} extent is {actual:.5f} m "
                f"but the card's {names[idx]} is {target:.5f} m "
                f"(delta {delta * 1000:.3f} mm, card tolerance "
                f"±{tol_m * 1000:.3f} mm). The client measures "
                f"{names[idx]} along {'XYZ'[idx]} (card axis map "
                f"L→{job_card.axis_map.length.upper()}, "
                f"W→{job_card.axis_map.width.upper()}, "
                f"H→{job_card.axis_map.height.upper()}) — rotate or "
                "re-dimension the parts so the overall extents match the "
                "card on every axis, exactly (the client gate is far "
                "tighter than the internal build tolerance)."
            )
    return passed, details, feedback


@dataclass
class MeshGateResult:
    passed: bool
    is_watertight: bool
    faces_count: int
    vertices_count: int
    bounding_box_m: list[float]
    volume_m3: float
    warnings: list[str]
    errors: list[str]


@dataclass
class VerificationReport:
    passed: bool
    dimension_gate: DimensionGateResult
    mesh_gate: MeshGateResult
    feedback_for_agent: str


class Verifier:
    def __init__(self, default_tri_budget: int = 50000):
        self.tri_budget = default_tri_budget

    def evaluate_mesh(self, glb_path: str | Path, tri_budget: int | None = None) -> MeshGateResult:
        """Analyze exported 3D mesh via trimesh for manifoldness, size, and topology."""
        budget = tri_budget or self.tri_budget
        warnings: list[str] = []
        errors: list[str] = []

        path_str = str(glb_path)
        if not Path(path_str).exists():
            return MeshGateResult(
                passed=False,
                is_watertight=False,
                faces_count=0,
                vertices_count=0,
                bounding_box_m=[0, 0, 0],
                volume_m3=0.0,
                warnings=[],
                errors=[f"Exported model file not found: {path_str}"],
            )

        try:
            mesh = load_merged_mesh(path_str)
            if mesh is None or len(mesh.faces) == 0:
                errors.append("Scene contains no 3D geometry.")
                return MeshGateResult(
                    passed=False,
                    is_watertight=False,
                    faces_count=0,
                    vertices_count=0,
                    bounding_box_m=[0, 0, 0],
                    volume_m3=0.0,
                    warnings=[],
                    errors=errors,
                )

            faces_count = len(mesh.faces)
            vertices_count = len(mesh.vertices)
            extents = [float(x) for x in mesh.extents]
            is_watertight = bool(mesh.is_watertight)
            vol = float(mesh.volume) if is_watertight else 0.0

            if faces_count > budget:
                warnings.append(f"Face count ({faces_count}) exceeds budget ({budget}).")
            if not is_watertight:
                warnings.append("Mesh is not completely watertight/closed (open boundary edges or non-manifold junctions).")
            if faces_count == 0:
                errors.append("Mesh contains zero faces.")

            passed = len(errors) == 0
            return MeshGateResult(
                passed=passed,
                is_watertight=is_watertight,
                faces_count=faces_count,
                vertices_count=vertices_count,
                bounding_box_m=extents,
                volume_m3=vol,
                warnings=warnings,
                errors=errors,
            )
        except Exception as e:
            return MeshGateResult(
                passed=False,
                is_watertight=False,
                faces_count=0,
                vertices_count=0,
                bounding_box_m=[0, 0, 0],
                volume_m3=0.0,
                warnings=[],
                errors=[f"Failed to inspect mesh with trimesh: {e}"],
            )

    def verify_run(
        self,
        spec: ObjectSpec,
        measurement_data: dict[str, Any],
        glb_path: str | Path,
        job_card: "JobCard | None" = None,
    ) -> VerificationReport:
        """Comprehensive verification of dimensions against spec and mesh topological quality."""
        dim_result = evaluate_dimension_gate(spec, measurement_data)
        mesh_result = self.evaluate_mesh(glb_path, tri_budget=spec.tri_budget)

        overall_passed = dim_result.passed and mesh_result.passed

        # Generate actionable feedback for the agent loop
        feedback_lines = []
        if not dim_result.passed:
            feedback_lines.append(f"DIMENSION GATE FAILED ({dim_result.failed_count} measurements out of tolerance):")
            for d in dim_result.details:
                if not d.get("passed", False):
                    if d.get("actual_m") is None:
                        # Unmeasurable target (Phase 6 cold-path defect: the
                        # old line printed "Actual Nonem, Delta 0mm" — a fake
                        # zero delta the corrector chased for iterations).
                        feedback_lines.append(
                            f" - Measurement '{d['name']}': UNMEASURABLE — {d.get('reason', 'no measured value for this target')}. "
                            "Rewrite its applies_to to the measurement grammar "
                            "(overall.width_x | overall.depth_y | overall.height_z | "
                            "<part>.width_x | <part>.depth_y | <part>.height_z | "
                            "<part>.top_z | <part>.bottom_z) pointing at a real part, "
                            "and set target_value to that measurable quantity "
                            "(a center height becomes the part's top_z with the "
                            "target adjusted by half its height)."
                        )
                    else:
                        feedback_lines.append(
                            f" - Measurement '{d['name']}': Target {d['target_m']}m, Actual {d['actual_m']}m, "
                            f"Delta {d.get('delta_mm', 0)}mm (Tolerance: {d['tolerance_m']*1000}mm)"
                        )
        else:
            feedback_lines.append("DIMENSION GATE PASSED: All measurements match spec within tolerance.")

        if job_card is not None:
            card_passed, card_details, card_feedback = evaluate_card_axis_gate(
                job_card, measurement_data)
            # Card-axis details ride in the dimension gate's details so the
            # run manifest carries the evidence; failures force a corrector
            # iteration exactly like any other dimension failure.
            dim_result.details.extend(card_details)
            if not card_passed:
                overall_passed = False
                dim_result.passed = False
                dim_result.failed_count += len(
                    [d for d in card_details if not d.get("passed")])
                feedback_lines.append(
                    "JOB CARD AXIS GATE FAILED (overall extents do not match "
                    "the client job card's axis convention):")
                feedback_lines.extend(card_feedback)
            else:
                feedback_lines.append(
                    "JOB CARD AXIS GATE PASSED: overall extents match the "
                    "client job card on every axis.")

        if not mesh_result.passed:
            feedback_lines.append("MESH GATE FAILED:")
            for err in mesh_result.errors:
                feedback_lines.append(f" - Error: {err}")
        if mesh_result.warnings:
            for w in mesh_result.warnings:
                feedback_lines.append(f" - Warning: {w}")

        feedback_str = "\n".join(feedback_lines)

        return VerificationReport(
            passed=overall_passed,
            dimension_gate=dim_result,
            mesh_gate=mesh_result,
            feedback_for_agent=feedback_str,
        )
