"""WSL / Local FastAPI Image-to-3D provider client (for TripoSR / TRELLIS)."""

from __future__ import annotations

import os
import time
from pathlib import Path
import httpx

from .provider import ImageTo3DProvider, ImageTo3DResult


class WSLTripoSRProvider(ImageTo3DProvider):
    def __init__(self, endpoint_url: str = "http://127.0.0.1:8000/generate"):
        self.endpoint_url = os.environ.get("IMAGE_TO_3D_ENDPOINT") or endpoint_url

    def generate_mesh_from_image(
        self,
        image_path: str | Path,
        output_dir: str | Path,
        target_dimensions_m: list[float] | None = None,
    ) -> ImageTo3DResult:
        p = Path(image_path)
        if not p.exists():
            return ImageTo3DResult(
                success=False,
                output_glb_path=None,
                tri_count=0,
                duration_sec=0.0,
                error=f"Image file not found: {image_path}",
            )

        out_d = Path(output_dir)
        out_d.mkdir(parents=True, exist_ok=True)
        target_glb = out_d / f"{p.stem}_neural.glb"

        start_t = time.perf_counter()
        try:
            with open(p, "rb") as img_file:
                files = {"file": (p.name, img_file, "image/png")}
                data = {}
                if target_dimensions_m:
                    data["target_x"] = target_dimensions_m[0]
                    data["target_y"] = target_dimensions_m[1]
                    data["target_z"] = target_dimensions_m[2]

                resp = httpx.post(self.endpoint_url, files=files, data=data, timeout=180.0)
                if resp.status_code != 200:
                    return ImageTo3DResult(
                        success=False,
                        output_glb_path=None,
                        tri_count=0,
                        duration_sec=time.perf_counter() - start_t,
                        error=f"Neural 3D service returned HTTP {resp.status_code}: {resp.text}",
                    )

                with open(target_glb, "wb") as f_out:
                    f_out.write(resp.content)

            return ImageTo3DResult(
                success=True,
                output_glb_path=target_glb,
                tri_count=0,
                duration_sec=time.perf_counter() - start_t,
            )
        except Exception as e:
            return ImageTo3DResult(
                success=False,
                output_glb_path=None,
                tri_count=0,
                duration_sec=time.perf_counter() - start_t,
                error=str(e),
            )
