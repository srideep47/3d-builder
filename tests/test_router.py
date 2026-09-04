"""Router tests (§4.0.5): cheapest-first routing (template → parametric →
neural), forced-route refusal with a named reason (never a silent
fallback), and manifest recording of the decision + §3.1 diversity score.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.neural.router import (
    ROUTES,
    RouteError,
    decide_route,
    find_template,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_template(templates_dir: Path, name: str) -> Path:
    """A minimal VALID product template (bands sum to 1.0, materials defined)."""
    p = templates_dir / f"{name}.yaml"
    p.write_text(
        f"product_class: {name}\n"
        "description: router test template\n"
        "bands:\n"
        "  - name: top\n"
        "    height_fraction: 0.5\n"
        "    material: cloth\n"
        "  - name: bottom\n"
        "    height_fraction: 0.5\n"
        "    material: cloth\n"
        "textures:\n"
        "  cloth:\n"
        "    base: flat\n"
        "    roughness: 0.8\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def tpl_dir(tmp_path):
    d = tmp_path / "templates"
    d.mkdir()
    _write_template(d, "mattress")
    return d


def _img(tmp_path: Path, name: str, color=(120, 120, 120)) -> Path:
    from PIL import Image

    p = tmp_path / name
    Image.new("RGB", (48, 48), color).save(p)
    return p


def _views(tmp_path: Path, diverse: bool = True) -> list[Path]:
    if diverse:
        return [
            _img(tmp_path, "v1.png", (230, 230, 230)),
            _img(tmp_path, "v2.png", (25, 25, 25)),
            _img(tmp_path, "v3.png", (230, 25, 230)),
            _img(tmp_path, "v4.png", (25, 230, 25)),
        ]
    # near-duplicates: tiny exposure wobble on the same structure
    return [_img(tmp_path, f"d{i}.png", (120 + i, 120 + i, 120 + i)) for i in range(4)]


# ── find_template ────────────────────────────────────────────────────────────


def test_find_template_matches_stem_and_declared_class(tpl_dir):
    assert find_template("mattress", tpl_dir) is not None
    assert find_template("MATTRESS", tpl_dir) is not None  # case-insensitive
    assert find_template("pillow", tpl_dir) is None
    assert find_template(None, tpl_dir) is None


def test_find_template_skips_invalid_yaml(tmp_path):
    d = tmp_path / "templates"
    d.mkdir()
    (d / "broken.yaml").write_text("product_class: [unclosed\n", encoding="utf-8")
    assert find_template("broken", d) is not None  # stem match needs no parse
    assert find_template("other", d) is None


# ── auto routing, cheapest first ─────────────────────────────────────────────


def test_auto_template_wins_when_class_matches(tpl_dir, tmp_path):
    d = decide_route(
        prompt="a mattress with quilted top",
        product_class="mattress",
        views=_views(tmp_path),
        templates_dir=tpl_dir,
    )
    assert d.route == "template"
    assert d.template_file == "mattress.yaml"
    assert "mattress.yaml" in d.reason and "product_class" in d.reason
    assert not d.forced
    # the §3.1 diversity score rides along for the manifest
    assert d.diversity is not None and d.diversity["score"] is not None
    assert d.diversity["image_count"] == 4


def test_auto_parametric_when_no_template_and_shape_expressible(tpl_dir, tmp_path):
    d = decide_route(
        prompt="a rectangular storage box with a lid",
        product_class="box",
        templates_dir=tpl_dir,
    )
    assert d.route == "parametric"
    assert "no template" in d.reason and "primitives" in d.reason


def test_auto_neural_when_organic_keyword(tpl_dir, tmp_path):
    d = decide_route(
        prompt="a small sculpted figurine of a cat",
        product_class="decor",
        views=_views(tmp_path),
        templates_dir=tpl_dir,
    )
    assert d.route == "neural"
    assert "sculpted" in d.reason
    assert "view diversity" in d.reason


def test_auto_neural_notes_low_diversity(tpl_dir, tmp_path):
    d = decide_route(
        prompt="an amorphous organic sculpture",
        views=_views(tmp_path, diverse=False),
        templates_dir=tpl_dir,
    )
    assert d.route == "neural"
    assert "LOW VIEW DIVERSITY" in d.reason


def test_auto_parametric_notes_low_diversity(tpl_dir, tmp_path):
    d = decide_route(
        prompt="a rigid rectangular crate",
        views=_views(tmp_path, diverse=False),
        templates_dir=tpl_dir,
    )
    assert d.route == "parametric"
    assert "low view diversity" in d.reason  # reinforces the spec route


def test_auto_with_no_views_has_no_diversity(tpl_dir):
    d = decide_route(prompt="a crate", templates_dir=tpl_dir)
    assert d.diversity is None


# ── forced routes: refuse what cannot run, never fall back ──────────────────


def test_forced_template_without_template_refuses(tpl_dir):
    with pytest.raises(RouteError, match="Template route refused.*pillow"):
        decide_route(prompt="a pillow", product_class="pillow", forced="template",
                     templates_dir=tpl_dir)


def test_forced_template_with_match_runs(tpl_dir):
    d = decide_route(prompt="a mattress", product_class="mattress", forced="template",
                     templates_dir=tpl_dir)
    assert d.route == "template" and d.forced
    assert d.template_file == "mattress.yaml"


def test_forced_parametric_with_empty_prompt_refuses(tpl_dir):
    with pytest.raises(RouteError, match="Parametric route refused.*empty"):
        decide_route(prompt="   ", forced="parametric", templates_dir=tpl_dir)


def test_forced_neural_without_views_refuses(tpl_dir):
    with pytest.raises(RouteError, match="Neural route refused.*views"):
        decide_route(prompt="a sculpture", forced="neural", templates_dir=tpl_dir)


def test_forced_neural_with_views_runs(tpl_dir, tmp_path):
    d = decide_route(prompt="a sculpture", views=_views(tmp_path), forced="neural",
                     templates_dir=tpl_dir)
    assert d.route == "neural" and d.forced


def test_forced_unknown_route_refuses(tpl_dir):
    with pytest.raises(RouteError, match="unknown route"):
        decide_route(prompt="x", forced="nonsense", templates_dir=tpl_dir)


# ── decision recording (manifest contract) ──────────────────────────────────


def test_decision_to_dict_roundtrips(tpl_dir, tmp_path):
    d = decide_route(prompt="a mattress", product_class="mattress",
                     views=_views(tmp_path), templates_dir=tpl_dir)
    as_dict = d.to_dict()
    assert as_dict["route"] == "template"
    assert as_dict["reason"]
    assert as_dict["forced"] is False
    assert as_dict["template_file"] == "mattress.yaml"
    assert as_dict["diversity"]["image_count"] == 4


def test_real_repo_templates_dir_has_mattress():
    # the committed repo ships the mattress template — the router's default
    # templates dir must keep finding it
    d = decide_route(prompt="a mattress", product_class="mattress")
    assert d.route == "template"
    assert d.template_file == "mattress.yaml"


def test_route_decision_recorded_in_manifest(tmp_path):
    """§4.0.5 hard requirement: the decision and its reason land in the run
    manifest EVERY time — a disputed asset must show which path built it."""
    from src.agent.loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    # _finish only needs run_store + the passed-in state
    loop.run_store = type(
        "S",
        (),
        {
            "save_spec": lambda self, rd, spec: None,
            "save_manifest": lambda self, rd, m: (rd / "manifest.json").write_text(
                __import__("json").dumps({"metrics": m.metrics, "status": m.status}),
                encoding="utf-8",
            ),
        },
    )()
    from src.spec.schema import ObjectSpec

    result = loop._finish(
        run_dir=tmp_path,
        spec=ObjectSpec(name="route test"),
        final_glb_path=None,
        verification=None,
        iterations=0,
        renders={},
        error=None,
        run_name="test",
        started=0.0,
        route_decision={"route": "neural", "reason": "no template, organic", "forced": False},
    )
    assert result.manifest_path.exists()
    import json

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["metrics"]["route"]["route"] == "neural"
    assert manifest["metrics"]["route"]["reason"] == "no template, organic"
