"""Image-to-3D neural reconstruction provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ImageTo3DResult:
    success: bool
    output_glb_path: str | Path | None
    tri_count: int
    duration_sec: float
    error: str | None = None


class ImageTo3DProvider(ABC):
    @abstractmethod
    def generate_mesh_from_image(
        self,
        image_path: str | Path,
        output_dir: str | Path,
        target_dimensions_m: list[float] | None = None,
    ) -> ImageTo3DResult:
        """Generate a 3D mesh (GLB) from a single 2D reference image."""
        ...
