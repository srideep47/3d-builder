"""Web UI API tests — FastAPI TestClient coverage for the studio server.

Pure-API tests run everywhere. The end-to-end spec build goes through the
real Blender pipeline and is marked `blender` (skipped when Blender is
absent), mirroring the golden benchmark suite."""

import json
import shutil
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


# ── delivery endpoints (T5): job intake + compliance panel ──────────────────


@pytest.fixture()
def delivery_app(tmp_path):
    """The studio app with the delivery roots pointed at temp dirs — job
    cards, templates and packages never touch the real repo state."""
    from src.webapp.server import create_app

    app = create_app()
    app.state.jobs_dir = tmp_path / "jobs"
    app.state.templates_dir = tmp_path / "templates"
    app.state.packages_root = tmp_path / "packages"
    app.state.blocked_root = tmp_path / "blocked"
    return app, tmp_path


@pytest.fixture()
def dclient(delivery_app):
    with TestClient(delivery_app[0]) as c:
        yield c


JOB_BODY = {
    "job_code": "TESTJOB01",
    "dims": {"length": 60, "width": 80, "height": 11, "unit": "in"},
    "complexity": "simple",
    "orientation": "floor",
    "product_class": "mattress",
    "part_scope": "mattress only",
}


def test_templates_endpoint_lists_valid_templates(dclient, delivery_app):
    _app, tmp = delivery_app
    (tmp / "templates").mkdir()
    shutil.copy(PROJECT_ROOT / "templates" / "mattress.yaml", tmp / "templates")
    (tmp / "templates" / "broken.yaml").write_text("product_class: 'bad class!'\n")
    templates = dclient.get("/api/templates").json()
    by_file = {t["file"]: t for t in templates}
    assert by_file["mattress.yaml"]["product_class"] == "mattress"
    assert by_file["mattress.yaml"]["tri_budget"] == 50000
    assert "error" in by_file["broken.yaml"]  # invalid templates surface, never 500


def test_create_and_list_job_card(dclient, delivery_app):
    _app, tmp = delivery_app
    res = dclient.post("/api/jobs", json=JOB_BODY)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["dims_placeholder"] is False

    # the written card round-trips through the loader the CLI uses
    from src.client.job import load_job

    card = load_job(tmp / "jobs" / "TESTJOB01.yaml")
    assert card.job_code == "TESTJOB01"
    assert card.expected_bounds_m() == {"x": 1.524, "y": 2.032, "z": 0.2794}

    jobs = dclient.get("/api/jobs").json()
    assert jobs and jobs[0]["job_code"] == "TESTJOB01"
    assert jobs[0]["dims"]["unit"] == "in"
    assert jobs[0]["dims_placeholder"] is False


def test_create_job_placeholder_flags_refusal(dclient, delivery_app):
    _app, tmp = delivery_app
    res = dclient.post("/api/jobs", json={**JOB_BODY, "dims_placeholder": True})
    assert res.status_code == 200
    raw = (tmp / "jobs" / "TESTJOB01.yaml").read_text(encoding="utf-8")
    assert "dims_placeholder: true" in raw
    assert "never inferred" in raw  # the rule-9 warning comment
    jobs = dclient.get("/api/jobs").json()
    assert jobs[0]["dims_placeholder"] is True


def test_create_job_validation_never_infers(dclient):
    # rule 9: dims + explicit unit are REQUIRED, always
    no_unit = {**JOB_BODY, "dims": {"length": 1, "width": 1, "height": 1}}
    assert dclient.post("/api/jobs", json=no_unit).status_code == 400
    zero = {**JOB_BODY, "dims": {"length": 0, "width": 1, "height": 1, "unit": "in"}}
    assert dclient.post("/api/jobs", json=zero).status_code == 400
    unknown_unit = {**JOB_BODY, "dims": {**JOB_BODY["dims"], "unit": "hands"}}
    assert dclient.post("/api/jobs", json=unknown_unit).status_code == 400
    bad_code = {**JOB_BODY, "job_code": "../escape"}
    assert dclient.post("/api/jobs", json=bad_code).status_code == 400
    bad_complexity = {**JOB_BODY, "complexity": "ultra"}
    assert dclient.post("/api/jobs", json=bad_complexity).status_code == 400
    assert not list(Path(dclient.app.state.jobs_dir).glob("*.yaml"))


