"""TripoSR backend tests — against a fake vendored TSR, GPU-free and
torch-free. The real backend needs the vendored TripoSR repo + torch cu124
(service venv only); these tests pin the TSR call contract that the live
bake-off leg caught the hard way: ``extract_mesh`` REQUIRES ``has_vertex_color``
(no default — a call without it raises TypeError) and returns ONE trimesh per
scene code, i.e. a LIST (upstream run.py indexes ``[0]``). Both defects were
invisible until the service actually ran a tripo_sr job, because a mislabeled
bake-off leg had silently re-run trellis instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = PROJECT_ROOT / "services" / "img3d_service"
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from providers.base import GenerateParams  # noqa: E402
from providers.tripo_sr import TripoSRBackend  # noqa: E402


class _FakeTSR:
    """Vendored TSR stand-in with the real extract_mesh shape: has_vertex_color
    is positional-without-default, and the return value is a list of meshes."""

    def __init__(self, meshes):
        self._meshes = meshes
        self.extract_calls: list[dict] = []

    def __call__(self, image, device):
        return object()  # opaque scene code — never inspected

    def extract_mesh(self, scene_codes, has_vertex_color, resolution=256, threshold=25.0):
        self.extract_calls.append(
            {"has_vertex_color": has_vertex_color, "resolution": resolution}
        )
        return list(self._meshes)


def _png(path: Path) -> Path:
    from PIL import Image

    Image.new("RGB", (16, 16), (200, 30, 30)).save(path)
    return path


def test_generate_indexes_extract_mesh_list(tmp_path):
    """extract_mesh returns a LIST; a bare ``mesh.export(...)`` on its return
    value dies with "'list' object has no attribute 'export'" (the live
    failure). The provider must index the single scene code's mesh and
    deliver a GLB scaled to target_size."""
    import trimesh

    backend = TripoSRBackend()
    backend.model = _FakeTSR([trimesh.creation.icosphere(subdivisions=2, radius=0.5)])

    out = backend.generate(
        GenerateParams(
            image_path=_png(tmp_path / "vase.png"),
            output_dir=tmp_path,
            target_size_m=[0.3, 0.2, 0.15],
            max_tris=50000,
        )
    )

    assert out.glb_path.exists()
    result = trimesh.load(out.glb_path, force="mesh", process=True)
    assert out.tri_count == len(result.faces)
    # unit-diameter sphere scaled to the target lands exactly on it
    for got, want in zip(sorted(result.extents), sorted([0.3, 0.2, 0.15])):
        assert got == pytest.approx(want, abs=2e-3)


def test_generate_passes_has_vertex_color_false(tmp_path):
    """The vendored TSR REQUIRES has_vertex_color; we pass False — vertex
    colors are discarded (PBR comes from our own texture pipeline), so no
    color query is made on the triplane."""
    import trimesh

    backend = TripoSRBackend()
    fake = _FakeTSR([trimesh.creation.box(extents=(0.5, 0.5, 0.5))])
    backend.model = fake

    backend.generate(
        GenerateParams(image_path=_png(tmp_path / "vase.png"), output_dir=tmp_path)
    )

    assert fake.extract_calls, "extract_mesh was never called"
    assert fake.extract_calls[0]["has_vertex_color"] is False


def test_generate_refuses_empty_mesh_list(tmp_path):
    """An empty extract_mesh result (no surface at the density threshold)
    must raise loudly — never export a 'mesh' that is an empty list."""
    backend = TripoSRBackend()
    backend.model = _FakeTSR([])

    with pytest.raises(RuntimeError, match="no mesh"):
        backend.generate(
            GenerateParams(image_path=_png(tmp_path / "vase.png"), output_dir=tmp_path)
        )
