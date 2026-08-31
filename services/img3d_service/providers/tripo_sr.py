"""TripoSR backend — fastest/lightest image-to-3D (~6 GB VRAM).

Heavy imports (torch, tsr) happen inside load() so the service can list and
probe this backend from the light environment. The TripoSR repo is vendored
by scripts/setup-img3d-gpu.ps1 into vendor/TripoSR (its `tsr` package is not
on PyPI).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from .base import GenerateOutput, GenerateParams, NeuralBackend

HF_MODEL_ID = "stabilityai/TripoSR"
_VENDOR_CANDIDATES = [
    Path(__file__).resolve().parents[1] / "vendor" / "TripoSR",
    Path(__file__).resolve().parents[2] / "models" / "TripoSR",
]


class TripoSRBackend(NeuralBackend):
    name = "tripo_sr"

    def __init__(self, models_dir: Path | None = None, device: str = "cuda"):
        self.models_dir = models_dir
        self.device = device
        self.model = None

    def _vendor_path(self) -> Path | None:
        for cand in _VENDOR_CANDIDATES:
            if (cand / "tsr").is_dir():
                return cand
        return None

    def is_available(self) -> tuple[bool, str]:
        vendor = self._vendor_path()
        if vendor is None:
            return False, "TripoSR repo not vendored (run scripts/setup-img3d-gpu.ps1)"
        if str(vendor) not in sys.path:
            sys.path.insert(0, str(vendor))
        try:
            import torch  # noqa: F401
            from tsr.system import TSR  # noqa: F401

            if self.device.startswith("cuda"):
                import torch

                if not torch.cuda.is_available():
                    return False, "CUDA not available"
            return True, "ready"
        except Exception as e:
            return False, f"import failed: {e}"

    def load(self) -> None:
        available, reason = self.is_available()
        if not available:
            raise RuntimeError(f"TripoSR backend unavailable: {reason}")

        import torch
        from tsr.system import TSR

        if self.device.startswith("cuda") and not torch.cuda.is_available():
            self.device = "cpu"

        kwargs = {}
        if self.models_dir:
            kwargs["cache_dir"] = str(self.models_dir)
        self.model = TSR.from_pretrained(
            HF_MODEL_ID, config_name="config.yaml", weight_name="model.ckpt", **kwargs
        )
        self.model.renderer.set_chunk_size(8192)
        self.model.to(self.device)

    def generate(self, params: GenerateParams) -> GenerateOutput:
        if self.model is None:
            self.load()
        started = time.perf_counter()
        params.output_dir.mkdir(parents=True, exist_ok=True)

        import trimesh
        from PIL import Image

        image = Image.open(params.image_path)
        # TripoSR expects RGB on a white background; composite alpha over white
        # instead of pulling in rembg/onnxruntime.
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            bg = Image.new("RGBA", image.size, (255, 255, 255, 255))
            image = Image.alpha_composite(bg, image)
        image = image.convert("RGB")

        resolution = int(params.extra.get("resolution", 256))
        code = self.model(image, self.device)
        mesh = self.model.extract_mesh(code, resolution=resolution)
        if mesh is None:
            raise RuntimeError("TripoSR returned no mesh for this image")

        tmp_glb = params.output_dir / f"{params.image_path.stem}_tripo_raw.glb"
        mesh.export(tmp_glb)
        result = trimesh.load(tmp_glb, force="mesh", process=True)
        tmp_glb.unlink(missing_ok=True)

        try:
            if len(result.faces) > params.max_tris:
                result = result.simplify_quadric_decimation(params.max_tris)
        except Exception:
            pass  # simplifier optional (fast-simplification); budget enforced downstream

        target = params.target_size_m
        if target:
            extents = result.extents
            result.apply_scale([target[i] / max(extents[i], 1e-9) for i in range(3)])

        out_path = params.output_dir / f"{params.image_path.stem}_tripo.glb"
        result.export(out_path)

        return GenerateOutput(
            glb_path=out_path,
            tri_count=int(len(result.faces)),
            duration_sec=time.perf_counter() - started,
        )
