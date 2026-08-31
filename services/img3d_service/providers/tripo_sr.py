"""TripoSR backend — fastest/lightest image-to-3D (~6 GB VRAM).

Heavy imports (torch, tsr) happen inside load() so the service can list and
probe this backend from the light environment. The TripoSR repo is vendored
by scripts/setup-img3d-gpu.ps1 into vendor/TripoSR (its `tsr` package is not
on PyPI).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from .base import GenerateOutput, GenerateParams, NeuralBackend

HF_MODEL_ID = "stabilityai/TripoSR"
_VENDOR_CANDIDATES = [
    Path(__file__).resolve().parents[1] / "vendor" / "TripoSR",
    Path(__file__).resolve().parents[2] / "models" / "TripoSR",
]


def _install_torchmcubes_shim() -> None:
    """torchmcubes is a CUDA extension that is painful to build on Windows.
    Its single call site (tsr/models/isosurface.py) is a plain marching-cubes
    over a scalar grid — scikit-image does that on CPU. Install a compatible
    module shim only when the real extension is absent."""
    import sys
    import types

    try:
        import torchmcubes  # noqa: F401

        return  # real extension present
    except ImportError:
        pass

    import numpy as np
    import torch
    from skimage.measure import marching_cubes as _sk_mc

    def marching_cubes(grid: "torch.Tensor", isovalue: float):
        volume = grid.detach().cpu().numpy()
        if volume.min() > isovalue or volume.max() < isovalue:
            empty_v = torch.zeros(0, 3, dtype=torch.float32)
            empty_f = torch.zeros(0, 3, dtype=torch.long)
            return empty_v, empty_f
        verts, faces, _, _ = _sk_mc(volume, level=float(isovalue))
        return (
            torch.from_numpy(verts.astype(np.float32)),
            torch.from_numpy(faces.astype(np.int64)),
        )

    shim = types.ModuleType("torchmcubes")
    shim.marching_cubes = marching_cubes
    sys.modules["torchmcubes"] = shim


class TripoSRBackend(NeuralBackend):
    name = "tripo_sr"

    def __init__(self, models_dir: Path | None = None, device: str = "cuda"):
        self.models_dir = models_dir
        self.device = device
        self.model = None
        # huggingface_hub reads HF_HUB_CACHE at import time — set it BEFORE
        # anything imports it (TSR.from_pretrained has no cache_dir kwarg).
        if models_dir:
            cache = Path(models_dir) / "hf"
            cache.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("HF_HUB_CACHE", str(cache))

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
            _install_torchmcubes_shim()
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

        _install_torchmcubes_shim()
        import torch
        from tsr.system import TSR

        if self.device.startswith("cuda") and not torch.cuda.is_available():
            self.device = "cpu"

        self.model = TSR.from_pretrained(
            HF_MODEL_ID, config_name="config.yaml", weight_name="model.ckpt"
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
