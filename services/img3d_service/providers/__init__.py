"""Provider registry — backend name → class. Selected via IMG3D_MODEL env or
the /generate `model` field (job-level override, only when preloaded)."""

from __future__ import annotations

from .base import NeuralBackend
from .comfy_trellis2 import ComfyTrellis2Backend
from .hunyuan3d import Hunyuan3DBackend
from .mock import MockBackend
from .trellis import TrellisBackend
from .tripo_sr import TripoSRBackend

BACKENDS: dict[str, type[NeuralBackend]] = {
    MockBackend.name: MockBackend,
    TripoSRBackend.name: TripoSRBackend,
    TrellisBackend.name: TrellisBackend,
    Hunyuan3DBackend.name: Hunyuan3DBackend,
    ComfyTrellis2Backend.name: ComfyTrellis2Backend,
}

DEFAULT_BACKEND = "mock"


def create_backend(name: str, models_dir=None) -> NeuralBackend:
    cls = BACKENDS.get(name)
    if cls is None:
        raise KeyError(
            f"Unknown img3d backend '{name}'. Available: {', '.join(sorted(BACKENDS))}"
        )
    return cls(models_dir=models_dir)
