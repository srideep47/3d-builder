"""TRELLIS backend — placeholder for the M4 bake-off.

TRELLIS (structured latents) gives the best geometry quality of the three
candidates but has the heaviest Windows install (spconv, nvdiffrast, custom
CUDA extensions). This slot is wired into the provider registry so the
bake-off tooling can select it once the GPU environment is installed.
"""

from __future__ import annotations

from .base import GenerateOutput, GenerateParams, NeuralBackend


class TrellisBackend(NeuralBackend):
    name = "trellis"

    def __init__(self, models_dir=None, device: str = "cuda"):
        self.models_dir = models_dir
        self.device = device

    def is_available(self) -> tuple[bool, str]:
        return False, "TRELLIS not installed yet (M4 bake-off; see services/img3d_service/README.md)"

    def load(self) -> None:
        raise RuntimeError(self.is_available()[1])

    def generate(self, params: GenerateParams) -> GenerateOutput:
        raise RuntimeError(self.is_available()[1])