def test_create_job_never_overwrites(dclient):
    assert dclient.post("/api/jobs", json=JOB_BODY).status_code == 200
    again = dclient.post("/api/jobs", json=JOB_BODY)
    assert again.status_code == 409


def _write_qa(root: Path, job_code: str, refused: bool = False) -> None:
    d = root / job_code
    d.mkdir(parents=True)
    gates = None if refused else [
        {"gate": "dimensions", "passed": True, "expected": "1.52 m",
         "received": "1.52 m", "message": ""},
        {"gate": "ngon_free", "passed": False, "expected": "0",
         "received": "3", "message": "n-gons present"},
    ]
    report = {
        "job_code": job_code, "refused": refused,
        "all_passed": None if refused else False,
        "gates": gates, "package_dir": str(d),
    }
    if refused:
        report["refusal_reason"] = "dims_placeholder (test)"
        report["unblock"] = "supply real dimensions"
    (d / "qa_report.json").write_text(json.dumps(report), encoding="utf-8")


def test_packages_list_and_detail(dclient, delivery_app):
    _app, tmp = delivery_app
    _write_qa(tmp / "packages", "PKGOK1")
    _write_qa(tmp / "blocked", "PKGBAD1", refused=True)

    pkgs = dclient.get("/api/packages").json()
    by_code = {p["job_code"]: p for p in pkgs}
    assert by_code["PKGOK1"]["kind"] == "package"
    assert by_code["PKGOK1"]["gates_passed"] == 1 and by_code["PKGOK1"]["gates_total"] == 2
    assert by_code["PKGBAD1"]["kind"] == "blocked"
    assert by_code["PKGBAD1"]["refused"] is True

    detail = dclient.get("/api/packages/PKGBAD1").json()
    assert detail["kind"] == "blocked"
    assert detail["report"]["refusal_reason"] == "dims_placeholder (test)"
    assert dclient.get("/api/packages/NOPE").status_code == 404


def test_validate_package_refusal_paths(dclient, delivery_app):
    _app, tmp = delivery_app
    # no package dir
    assert dclient.post("/api/packages/GHOST1/validate").status_code == 404
    # package without a job card
    _write_qa(tmp / "packages", "ORPHAN1")
    assert dclient.post("/api/packages/ORPHAN1/validate").status_code == 400
    # placeholder-dims card: validation is REFUSED (rule 9)
    _write_qa(tmp / "packages", "HOLD1")
    dclient.post("/api/jobs", json={**JOB_BODY, "job_code": "HOLD1",
                                    "dims_placeholder": True})
    refused = dclient.post("/api/packages/HOLD1/validate")
    assert refused.status_code == 409
    assert "REFUSED" in refused.json()["detail"]


def test_validate_package_runs_gates(dclient, delivery_app, monkeypatch):
    """The live re-validation wiring: gates come from run_all_gates against
    the package dir + job card (Blender fact-gathering stubbed out)."""
    import src.webapp.server as server_mod

    _app, tmp = delivery_app
    _write_qa(tmp / "packages", "LIVEVAL1")
    dclient.post("/api/jobs", json={**JOB_BODY, "job_code": "LIVEVAL1"})

    monkeypatch.setattr(server_mod, "locate_blender", lambda: None)
    seen: dict = {}

    class _FakeResult:
        def __init__(self, name, passed):
            self.passed = passed
            self._name = name

        def to_dict(self):
            return {"gate": self._name, "passed": self.passed,
                    "expected": "e", "received": "r", "message": "m"}

    def _fake_run_all_gates(pkg, job, facts):
        seen["pkg"] = str(pkg)
        seen["job"] = job.job_code
        seen["facts"] = facts
        return [_FakeResult("dimensions", True), _FakeResult("ngon_free", False)]

    monkeypatch.setattr(server_mod, "run_all_gates", _fake_run_all_gates)
    res = dclient.post("/api/packages/LIVEVAL1/validate")
    assert res.status_code == 200
    body = res.json()
    assert body["all_passed"] is False
    assert body["blender_facts"] is False  # no Blender → fail closed, honestly reported
    assert [g["gate"] for g in body["gates"]] == ["dimensions", "ngon_free"]
    assert seen["job"] == "LIVEVAL1"
    assert Path(seen["pkg"]).name == "LIVEVAL1"
