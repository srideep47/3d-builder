"""Web UI API tests — FastAPI TestClient coverage for the studio server.

Pure-API tests run everywhere. The end-to-end spec build goes through the
real Blender pipeline and is marked `blender` (skipped when Blender is
absent), mirroring the golden benchmark suite."""

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]

MINIMAL_SPEC = {
    "schema_name": "threed-objectspec",
    "schema_version": "2.0.0",
    "name": "API Test Box",
    "units": "meters",
    "tolerance_m": 0.001,
    "measurements": [
        {"name": "overall_width", "target_value": 0.5, "unit": "meters", "applies_to": "overall.width_x"},
        {"name": "overall_height", "target_value": 0.25, "unit": "meters", "applies_to": "overall.height_z"},
    ],
    "parts": [
        {"name": "base", "shape": "box", "dimensions": [0.5, 0.5, 0.25], "position": [0.0, 0.0, 0.125]},
    ],
}

# Set by the blender e2e test so the WebSocket replay test has a real run.
_finished_run_id: str | None = None


@pytest.fixture(scope="module")
def client():
    from src.webapp.server import create_app

    with TestClient(create_app()) as c:
        yield c


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert {"blender", "ai", "config", "agent"} <= set(body)
    assert isinstance(body["blender"]["available"], bool)
    assert body["ai"]["model"]


def test_presets(client):
    res = client.get("/api/presets")
    assert res.status_code == 200
    presets = res.json()
    assert len(presets) >= 10
    assert all("name" in p and "category" in p for p in presets)


def test_static_frontend_served(client):
    assert "3D Builder" in client.get("/").text
    assert client.get("/js/app.js").status_code == 200
    assert client.get("/css/app.css").status_code == 200
    assert client.get("/vendor/three.module.js").status_code == 200
    # the missing-addon regression that once killed the whole module graph
    assert client.get("/vendor/addons/utils/BufferGeometryUtils.js").status_code == 200


def test_build_validation(client):
    assert client.post("/api/build", json={"mode": "ai"}).status_code == 400
    assert client.post("/api/build", json={"mode": "ai", "prompt": "   "}).status_code == 400
    assert client.post("/api/build", json={"mode": "spec"}).status_code == 400
    bad = client.post("/api/build", json={"mode": "spec", "spec": {"parts": [{"shape": "nonsense"}]}})
    assert bad.status_code == 400


def test_unknown_run_404(client):
    assert client.get("/api/runs/does_not_exist").status_code == 404
    assert client.get("/api/runs/does_not_exist/file/final.glb").status_code == 404
    assert client.post("/api/runs/does_not_exist/cancel").status_code == 404


def test_file_endpoint_stays_inside_run_dir(client):
    # traversal attempts must not resolve outside the run directory
    for escape in ("../pyproject.toml", "renders/../../config/ai.yaml", "specs/../../README.md"):
        res = client.get(f"/api/runs/does_not_exist/file/{escape}")
        assert res.status_code == 404


def test_upload_image(client):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    res = client.post("/api/uploads", files={"files": ("ref.png", png, "image/png")})
    assert res.status_code == 200
    saved = res.json()["files"][0]
    assert saved["size"] == len(png)
    assert Path(saved["path"]).exists()
    assert Path(saved["path"]).parent == PROJECT_ROOT / "output" / "uploads"


def test_ws_unknown_run_closes(client):
    with client.websocket_connect("/api/ws/does_not_exist") as ws:
        msg = ws.receive_json()
        assert msg == {"event": "ws_closed", "reason": "run not live"}


@pytest.mark.blender
def test_spec_build_end_to_end(client):
    global _finished_run_id

    from src.blender.locate import locate_blender

    if locate_blender() is None:
        pytest.skip("Blender 3.3+ not found on this machine")

    res = client.post("/api/build", json={"mode": "spec", "spec": MINIMAL_SPEC})
    assert res.status_code == 200
    run_id = res.json()["run_id"]

    # The run executes on a daemon thread: build -> measure -> render -> gates.
    deadline = time.time() + 120
    detail = {}
    while time.time() < deadline:
        detail = client.get(f"/api/runs/{run_id}").json()
        if not detail.get("live"):
            break
        time.sleep(0.5)
    assert not detail.get("live"), "spec run did not finish within 120s"
    _finished_run_id = run_id

    assert detail["status"] == "completed", detail.get("manifest", {}).get("metrics", {}).get("unresolved_error")
    assert detail["manifest"]["dimension_gate_passed"] is True
    assert detail["manifest"]["mesh_gate_passed"] is True
    assert detail["final_glb"] == f"/api/runs/{run_id}/file/final.glb"

    events = [e["event"] for e in detail["events"]]
    assert "run_started" in events
    assert "build_started" in events
    assert "run_finished" in events

    glb = client.get(detail["final_glb"])
    assert glb.status_code == 200
    assert glb.headers["content-type"].startswith("model/gltf-binary")
    assert glb.content[:4] == b"glTF"  # GLB magic header

    assert detail["renders"], "expected four studio renders"
    for url in detail["renders"].values():
        img = client.get(url)
        assert img.status_code == 200
        assert img.headers["content-type"].startswith("image/png")

    runs = client.get("/api/runs").json()
    assert any(r["run_id"] == run_id and not r.get("live") for r in runs)


def test_ws_replays_finished_run(client):
    if _finished_run_id is None:
        pytest.skip("no finished run available (Blender e2e was skipped)")
    with client.websocket_connect(f"/api/ws/{_finished_run_id}") as ws:
        seen = []
        while True:
            msg = ws.receive_json()
            if msg.get("event") == "ws_closed":
                break
            seen.append(msg.get("event"))
        assert "run_started" in seen
        assert "run_finished" in seen
