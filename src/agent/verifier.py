"""Verifier module — enforces mesh quality gates and dimension tolerances."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from ..spec.schema import ObjectSpec
from ..spec.validation import DimensionGateResult, evaluate_dimension_gate


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
                    feedback_lines.append(
                        f" - Measurement '{d['name']}': Target {d['target_m']}m, Actual {d['actual_m']}m, "
                        f"Delta {d.get('delta_mm', 0)}mm (Tolerance: {d['tolerance_m']*1000}mm)"
                    )
        else:
            feedback_lines.append("DIMENSION GATE PASSED: All measurements match spec within tolerance.")

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
