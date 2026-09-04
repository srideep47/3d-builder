"""Deterministic mock backend — no GPU, no torch.

Produces a plausible organic blob (a displaced icosphere whose shape derives
from the image content hash) scaled to the requested target size. Used for
pipeline bring-up, tests, and as the service's default when no neural deps
are installed. Output is deliberately watertight and low-poly so it passes
the mesh audit.
"""

from __future__ import annotations

import hashlib
import time

import numpy as np
import trimesh

from .base import GenerateOutput, GenerateParams, NeuralBackend


class MockBackend(NeuralBackend):
    name = "mock"
    uses_gpu = False  # never queue behind (or hold up) real GPU tenants

    def __init__(self, models_dir=None):
        self.models_dir = models_dir

    def is_available(self) -> tuple[bool, str]:
        return True, "always available (deterministic CPU mesh)"

    def load(self) -> None:
        return None

    def generate(self, params: GenerateParams) -> GenerateOutput:
        started = time.perf_counter()
        assert params.output_dir is not None
        params.output_dir.mkdir(parents=True, exist_ok=True)
        front = params.resolve_views()["front"]

        # Seed from the front image content so the same reference always
        # yields the same shape (deterministic runs, cache-friendly benchmarks).
        seed = params.seed
        if seed is None:
            digest = hashlib.sha256(front.read_bytes()).digest()
            seed = int.from_bytes(digest[:4], "little")
        rng = np.random.default_rng(seed)

        mesh = trimesh.creation.icosphere(subdivisions=2, radius=1.0)
        vertices = np.asarray(mesh.vertices, dtype=np.float64)
        # Smooth low-frequency displacement — organic blob, no sharp noise.
        dirs = rng.normal(size=(4, 3))
        warp = (vertices @ dirs.T).mean(axis=1)
        warp = (warp - warp.min()) / max(warp.max() - warp.min(), 1e-9)
        scale = 0.75 + 0.5 * warp
        vertices = vertices * scale[:, None]

        blob = trimesh.Trimesh(vertices=vertices, faces=mesh.faces, process=True)
        blob.fix_normals()

        target = params.target_size_m
        if target:
            extents = blob.extents
            blob.apply_scale([target[i] / max(extents[i], 1e-9) for i in range(3)])
        else:
            blob.apply_scale(0.2)  # 20 cm default blob

        try:
            if len(blob.faces) > params.max_tris:
                blob = blob.simplify_quadric_decimation(params.max_tris)
        except Exception:
            pass  # fast-simplification optional; mock meshes are already low-poly

        out_path = params.output_dir / f"{front.stem}_mock.glb"
        blob.export(out_path)

        return GenerateOutput(
            glb_path=out_path,
            tri_count=int(len(blob.faces)),
            duration_sec=time.perf_counter() - started,
        )
