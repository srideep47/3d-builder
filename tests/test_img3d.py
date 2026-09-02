"""img3d tests — service API (mock backend), HTTP client, and AgentLoop
neural-part wiring. GPU-free: everything runs against the deterministic mock
backend. The final test is a full hybrid build (parametric + neural part) and
is marked `blender` — skipped automatically when Blender is absent.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = PROJECT_ROOT / "services" / "img3d_service"
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app import app as service_app  # noqa: E402

from src.agent.loop import AgentLoop  # noqa: E402
from src.img3d.client import RemoteImg3DProvider  # noqa: E402
from src.img3d.provider import ImageTo3DResult  # noqa: E402
from src.spec.schema import (  # noqa: E402
    GenerationMethod,
    MeasurementSpec,
    ObjectSpec,
    PartSpec,
    ShapeType,
)

GLB_MAGIC = b"glTF"


def _make_image(tmp: Path, name: str = "ref.png", color=(180, 120, 60)) -> Path:
    img = Image.new("RGB", (64, 64), color)
    p = tmp / name
    img.save(p)
    return p


# ── Service API (TestClient, mock backend) ──────────────────────────────────


def test_service_health_and_models():
    client = TestClient(service_app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["model"] == "mock"

    r = client.get("/models")
    assert r.status_code == 200
    backends = r.json()["backends"]
    assert backends["mock"]["available"] is True
    assert backends["mock"]["selected"] is True
    # bake-off slots are registered even when not installed
    assert "tripo_sr" in backends and "trellis" in backends and "hunyuan3d" in backends


def test_service_generate_roundtrip(tmp_path):
    client = TestClient(service_app)
    img = _make_image(tmp_path)

    r = client.post(
        "/generate",
        files={"file": (img.name, img.read_bytes(), "image/png")},
        data={"target_x": "0.3", "target_y": "0.2", "target_z": "0.1"},
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    # single-job queue: mock completes quickly, but poll like a real client
    deadline = time.time() + 30
    while True:
        res = client.get(f"/result/{job_id}")
        assert res.status_code == 200
        status = res.json()["status"]
        assert status != "failed", res.json()
        if status == "completed":
            break
        assert time.time() < deadline, "mock job never completed"
        time.sleep(0.1)

    job = res.json()
    assert job["tri_count"] > 0
    assert Path(job["glb_path"]).exists()

    dl = client.get(f"/download/{job_id}")
    assert dl.status_code == 200
    assert dl.content[:4] == GLB_MAGIC


def test_service_rejects_bad_target(tmp_path):
    client = TestClient(service_app)
    img = _make_image(tmp_path)
    r = client.post(
        "/generate",
        files={"file": (img.name, img.read_bytes(), "image/png")},
        data={"target_x": "-0.3", "target_y": "0.2", "target_z": "0.1"},
    )
    assert r.status_code == 400


def test_service_unknown_job_404():
    client = TestClient(service_app)
    assert client.get("/result/nope").status_code == 404
    assert client.get("/download/nope").status_code == 404


def test_service_token_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("THREED_IMG3D_TOKEN", "sekrit")
    client = TestClient(service_app)
    img = _make_image(tmp_path)
    files = {"file": (img.name, img.read_bytes(), "image/png")}

    r = client.post("/generate", files=files)
    assert r.status_code == 401
    r = client.post("/generate", files=files, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    r = client.post("/generate", files=files, headers={"Authorization": "Bearer sekrit"})
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    assert client.get(f"/result/{job_id}").status_code == 401
    assert client.get(f"/result/{job_id}", headers={"Authorization": "Bearer sekrit"}).status_code == 200


# ── Client e2e against a live server ────────────────────────────────────────


@pytest.fixture(scope="module")
def live_service_url():
    import uvicorn

    config = uvicorn.Config(service_app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        pytest.fail("img3d test service did not start")
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"


def test_client_end_to_end(live_service_url, tmp_path):
    provider = RemoteImg3DProvider(base_url=live_service_url, poll_interval_s=0.2)
    assert provider.is_available() is True
    assert provider.is_available() is True  # cached

    img = _make_image(tmp_path)
    result = provider.generate_mesh_from_image(img, tmp_path / "out", [0.3, 0.2, 0.1])
    assert result.success, result.error
    assert Path(result.output_glb_path).exists()
    assert Path(result.output_glb_path).read_bytes()[:4] == GLB_MAGIC
    assert result.tri_count > 0

    # mock backend scales to the requested target bounds
    import trimesh

    mesh = trimesh.load(result.output_glb_path, force="mesh", process=True)
    for got, want in zip(mesh.extents, [0.3, 0.2, 0.1]):
        assert abs(got - want) < 0.005


def test_client_rejects_bad_target(live_service_url, tmp_path):
    provider = RemoteImg3DProvider(base_url=live_service_url)
    img = _make_image(tmp_path)
    result = provider.generate_mesh_from_image(img, tmp_path, [0.1, 0.0, 0.1])
    assert not result.success
    assert "target" in (result.error or "")


def test_client_dead_service():
    provider = RemoteImg3DProvider(base_url="http://127.0.0.1:9", health_timeout_s=0.5)
    assert provider.is_available() is False


# ── AgentLoop neural-part wiring (no Blender needed) ────────────────────────


class FakeImg3DProvider:
    """In-process stand-in: writes a scaled icosphere GLB, counts calls."""

    def __init__(self, available: bool = True):
        self.available = available
        self.base_url = "fake://img3d"
        self.calls = 0

    def is_available(self) -> bool:
        return self.available

    def generate_mesh_from_image(self, image_path, output_dir, target_dimensions_m=None):
        import trimesh

        self.calls += 1
        out = Path(output_dir) / f"fake_{self.calls}.glb"
        out.parent.mkdir(parents=True, exist_ok=True)
        mesh = trimesh.creation.icosphere(subdivisions=1, radius=0.1)
        if target_dimensions_m:
            mesh.apply_scale(
                [target_dimensions_m[i] / mesh.extents[i] for i in range(3)]
            )
        mesh.export(out)
        return ImageTo3DResult(
            success=True, output_glb_path=out, tri_count=len(mesh.faces), duration_sec=0.01
        )


def _neural_spec(tmp_path: Path) -> ObjectSpec:
    img = _make_image(tmp_path)
    return ObjectSpec(
        name="blob test",
        parts=[
            PartSpec(
                name="organic_blob",
                method=GenerationMethod.IMAGE_TO_3D,
                shape=ShapeType.ORGANIC,
                dimensions=[0.3, 0.3, 0.2],
                image_crop=str(img),
            )
        ],
    )


def _bare_loop(provider) -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)
    loop._img3d_provider = provider
    loop._img3d_checked = True
    return loop


def _collect(events: list[dict]):
    """Matches the loop-internal emit(event, **data) shape (run() wraps this
    into the public progress(dict) callback)."""

    def emit(event: str, **data) -> None:
        events.append({"event": event, **data})

    return emit


def test_normalize_spec_methods_routes_organic_to_neural(tmp_path):
    spec = _neural_spec(tmp_path)
    spec.parts[0].method = None  # reset to default below
    spec.parts[0].method = type(spec.parts[0]).model_fields["method"].default
    loop = _bare_loop(FakeImg3DProvider())
    loop._normalize_spec_methods(spec)
    assert spec.parts[0].method == GenerationMethod.IMAGE_TO_3D

    # parametric shapes are never rerouted
    spec.parts[0].shape = ShapeType.ROUNDED_BOX
    spec.parts[0].method = GenerationMethod.PARAMETRIC
    loop._normalize_spec_methods(spec)
    assert spec.parts[0].method == GenerationMethod.PARAMETRIC


def test_prepare_neural_parts_generates_and_caches(tmp_path):
    spec = _neural_spec(tmp_path)
    fake = FakeImg3DProvider()
    loop = _bare_loop(fake)
    events: list[dict] = []
    run_dir = tmp_path / "run"

    loop._prepare_neural_parts(spec, run_dir, [], _collect(events))
    part = spec.parts[0]
    assert part.mesh_path and Path(part.mesh_path).exists()
    assert part.target_size == [0.3, 0.3, 0.2]  # dimensions fallback
    assert fake.calls == 1
    assert [e["event"] for e in events] == ["neural_part_started", "neural_part_done"]

    # corrector-style rewrite drops mesh_path → cache restores it, no new call
    part.mesh_path = None
    loop._reattach_neural_meshes(spec, run_dir)
    assert part.mesh_path and Path(part.mesh_path).exists()
    assert fake.calls == 1


def test_prepare_neural_parts_skips_when_unavailable(tmp_path):
    spec = _neural_spec(tmp_path)
    for provider, reason in [
        (None, "disabled"),
        (FakeImg3DProvider(available=False), "unreachable"),
    ]:
        loop = _bare_loop(provider)
        events: list[dict] = []
        loop._prepare_neural_parts(spec, tmp_path / "run", [], _collect(events))
        assert spec.parts[0].mesh_path is None
        assert events and events[0]["event"] == "neural_skipped"


def test_prepare_neural_parts_no_image_error(tmp_path):
    spec = _neural_spec(tmp_path)
    spec.parts[0].image_crop = None  # and no run images passed
    spec.source_images = []
    fake = FakeImg3DProvider()
    loop = _bare_loop(fake)
    events: list[dict] = []
    loop._prepare_neural_parts(spec, tmp_path / "run", [], _collect(events))
    assert fake.calls == 0
    assert events[0]["event"] == "neural_part_error"


# ── Full hybrid build (Blender) ─────────────────────────────────────────────


@pytest.mark.blender
def test_hybrid_build_parametric_plus_neural(tmp_path):
    from src.blender.locate import locate_blender

    if locate_blender() is None:
        pytest.skip("Blender 3.3+ not found on this machine")

    img = _make_image(tmp_path, color=(90, 140, 90))
    spec = ObjectSpec(
        name="hybrid blob on pedestal",
        parts=[
            PartSpec(
                name="pedestal",
                shape=ShapeType.CYLINDER,
                dimensions=[0.20, 0.20, 0.25],
                position=[0.0, 0.0, 0.125],
            ),
            PartSpec(
                name="organic_blob",
                method=GenerationMethod.IMAGE_TO_3D,
                shape=ShapeType.ORGANIC,
                dimensions=[0.16, 0.16, 0.12],
                target_size=[0.16, 0.16, 0.12],
                position=[0.0, 0.0, 0.31],
                position_mode="center",
                image_crop=str(img),
            ),
        ],
        measurements=[
            MeasurementSpec(
                name="overall_height", target_value=0.37, applies_to="overall.height_z", tolerance_m=0.002
            ),
            MeasurementSpec(
                name="blob_width", target_value=0.16, applies_to="organic_blob.width_x", tolerance_m=0.002
            ),
        ],
    )

    loop = AgentLoop(max_iterations=2)
    loop._img3d_provider = FakeImg3DProvider()
    loop._img3d_checked = True
    events: list[dict] = []

    res = loop.run(
        prompt="hybrid test",
        spec_override=spec,
        run_dir=tmp_path / "run",
        progress=events.append,
    )

    assert res.success, f"gates failed: {res.error}"
    assert (tmp_path / "run" / "final.glb").exists()
    assert any(e["event"] == "neural_part_done" for e in events)
    # the neural mesh must actually be part of the built and measured model
    blob = [d for d in res.verification.dimension_gate.details if d["name"] == "blob_width"][0]
    assert blob["passed"]
    assert blob["actual_m"] is not None and abs(blob["actual_m"] - 0.16) < 0.002


def test_normalize_spec_methods_keeps_file_backed_sources(tmp_path):
    """Mesh-source contract: an 'organic' part that already declares a
    file-backed source (imported/scanned) KEEPS it — the loop must not
    retarget an authored mesh file at a neural generation it never asked
    for. Only parametric/script organics are rerouted to image_to_3d."""
    spec = _neural_spec(tmp_path)
    spec.parts[0].method = GenerationMethod.IMPORTED
    spec.parts[0].mesh_path = str(tmp_path / "asset.glb")
    spec.parts[0].target_size = [0.3, 0.3, 0.2]
    loop = _bare_loop(FakeImg3DProvider())
    loop._normalize_spec_methods(spec)
    assert spec.parts[0].method == GenerationMethod.IMPORTED

    spec.parts[0].method = GenerationMethod.SCANNED
    loop._normalize_spec_methods(spec)
    assert spec.parts[0].method == GenerationMethod.SCANNED

    # the pre-contract behavior stays: parametric organic → image_to_3d
    spec.parts[0].method = GenerationMethod.PARAMETRIC
    loop._normalize_spec_methods(spec)
    assert spec.parts[0].method == GenerationMethod.IMAGE_TO_3D


def test_validate_imported_parts_resolves_paths_and_fires_on_missing(tmp_path):
    """Imported/scanned mesh files are authored, never generated: the loop
    resolves each existing path to ABSOLUTE (the harness subprocess must not
    depend on the caller's CWD) and fires mesh_source_error when the file
    is absent — loud evidence, with the build skipping the part and the
    gates failing honestly downstream."""
    good = tmp_path / "good.glb"
    good.write_bytes(b"glTF")  # existence is all the loop checks here
    spec = ObjectSpec(
        name="imported test",
        parts=[
            PartSpec(
                name="asset",
                method=GenerationMethod.IMPORTED,
                shape=ShapeType.ORGANIC,
                mesh_path=str(good),
                target_size=[0.3, 0.3, 0.2],
                dimensions=[0.3, 0.3, 0.2],
            ),
            PartSpec(
                name="ghost_scan",
                method=GenerationMethod.SCANNED,
                shape=ShapeType.ORGANIC,
                mesh_path=str(tmp_path / "missing.stl"),
                target_size=[0.2, 0.2, 0.2],
                dimensions=[0.2, 0.2, 0.2],
            ),
        ],
    )
    loop = _bare_loop(None)
    events: list[dict] = []
    loop._validate_imported_parts(spec, _collect(events))

    assert Path(spec.parts[0].mesh_path).is_absolute()
    assert spec.parts[0].mesh_path == str(good.resolve())
    assert [e["event"] for e in events] == ["mesh_source_error"]
    err = events[0]
    assert err["part"] == "ghost_scan"
    assert err["method"] == "scanned"
    assert "missing.stl" in err["mesh_path"]
    assert "not found" in err["error"]
