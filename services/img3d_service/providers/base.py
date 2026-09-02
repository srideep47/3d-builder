"""Neural backend interface for the img3d service.

A backend is loaded once inside the service process (the GPU stays warm) and
must be importable without torch until `load()` is called — the service and
the mock backend must run in the light main environment too.
"""

from __future__ import annotations

import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GenerateParams:
    image_path: Path
    output_dir: Path
    target_size_m: list[float] | None = None  # [x, y, z] in meters
    max_tris: int = 50000
    seed: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerateOutput:
    glb_path: Path
    tri_count: int
    duration_sec: float


def decimate_to_budget(mesh, max_tris: int, backend: str):
    """Decimate a trimesh to the triangle budget, or warn loudly when the
    simplifier is unavailable — never silently ship over budget.

    trimesh 5.x: the first positional parameter of
    simplify_quadric_decimation is `percent` (a 0-1 reduction fraction); a
    face count there raises ValueError, which a silent guard swallows while
    shipping ~3x the budget (caught live in the trellis bake-off leg:
    142,688 avg tris against the 50,000 budget). fast_simplification is
    optional in light environments and the delivery pipeline enforces the
    budget downstream, so the fallback is a warning, not an error.
    """
    if len(mesh.faces) <= max_tris:
        return mesh
    try:
        return mesh.simplify_quadric_decimation(face_count=max_tris)
    except Exception as e:
        warnings.warn(
            f"{backend} decimation skipped ({type(e).__name__}: {e}); "
            f"shipping {len(mesh.faces)} faces against the {max_tris} budget"
        )
        return mesh


class NeuralBackend(ABC):
    """One image → one watertight-ish GLB mesh, scaled to target_size_m."""

    name: str = "abstract"

    @abstractmethod
    def is_available(self) -> tuple[bool, str]:
        """(available, reason). Must not import heavy deps — probe installs."""

    @abstractmethod
    def load(self) -> None:
        """Load model weights onto the device. Called once, lazily."""

    @abstractmethod
    def generate(self, params: GenerateParams) -> GenerateOutput:
        """Run inference and write the GLB. Synchronous — the queue serializes."""
