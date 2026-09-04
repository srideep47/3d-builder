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
    # `image_path` is the legacy single-view contract (tripo_sr, trellis.cpp);
    # `views` carries the labelled multi-view set (front/back/left/right) for
    # backends that can use it (comfy_trellis2). Both are optional at the
    # dataclass level; resolve_views() enforces that at least one is present.
    image_path: Path | None = None
    output_dir: Path | None = None
    target_size_m: list[float] | None = None  # [x, y, z] in meters
    max_tris: int = 50000
    seed: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    # Labelled views, e.g. {"front": p, "back": p, "left": p, "right": p};
    # backends decide which labels they accept.
    views: dict[str, Path] | None = None

    def resolve_views(self) -> dict[str, Path]:
        """The labelled view set for this job: `views` when given, else the
        single image as the front view. Raises when neither is present."""
        if self.views:
            return dict(self.views)
        if self.image_path is not None:
            return {"front": self.image_path}
        raise ValueError("GenerateParams needs image_path or views")


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

    # Backends that never touch the GPU skip the machine-wide GPU lock in
    # the service worker — a mock generation must not queue behind (or hold
    # up) a real one.
    uses_gpu: bool = True

    @abstractmethod
    def is_available(self) -> tuple[bool, str]:
        """(available, reason). Must not import heavy deps — probe installs."""

    @abstractmethod
    def load(self) -> None:
        """Load model weights onto the device. Called once, lazily."""

    @abstractmethod
    def generate(self, params: GenerateParams) -> GenerateOutput:
        """Run inference and write the GLB. Synchronous — the queue serializes."""

    def unload(self) -> None:
        """Release GPU memory after a generation (§4.0 GPU sequencing).

        Default no-op: backends with in-process weights may deliberately
        stay resident (the legacy "model stays loaded" behaviour). Backends
        driving an external GPU process (comfy_trellis2 → POST /free)
        override this. The service worker calls it after every generate()
        while still holding the machine GPU lock; an unload failure is
        logged by the caller and must never lose a completed generation.
        """
