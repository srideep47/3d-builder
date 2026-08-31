"""Neural backend interface for the img3d service.

A backend is loaded once inside the service process (the GPU stays warm) and
must be importable without torch until `load()` is called — the service and
the mock backend must run in the light main environment too.
"""

from __future__ import annotations

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
