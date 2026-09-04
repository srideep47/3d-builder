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

from .base import GenerateOutput, GenerateParams, NeuralBackend, decimate_to_budget

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
        if params.image_path is None:
            raise RuntimeError("tripo_sr takes a single image (image_path), not labelled views")
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
        # has_vertex_color is REQUIRED by the vendored TSR (upstream run.py
        # passes `not bake_texture`); we discard vertex colors — PBR comes
        # from our own texture pipeline — so no color query on the triplane.
        # extract_mesh returns ONE trimesh per scene code in the batch —
        # a bare `.export()` on the return value dies with "'list' object
        # has no attribute 'export'" (caught live in the bake-off leg); we
        # always generate a single image, so index the only element.
        meshes = self.model.extract_mesh(
            code, has_vertex_color=False, resolution=resolution
        )
        if not meshes:
            raise RuntimeError("TripoSR returned no mesh for this image")
        mesh = meshes[0]

        tmp_glb = params.output_dir / f"{params.image_path.stem}_tripo_raw.glb"
        mesh.export(tmp_glb)
        result = trimesh.load(tmp_glb, force="mesh", process=True)
        tmp_glb.unlink(missing_ok=True)

        result = decimate_to_budget(result, params.max_tris, "tripo_sr")

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
