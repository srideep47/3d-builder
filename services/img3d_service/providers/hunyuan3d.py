"""Hunyuan3D-2.1 backend — placeholder for the M4 bake-off.

Strongest PBR textures of the three candidates (~12 GB VRAM). Wired into the
provider registry; implemented when the GPU environment is installed.
"""

from __future__ import annotations

from .base import GenerateOutput, GenerateParams, NeuralBackend


class Hunyuan3DBackend(NeuralBackend):
    name = "hunyuan3d"

    def __init__(self, models_dir=None, device: str = "cuda"):
        self.models_dir = models_dir
        self.device = device

    def is_available(self) -> tuple[bool, str]:
        return False, "Hunyuan3D-2.1 not installed yet (M4 bake-off; see services/img3d_service/README.md)"

    def load(self) -> None:
        raise RuntimeError(self.is_available()[1])

    def generate(self, params: GenerateParams) -> GenerateOutput:
        raise RuntimeError(self.is_available()[1])
