"""Phase 8 item 1: per-surface texel priority (the illegible-label fix).

A uniform atlas gives every surface the same texel density — that starves
printed text while over-serving velvet. `texel_priority` lets the spec
redistribute the shared atlas budget: a label at 4.0 gets 4x the texels per
metre, and total atlas use is unchanged because the packer renormalises
rho over the priority-squared weighted world area.

Pinned here:
- schema bounds + resolver passthrough — the default 1.0 is OMITTED from
  build params so historic uniform packs stay byte-identical;
- the template decal patch defaults to 4.0 (a brand label is the canonical
  high-detail surface) and every other template part stays at 1.0;
- the atlas packer honours the multiplier through `prepare_delivery_scene`:
  a 4x-priority part measures ~4x the plain part's texels per metre, the
  atlas stays valid (in bounds, zero overlapping texels), the RAW density
  ratio honestly reports the authored spread, and the priority-weighted
  ratio — the uniformity metric that replaces the raw ratio when spread is
  authored — stays ~1.0;
- with no priorities authored, weighted == raw (the historic metric,
  unchanged).
"""

from pathlib import Path

import pytest

from src.spec.schema import ObjectSpec, PartSpec
from src.spec.resolver import resolve_spec_to_build_params

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _part_spec(**overrides):
    base = dict(name="base", dimensions=[0.508, 0.3048, 0.2286],
                position=[0.0, 0.0, 0.1143])
    return PartSpec(**{**base, **overrides})


def _one_part_spec(**overrides):
    return ObjectSpec(name="texel fixture", parts=[_part_spec(**overrides)])


# ── schema + resolver (no Blender) ──────────────────────────────────────────


def test_default_priority_omitted_from_build_params():
    params = resolve_spec_to_build_params(_one_part_spec())
    assert "texel_priority" not in params["spec"]["parts"][0], \
        "default 1.0 must not appear in build params (historic packs unchanged)"


def test_priority_passthrough():
    params = resolve_spec_to_build_params(_one_part_spec(texel_priority=4.0))
    assert params["spec"]["parts"][0]["texel_priority"] == pytest.approx(4.0)


@pytest.mark.parametrize("bad", [0.24, 16.1, -1.0])
def test_priority_bounds_reject(bad):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _one_part_spec(texel_priority=bad)


def test_priority_bounds_accept_edges():
    assert _one_part_spec(texel_priority=0.25).parts[0].texel_priority == 0.25
    assert _one_part_spec(texel_priority=16.0).parts[0].texel_priority == 16.0


def test_analyst_prompt_documents_priority():
    # the prompt/harness sync invariant (like the shape enum): the analyst
    # authors specs, so it must know the field exists
    from src.agent.prompts import ANALYST_SYSTEM_PROMPT

    assert "texel_priority" in ANALYST_SYSTEM_PROMPT


# ── template decal (no Blender) ─────────────────────────────────────────────


def test_template_decal_patch_defaults_to_four():
    from src.client.job import load_job
    from src.spec.template import compile_spec, load_template

    template = load_template(PROJECT_ROOT / "templates" / "mattress.yaml")
    job = load_job(PROJECT_ROOT / "input" / "jobs" / "MAYA00053153.yaml")
    spec, _warnings = compile_spec(template, job)
    parts = {p.name: p for p in spec.parts}
    assert parts["decal_patch"].texel_priority == pytest.approx(4.0)
    assert all(p.texel_priority == 1.0
               for n, p in parts.items() if n != "decal_patch"), \
        "only the decal carries a non-default priority in the stock template"
    # ...and it survives the resolver into build params
    params = resolve_spec_to_build_params(spec)
    bp = {p["name"]: p for p in params["spec"]["parts"]}
    assert bp["decal_patch"]["texel_priority"] == pytest.approx(4.0)


# ── atlas behaviour (Blender) ───────────────────────────────────────────────


def _get_runner():
    from src.blender.locate import locate_blender
    from src.blender.runner import BlenderRunner

    if locate_blender() is None:
        pytest.skip("Blender 3.3+ not found on this machine")
    return BlenderRunner()


@pytest.fixture(scope="module")
def runner():
    return _get_runner()


@pytest.fixture(scope="module")
def priority_uv(runner):
    """Two-box scene: a plain base and a small label plate on its +Y face at
    4x texel priority (the mattress-decal shape: a patch floating just off
    the wall, never widening the silhouette)."""
    spec = ObjectSpec(name="texel priority fixture", parts=[
        PartSpec(name="base", dimensions=[0.508, 0.3048, 0.2286],
                 position=[0.0, 0.0, 0.1143]),
        PartSpec(name="label_plate", dimensions=[0.12, 0.08, 0.004],
                 position=[0.0, 0.155, 0.20], texel_priority=4.0),
    ])
    result = runner.execute_op("prepare_delivery_scene", {
        "build": resolve_spec_to_build_params(spec),
    })
    assert result["success"], result.get("error")
    return result["uv"]


@pytest.mark.blender
def test_priority_part_gets_four_times_texels(priority_uv):
    per_obj = priority_uv["texel_density_per_object"]
    assert set(per_obj) == {"base", "label_plate"}
    assert per_obj["label_plate"]["texel_priority"] == pytest.approx(4.0)
    assert per_obj["base"]["texel_priority"] == pytest.approx(1.0)
    ratio = (per_obj["label_plate"]["texels_per_m"]
             / per_obj["base"]["texels_per_m"])
    assert ratio == pytest.approx(4.0, rel=0.05), \
        f"priority part got {ratio:.3f}x the plain part's texels, expected ~4x"


@pytest.mark.blender
def test_atlas_still_valid_under_priorities(priority_uv):
    assert priority_uv["in_bounds"] is True
    assert priority_uv["overlapping_island_pairs"] == 0
    assert priority_uv["overlapping_texels"] == 0


@pytest.mark.blender
def test_weighted_ratio_uniform_while_raw_reports_spread(priority_uv):
    tdp = priority_uv["texel_density_texels_per_m"]
    assert tdp["ratio"] == pytest.approx(4.0, rel=0.08), \
        "raw ratio must honestly report the authored 4x spread"
    assert tdp["ratio_priority_weighted"] < 1.05, \
        "priority-weighted ratio is the uniformity metric — must stay ~1.0"


@pytest.mark.blender
def test_default_priorities_leave_weighted_equal_to_raw(runner):
    """No priorities authored -> ratio_priority_weighted == raw ratio exactly
    (the historic metric, unchanged for every existing spec)."""
    spec = ObjectSpec(name="uniform fixture", parts=[
        PartSpec(name="base", dimensions=[0.508, 0.3048, 0.2286],
                 position=[0.0, 0.0, 0.1143]),
        PartSpec(name="boss", dimensions=[0.1016, 0.1016, 0.0508],
                 position=[0.127, 0.0762, 0.254]),
    ])
    result = runner.execute_op("prepare_delivery_scene", {
        "build": resolve_spec_to_build_params(spec),
    })
    assert result["success"], result.get("error")
    tdp = result["uv"]["texel_density_texels_per_m"]
    assert tdp["ratio_priority_weighted"] == pytest.approx(tdp["ratio"])
    assert tdp["ratio"] < 1.05
