"""Neural intake API tests (§4.1 + §4.0.5 wiring): the preview endpoint
(diversity score + warning, Auto route decision with reason, rule-9 dims
verdict), forced-route REFUSAL, and the /api/build route dispatch. The
heavy runners (start_neural/start_template/start_ai) are monkeypatched —
these tests own the WIRING, not the builds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _img(tmp: Path, name: str, color) -> Path:
    p = tmp / name
    Image.new("RGB", (64, 64), color).save(p)
    return p


def _structured_img(tmp: Path, name: str, seed: int) -> Path:
    """Deterministic noise — dHash reads gradient STRUCTURE, so flat colors
    all hash identically; genuinely different viewpoints need genuinely
    different structure."""
    import numpy as np

    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8)
    p = tmp / name
    Image.fromarray(arr, mode="RGB").save(p)
    return p


def _diverse_views(tmp: Path) -> dict[str, str]:
    return {
        "front": str(_structured_img(tmp, "v_front.png", 1)),
        "back": str(_structured_img(tmp, "v_back.png", 2)),
        "left": str(_structured_img(tmp, "v_left.png", 3)),
        "right": str(_structured_img(tmp, "v_right.png", 4)),
    }


def _near_duplicate_views(tmp: Path) -> dict[str, str]:
    return {label: str(_img(tmp, f"d{i}.png", (120 + i, 120 + i, 120 + i)))
            for i, label in enumerate(("front", "back", "left", "right"))}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """App with jobs/templates dirs pointed at temp dirs (so card writes
    and template lookups are hermetic) and the heavy runners stubbed."""
    from src.webapp.server import create_app

    jobs = tmp_path / "jobs"
    templates = tmp_path / "templates"
    jobs.mkdir()
    templates.mkdir()
    (templates / "mattress.yaml").write_text(
        "product_class: mattress\n"
        "description: test\n"
        "bands:\n"
        "  - name: top\n"
        "    height_fraction: 0.5\n"
        "    material: cloth\n"
        "  - name: bottom\n"
        "    height_fraction: 0.5\n"
        "    material: cloth\n"
        "textures:\n"
        "  cloth:\n"
        "    base: flat\n",
        encoding="utf-8",
    )

    # hermetic runs root — get_run tests fabricate run dirs under it; the
    # patch must land BEFORE create_app() constructs the store
    from src.run_store import RunStore

    monkeypatch.setattr("src.webapp.server.RunStore",
                        lambda: RunStore(root_dir=tmp_path / "runs"))
    app = create_app()
    app.state.jobs_dir = jobs
    app.state.templates_dir = templates

    started: dict[str, dict] = {}

    def _stub(method):
        def record(*args, **kwargs):
            started[method] = {"args": args, "kwargs": kwargs}
            return "run_fake"
        return record

    app.state.registry.start_neural = _stub("neural")
    app.state.registry.start_template = _stub("template")
    app.state.registry.start_ai = _stub("ai")
    with TestClient(app) as c:
        c.app.state._started = started  # type: ignore[attr-defined]
        yield c


# ── /api/intake/preview ─────────────────────────────────────────────────────


def test_preview_shows_route_reason_and_diversity(client, tmp_path):
    res = client.post("/api/intake/preview", json={
        "prompt": "a small sculpted figurine, 0.1 x 0.08 x 0.15 m",
        "views": _diverse_views(tmp_path),
        "product_class": "decor",
        "route": "auto",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["route"]["route"] == "neural"
    assert "sculpted" in body["route"]["reason"]
    assert body["diversity"]["image_count"] == 4
    assert body["diversity"]["score"] is not None
    assert body["diversity"]["warned"] is False
    assert body["intake"]["ok"] is True


def test_preview_flags_low_view_diversity_never_refuses(client, tmp_path):
    res = client.post("/api/intake/preview", json={
        "prompt": "a sculpted figurine, 0.1 x 0.08 x 0.15 m",
        "views": _near_duplicate_views(tmp_path),
        "product_class": "decor",
    })
    assert res.status_code == 200
    body = res.json()
    # §5: low diversity is NOT a stop — warn loudly, record, run anyway
    assert body["diversity"]["warned"] is True
    assert "LOW VIEW DIVERSITY" in body["route"]["reason"]


def test_preview_template_route_with_reason(client, tmp_path):
    res = client.post("/api/intake/preview", json={
        "prompt": "a mattress, 2.03 x 1.52 x 0.3 m",
        "product_class": "mattress",
        "route": "auto",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["route"]["route"] == "template"
    assert "mattress.yaml" in body["route"]["reason"]
    assert body["route"]["template_file"] == "mattress.yaml"


def test_preview_rule9_no_dims_refuses(client, tmp_path):
    res = client.post("/api/intake/preview", json={
        "prompt": "a sculpted figurine with no dimensions stated",
        "views": _diverse_views(tmp_path),
    })
    assert res.status_code == 200  # the preview itself works…
    body = res.json()
    assert body["intake"]["ok"] is False  # …and reports the refusal
    assert "rule 9" in body["intake"]["error"]


def test_preview_forced_route_that_cannot_run_refuses(client, tmp_path):
    res = client.post("/api/intake/preview", json={
        "prompt": "a pillow, 0.5 x 0.4 x 0.15 m",
        "product_class": "pillow",
        "route": "template",
    })
    assert res.status_code == 400
    assert "Template route refused" in res.json()["detail"]
    assert "pillow" in res.json()["detail"]


def test_preview_unknown_view_label_400(client, tmp_path):
    res = client.post("/api/intake/preview", json={
        "prompt": "x, 1 x 1 x 1 m",
        "views": {"top": str(_img(tmp_path, "t.png", (0, 0, 0)))},
    })
    assert res.status_code == 400
    assert "unknown view label" in res.json()["detail"]


# ── /api/build route dispatch ───────────────────────────────────────────────


def test_build_neural_route_dispatch(client, tmp_path):
    res = client.post("/api/build", json={
        "mode": "neural",
        "prompt": "a sculpted figurine, 0.1 x 0.08 x 0.15 m",
        "views": _diverse_views(tmp_path),
        "product_class": "decor",
        "declared_fabric": True,
        "max_tris": 40000,
        "job_code": "NEURAPI1",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["run_id"] == "run_fake"
    assert body["route"]["route"] == "neural"
    started = client.app.state._started  # type: ignore[attr-defined]
    assert set(started) == {"neural"}
    kwargs = started["neural"]["kwargs"]
    assert kwargs["job_card"].job_code == "NEURAPI1"
    assert kwargs["declared_fabric"] is True
    assert kwargs["max_tris"] == 40000
    assert kwargs["route_decision"]["route"] == "neural"
    assert sorted(kwargs["views"]) == ["back", "front", "left", "right"]
    # the card was persisted for provenance (rule 9 trail)
    card_path = Path(client.app.state.jobs_dir) / "NEURAPI1.yaml"  # type: ignore[attr-defined]
    assert card_path.exists()
    assert "0.1" in card_path.read_text(encoding="utf-8")


def test_build_form_dims_satisfy_rule9(client, tmp_path):
    """Form dims are explicit owner values — a prompt without dims does NOT
    refuse when the form carries them (S2 only fires when BOTH are silent)."""
    res = client.post("/api/build", json={
        "mode": "neural",
        "prompt": "a sculpted figurine",
        "dims": {"length": 0.1, "width": 0.08, "height": 0.15, "unit": "M"},
        "views": _diverse_views(tmp_path),
        "product_class": "decor",
        "job_code": "NEURAPI2",
    })
    assert res.status_code == 200
    started = client.app.state._started  # type: ignore[attr-defined]
    card = started["neural"]["kwargs"]["job_card"]
    assert card.dims_placeholder is False
    assert (card.dims.length, card.dims.width, card.dims.height) == (0.1, 0.08, 0.15)


def test_build_contradictory_prompt_and_form_dims_refused(client, tmp_path):
    res = client.post("/api/build", json={
        "mode": "neural",
        "prompt": "a sculpted figurine, 0.2 x 0.1 x 0.3 m",
        "dims": {"length": 0.1, "width": 0.08, "height": 0.15, "unit": "M"},
        "views": _diverse_views(tmp_path),
    })
    assert res.status_code == 400
    assert "contradictory" in res.json()["detail"]


def test_build_no_dims_anywhere_refused(client, tmp_path):
    res = client.post("/api/build", json={
        "mode": "neural",
        "prompt": "a sculpted figurine",
        "views": _diverse_views(tmp_path),
    })
    assert res.status_code == 400
    assert "rule 9" in res.json()["detail"]


def test_build_neural_route_without_views_400(client):
    res = client.post("/api/build", json={
        "mode": "neural",
        "prompt": "a sculpted figurine, 0.1 x 0.08 x 0.15 m",
    })
    assert res.status_code == 400
    assert "views" in res.json()["detail"]


def test_build_auto_routes_mattress_to_template(client, tmp_path):
    res = client.post("/api/build", json={
        "prompt": "a mattress, 2.03 x 1.52 x 0.3 m",
        "dims": {"length": 2.03, "width": 1.52, "height": 0.3, "unit": "M"},
        "product_class": "mattress",
        "route": "auto",
        "job_code": "TPLAPI1",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["route"]["route"] == "template"
    started = client.app.state._started  # type: ignore[attr-defined]
    assert set(started) == {"template"}
    args = started["template"]["args"]
    assert Path(args[1]).name == "mattress.yaml"
    assert args[0].job_code == "TPLAPI1"
    assert args[2]["route"] == "template"


def test_build_auto_parametric_dispatch(client, tmp_path):
    res = client.post("/api/build", json={
        "prompt": "a rigid rectangular storage crate, 0.6 x 0.4 x 0.3 m",
        "views": _diverse_views(tmp_path),
        "product_class": "crate",
        "route": "auto",
        "job_code": "PARAPI1",
    })
    assert res.status_code == 200
    started = client.app.state._started  # type: ignore[attr-defined]
    assert set(started) == {"ai"}
    kwargs = started["ai"]["kwargs"]
    assert kwargs["route_decision"]["route"] == "parametric"
    assert kwargs["job_card"].job_code == "PARAPI1"
    # the four reference images ride along as analyst references
    assert len(kwargs["images"]) == 4


def test_build_forced_route_refusal_is_400(client, tmp_path):
    res = client.post("/api/build", json={
        "prompt": "a pillow, 0.5 x 0.4 x 0.15 m",
        "product_class": "pillow",
        "route": "template",
    })
    assert res.status_code == 400
    assert "Template route refused" in res.json()["detail"]


def test_build_legacy_ai_mode_unchanged(client):
    """No route field → the pre-neural behaviour: plain start_ai, no card."""
    res = client.post("/api/build", json={"mode": "ai", "prompt": "a simple box"})
    assert res.status_code == 200
    started = client.app.state._started  # type: ignore[attr-defined]
    assert set(started) == {"ai"}
    kwargs = started["ai"]["kwargs"]
    assert kwargs["prompt"] == "a simple box"
    assert "route_decision" not in kwargs  # legacy path passes no route
    assert "job_card" not in kwargs


# ── /api/runs/<id> for neural runs (history reload) ────────────────────────


def test_get_run_serves_neural_glb_via_manifest_path(client):
    """Neural runs have no final.glb — the GLB lives at neural/<name>.glb and
    get_run must expose it as a run-relative URL the viewer can fetch."""
    import json

    store = client.app.state.store
    run_dir = store.create_run("ui_neural")
    glb = run_dir / "neural" / "gen.glb"
    glb.parent.mkdir(parents=True, exist_ok=True)
    glb.write_bytes(b"fake-glb-bytes")
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": run_dir.name,
        "final_glb_path": str(glb),
        "metrics": {
            "route": {"route": "neural", "reason": "t", "forced": False},
            "analyse": {"passed": True, "triangles": 12, "checks": []},
            "package": {"all_passed": True,
                        "gates": [{"gate": "Dimensions", "passed": True,
                                   "expected": "x", "received": "y"}]},
        },
        "status": "completed",
    }), encoding="utf-8")

    res = client.get(f"/api/runs/{run_dir.name}")
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == "neural"
    assert body["final_glb"] == f"/api/runs/{run_dir.name}/file/neural/gen.glb"

    # …and the file itself actually serves through that URL
    res2 = client.get(body["final_glb"])
    assert res2.status_code == 200
    assert res2.content == b"fake-glb-bytes"


def test_get_run_ignores_glb_outside_run_dir(client):
    """The template route's final_glb_path points into output/packages/ —
    outside the run dir it must NOT be exposed (the file route is scoped)."""
    import json

    store = client.app.state.store
    run_dir = store.create_run("ui_template")
    outside = store.base_dir.parent / "elsewhere.glb"
    outside.write_bytes(b"nope")
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": run_dir.name,
        "final_glb_path": str(outside),
        "metrics": {"route": {"route": "template", "reason": "t", "forced": False}},
        "status": "completed",
    }), encoding="utf-8")

    res = client.get(f"/api/runs/{run_dir.name}")
    assert res.status_code == 200
    assert res.json()["final_glb"] is None
    outside.unlink()
