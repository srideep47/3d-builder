"""Phase 3.1 agent delivery tools (finish / inspect / review / package).

The Blender boundary is stubbed (canned op results): these tests pin the
executor's dispatch, state threading (last_spec / last_built_glb), gate
computation, the rule-9 placeholder refusal as a TOOL RESULT, and verdict
caching/escalation. Vision runs against fakes only — S1: no live vision
calls while billing is unconfirmed. The real-Blender round trip is the
blender-marked test at the bottom (auto-skip without a Blender install).
"""

import json
from pathlib import Path

import pytest
import yaml

from src.agent.tools import (AGENT_TOOLS_SCHEMA, AgentToolExecutor,
                             advisory_visual_verdict)
from src.spec.schema import ObjectSpec

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAYA_JOB = PROJECT_ROOT / "input" / "jobs" / "MAYA00053153.yaml"


# ── fixtures ──────────────────────────────────────────────────────────────────


BOX_SPEC = {
    "schema_version": "2.0.0",
    "name": "agent_box",
    "tolerance_m": 0.001,
    "tri_budget": 1000,
    "parts": [{
        "name": "body", "shape": "box",
        "dimensions": [0.4, 0.3, 0.2], "position": [0.0, 0.0, 0.1],
    }],
    "measurements": [
        {"name": "overall_length", "target_value": 0.4, "applies_to": "overall.width_x"},
        {"name": "overall_width", "target_value": 0.3, "applies_to": "overall.depth_y"},
        {"name": "overall_height", "target_value": 0.2, "applies_to": "overall.height_z"},
    ],
    "constraints": [{"type": "ground_contact", "parts": ["body"]}],
}


class StubRunner:
    """Canned harness: scripted op results, every call recorded."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def execute_op(self, op, params=None, timeout_sec=None):
        self.calls.append((op, params))
        res = self.responses.get(op, {"success": True})
        return dict(res)


def _measure_ok():
    return {
        "success": True,
        "overall": {
            "dimensions": [0.4, 0.3, 0.2],
            "min": [-0.2, -0.15, 0.0],
            "max": [0.2, 0.15, 0.2],
            "center": [0.0, 0.0, 0.1],
        },
        "parts": {
            "body": {
                "dimensions": [0.4, 0.3, 0.2],
                "min": [-0.2, -0.15, 0.0],
                "max": [0.2, 0.15, 0.2],
                "center": [0.0, 0.0, 0.1],
                "bottom_z": 0.0, "top_z": 0.2,
                "vertices": 8, "faces": 6,
            }
        },
    }


def _topology_ok(model_path="m.glb"):
    bounds = {"dimensions": [0.4, 0.3, 0.2],
              "min": [-0.2, -0.15, 0.0], "max": [0.2, 0.15, 0.2]}
    detail = {
        "name": "body", "vertices": 8, "faces_total": 6, "triangles": 6,
        "quads": 0, "ngons": 0, "triangle_equivalent": 6,
        "loose_vertices": 0, "loose_edges": 0, "boundary_edges": 24,
        "nonmanifold_edges": 0, "closed_solid": True, "bounds": bounds,
    }
    return {
        "success": True, "model_path": model_path, "units": "meters",
        "objects": 1, "vertices": 8, "faces_total": 6, "triangles": 6,
        "quads": 0, "ngons": 0, "triangle_equivalent": 6,
        "loose_vertices": 0, "loose_edges": 0, "boundary_edges": 24,
        "nonmanifold_edges": 0, "bounds": bounds,
        "objects_detail": [detail],
    }


def _uv_ok(model_path="m.glb", resolution=1024):
    return {
        "success": True, "model_path": model_path, "resolution": resolution,
        "uv": {
            "islands_total": 2, "verified": True, "in_bounds": True,
            "texel_density_per_object": {
                "body": {"islands": 2, "texels_per_m": 512.0,
                          "atlas_share": 1.0, "world_area_share": 1.0},
            },
        },
    }


def _write_job(tmp_path, placeholder=False, code="AGT0001"):
    card = {
        "job_code": code,
        "dims": {"length": 16, "width": 12, "height": 5, "unit": "IN"},
        "complexity": "simple",
        "orientation": "floor",
        "product_class": "agent_test",
        "reference_dir": "input/references",
    }
    if placeholder:
        card["dims_placeholder"] = True
    p = tmp_path / f"{code}.yaml"
    p.write_text(yaml.safe_dump(card), encoding="utf-8")
    return p


def _make_png(path):
    from PIL import Image

    Image.new("RGB", (16, 16), (90, 90, 130)).save(path)
    return path


def _executor(tmp_path, responses=None):
    ex = AgentToolExecutor(runner=StubRunner(responses), workdir=tmp_path / "work")
    ex.last_spec = ObjectSpec.model_validate(BOX_SPEC)
    glb = tmp_path / "built.glb"
    glb.write_bytes(b"stub-glb")
    ex.last_built_glb = str(glb)
    return ex


def _review_executor(tmp_path, stub):
    """Executor with a built model for review tests (spec optional)."""
    ex = AgentToolExecutor(runner=stub, workdir=tmp_path / "w")
    glb = tmp_path / "model.glb"
    glb.write_bytes(b"stub-glb")
    ex.last_built_glb = str(glb)
    return ex


class CountingVLM:
    """Fake vision provider: verdicts by call, call count recorded."""

    def __init__(self, escalation_model=None):
        self.calls = 0
        self.escalation_model = escalation_model

    def is_available(self):
        return True

    def visual_verdict(self, renders, refs, model_summary="", escalate=False):
        self.calls += 1
        if escalate:
            return {"available": True, "parsed": True, "matches_reference": True,
                    "score": 8, "issues": [], "summary": "ok", "escalated": True}
        return {"available": True, "parsed": True, "matches_reference": False,
                "score": 4, "issues": ["proportions"], "summary": "meh"}


# ── schema registration ───────────────────────────────────────────────────────


def test_delivery_tools_registered_in_schema():
    names = {t["function"]["name"] for t in AGENT_TOOLS_SCHEMA}
    assert {"build_spec", "measure_model", "render_model",
            "inspect", "review", "finish", "package"} <= names
    # the brain never writes raw Blender Python (Phase 3.0, still pinned here)
    for tool in AGENT_TOOLS_SCHEMA:
        assert "code" not in tool["function"]["parameters"].get("properties", {})
        assert tool["function"]["name"] != "execute_blender_script"
    # measured facts, never prose: finish/package document the refusal path
    by_name = {t["function"]["name"]: t for t in AGENT_TOOLS_SCHEMA}
    assert "refused" in by_name["finish"]["function"]["description"]
    assert "rule 9" in by_name["package"]["function"]["description"]


def test_build_spec_records_last_spec(tmp_path):
    ex = AgentToolExecutor(runner=StubRunner({"build_from_spec": {"success": True}}),
                           workdir=tmp_path)
    assert ex.last_spec is None
    r = ex.execute("build_spec", {"spec": BOX_SPEC})
    assert r["success"]
    assert isinstance(ex.last_spec, ObjectSpec)
    assert ex.last_spec.name == "agent_box"
    assert Path(ex.last_built_glb).is_absolute()


# ── inspect ───────────────────────────────────────────────────────────────────


def test_inspect_green_path_all_gates_with_values(tmp_path):
    ex = _executor(tmp_path, {
        "measure": _measure_ok(),
        "topology_report": _topology_ok(),
        "uv_report": _uv_ok(),
    })
    r = ex.execute("inspect", {})
    assert r["success"], r.get("error")
    assert r["units"] == "meters"
    # measured facts, each with its value
    assert r["polycount"]["triangle_equivalent"] == 6
    assert r["polycount"]["ngons"] == 0
    assert r["parts"]["body"]["dimensions_m"] == [0.4, 0.3, 0.2]
    assert r["parts"]["body"]["bottom_z_m"] == 0.0
    assert r["parts"]["body"]["faces_total"] == 6
    assert r["parts"]["body"]["closed_solid"] is True
    assert r["uv"]["available"] is True
    assert r["uv"]["texel_density_per_object"]["body"]["texels_per_m"] == 512.0
    # every gate WITH its value
    assert r["gates"]["polycount"] == {"passed": True, "triangle_equivalent": 6,
                                        "budget": 1000}
    assert r["gates"]["ngons"] == {"passed": True, "count": 0}
    assert r["gates"]["closed_solids"] == {"passed": True, "open_parts": []}
    dim = r["gates"]["dimensions"]
    assert dim["passed"] is True and dim["failed_count"] == 0
    assert dim["ground_contact_passed"] is True
    assert dim["details"][0]["actual_m"] == 0.4
    assert dim["details"][0]["delta_mm"] == 0.0
    assert r["all_gates_passed"] is True


def test_inspect_flags_failures_with_values(tmp_path):
    topo = _topology_ok()
    topo["ngons"] = 1
    topo["triangle_equivalent"] = 2000
    topo["objects_detail"][0]["ngons"] = 1
    topo["objects_detail"][0]["closed_solid"] = False
    measure = _measure_ok()
    # floating part: body lifted 30 mm off the ground plane
    measure["overall"]["min"][2] = 0.03
    measure["parts"]["body"]["min"][2] = 0.03
    measure["parts"]["body"]["bottom_z"] = 0.03
    ex = _executor(tmp_path, {"measure": measure, "topology_report": topo,
                              "uv_report": _uv_ok()})
    r = ex.execute("inspect", {})
    assert r["success"]
    assert r["all_gates_passed"] is False
    assert r["gates"]["polycount"] == {"passed": False, "triangle_equivalent": 2000,
                                        "budget": 1000}
    assert r["gates"]["ngons"] == {"passed": False, "count": 1}
    assert r["gates"]["closed_solids"] == {"passed": False, "open_parts": ["body"]}
    dim = r["gates"]["dimensions"]
    assert dim["passed"] is False
    assert dim["ground_contact_passed"] is False
    assert any("body" in f for f in dim["ground_contact_failures"])


def test_inspect_without_spec_skips_dimension_gate(tmp_path):
    ex = _executor(tmp_path, {"measure": _measure_ok(),
                              "topology_report": _topology_ok(),
                              "uv_report": _uv_ok()})
    ex.last_spec = None
    r = ex.execute("inspect", {})
    assert r["success"]
    assert "dimensions" not in r["gates"]
    assert r["gates"]["polycount"]["budget"] == ex.default_tri_budget


def test_inspect_invalid_spec_errors_loudly_not_silent_fallback(tmp_path):
    ex = _executor(tmp_path, {"measure": _measure_ok(),
                              "topology_report": _topology_ok(),
                              "uv_report": _uv_ok()})
    r = ex.execute("inspect", {"spec": {"parts": "not-a-list"}})
    assert r["success"] is False
    assert "Invalid ObjectSpec schema" in r["error"]


def test_inspect_requires_a_model(tmp_path):
    ex = AgentToolExecutor(runner=StubRunner(), workdir=tmp_path)
    r = ex.execute("inspect", {})
    assert r["success"] is False and "No model" in r["error"]
    ex.last_built_glb = str(tmp_path / "missing.glb")
    r = ex.execute("inspect", {})
    assert r["success"] is False and "not found" in r["error"]


# ── review ────────────────────────────────────────────────────────────────────


def test_review_renders_and_takes_cached_verdict(tmp_path):
    front = _make_png(tmp_path / "front.png")
    iso = _make_png(tmp_path / "iso.png")
    ref = _make_png(tmp_path / "ref.png")
    stub = StubRunner({"render_views": {"success": True,
                                         "views": {"front": str(front),
                                                   "iso": str(iso)},
                                         "closeup_skips": []}})
    ex = _review_executor(tmp_path, stub)
    ex.last_spec = ObjectSpec.model_validate(BOX_SPEC)
    vlm = CountingVLM()
    ex._vlm = vlm
    ex._vlm_checked = True

    r = ex.execute("review", {"reference_images": [str(ref)]})
    assert r["success"], r.get("error")
    assert r["renders"] == {"front": str(front), "iso": str(iso)}
    assert r["vision_verdict"]["parsed"] is True
    assert r["vision_verdict"]["matches_reference"] is False
    assert vlm.calls == 1
    # the summary carries the spec identity (measured facts to the VLM too)
    (op, params), = [c for c in stub.calls if c[0] == "render_views"]
    assert params["views"] == ["front", "side", "top", "iso"]
    assert params["prefix"] == "review"

    # identical images → cache hit (VISION_CONFIG §6), no second VLM call
    r2 = ex.execute("review", {"reference_images": [str(ref)]})
    assert r2["vision_verdict"]["cached"] is True
    assert r2["vision_verdict"]["matches_reference"] is False
    assert vlm.calls == 1


def test_review_escalates_once_on_disagreement(tmp_path):
    front = _make_png(tmp_path / "front.png")
    ref = _make_png(tmp_path / "ref.png")
    stub = StubRunner({"render_views": {"success": True,
                                         "views": {"front": str(front)},
                                         "closeup_skips": []}})
    ex = _review_executor(tmp_path, stub)
    vlm = CountingVLM(escalation_model="gemini-3.6-flash")
    ex._vlm = vlm
    ex._vlm_checked = True

    r = ex.execute("review", {"reference_images": [str(ref)]})
    v = r["vision_verdict"]
    # exactly ONE escalation, both verdicts recorded (§3)
    assert vlm.calls == 2
    assert v["matches_reference"] is True
    assert v["escalated_from"]["matches_reference"] is False
    assert v["escalated_from"]["score"] == 4
    # the escalated verdict is what gets cached
    r2 = ex.execute("review", {"reference_images": [str(ref)]})
    assert r2["vision_verdict"]["cached"] is True
    assert vlm.calls == 2


def test_review_threads_closeups_from_last_spec(tmp_path):
    front = _make_png(tmp_path / "front.png")
    stub = StubRunner({"render_views": {"success": True,
                                         "views": {"front": str(front)},
                                         "closeup_skips": []}})
    ex = _review_executor(tmp_path, stub)
    spec = dict(BOX_SPEC)
    spec["review_closeups"] = [{"name": "seam", "part": "body",
                                 "direction": "front", "pad": 0.4,
                                 "frame": "part"}]
    ex.last_spec = ObjectSpec.model_validate(spec)
    ex.execute("review", {})
    (op, params), = [c for c in stub.calls if c[0] == "render_views"]
    assert params["closeups"] == [{"name": "seam", "part": "body",
                                    "direction": "front", "pad": 0.4,
                                    "frame": "part"}]


def test_review_without_refs_or_vlm(tmp_path):
    front = _make_png(tmp_path / "front.png")
    stub = StubRunner({"render_views": {"success": True,
                                         "views": {"front": str(front)},
                                         "closeup_skips": []}})
    ex = _review_executor(tmp_path, stub)
    # no reference images → renders only, no verdict attempted
    r = ex.execute("review", {})
    assert r["success"] and r["vision_verdict"] is None
    # refs but no provider → honest unavailable, not a crash
    ex._vlm = None
    ex._vlm_checked = True
    r2 = ex.execute("review", {"reference_images": [str(front)]})
    assert r2["vision_verdict"] == {"available": False,
                                     "reason": "no vision provider configured or reachable"}


# ── advisory_visual_verdict (shared with the agent loop) ─────────────────────


def test_advisory_verdict_no_escalation_without_model():
    class NoEscalation:
        def visual_verdict(self, renders, refs, model_summary="", escalate=False):
            return {"parsed": True, "matches_reference": False}

    v = advisory_visual_verdict(NoEscalation(), {"front": "x"}, ["y"])
    assert v["matches_reference"] is False
    assert "escalated_from" not in v


def test_advisory_verdict_no_escalation_when_unparsed():
    class Unparsed:
        escalation_model = "gemini-3.6-flash"

        def visual_verdict(self, renders, refs, model_summary="", escalate=False):
            return {"parsed": False}

    v = advisory_visual_verdict(Unparsed(), {}, [])
    assert v == {"parsed": False}


def test_loop_visual_gate_uses_the_shared_helper(tmp_path):
    """The agent loop's visual gate must go through the SAME escalation
    policy as the review tool (behavior-preserving refactor, pinned)."""
    from src.agent.loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    loop._vlm = CountingVLM(escalation_model="gemini-3.6-flash")
    loop._vlm_checked = True
    img = _make_png(tmp_path / "ref.png")
    render = _make_png(tmp_path / "front.png")
    # no rendered views → no verdict
    assert loop._run_visual_gate({}, [img], None, None) is None
    # disagreeing verdict → exactly ONE escalation, both recorded
    vlm = CountingVLM(escalation_model="gemini-3.6-flash")
    loop._vlm = vlm
    verdict = loop._run_visual_gate({"front": str(render)}, [img], None, None)
    assert verdict["escalated_from"]["matches_reference"] is False
    assert verdict["matches_reference"] is True
    assert vlm.calls == 2


# ── finish ────────────────────────────────────────────────────────────────────


def test_finish_threads_args_and_returns_measured_facts(tmp_path, monkeypatch):
    import src.client.package as pkg_mod

    captured = {}

    def fake_finish(job, spec, **kw):
        captured.update(kw)
        captured["job"] = job
        captured["spec"] = spec
        return {
            "package_dir": str(tmp_path / "packages" / "AGT0001"),
            "all_passed": True,
            "gates": [{"gate": "dimensions", "passed": True}],
            "finish": {
                "lp_tri_equivalent": 3468, "hp_tri_equivalent": 201600,
                "lp_budget": 5000, "lp_decimated": False,
                "texture_resolution": 2048,
                "bake_device_resolved": {"compute_device_type": "OPTIX"},
                "step_timings_sec": {"bake_maps": 42.0, "total_chain": 60.0},
                "uv_atlas": {"pack_scale": 0.75},
                "uv_diagnostics": {"islands_total": 120,
                                    "texel_density_per_object": {
                                        "body": {"texels_per_m": 300.0}}},
                "bake": {"basecolor": {"stats": {"std": 0.3}}},
                "review_renders": [str(tmp_path / "front.png")],
            },
        }

    monkeypatch.setattr(pkg_mod, "finish_delivery", fake_finish)
    ex = _executor(tmp_path)
    r = ex.execute("finish", {
        "job": str(_write_job(tmp_path)),
        "resolution": 2048, "bake_timeout_sec": 3600.0,
        "bake_device": "optix", "review_renders": False,
        "out_root": str(tmp_path / "packages"),
    })
    assert r["success"], r.get("error")
    # measured facts extracted, not the whole report blob
    assert r["package_dir"].endswith("AGT0001")
    assert r["all_gates_passed"] is True
    assert r["lp_tri_equivalent"] == 3468
    assert r["hp_tri_equivalent"] == 201600
    assert r["texture_resolution"] == 2048
    assert r["bake_device_resolved"]["compute_device_type"] == "OPTIX"
    assert r["step_timings_sec"]["bake_maps"] == 42.0
    assert r["texel_density_per_object"]["body"]["texels_per_m"] == 300.0
    # arguments threaded through to finish_delivery
    assert captured["resolution"] == 2048
    assert captured["bake_timeout_sec"] == 3600.0
    assert captured["bake_device"] == "optix"
    assert captured["review_renders"] is False
    assert isinstance(captured["spec"], ObjectSpec)
    assert captured["job"].job_code == "AGT0001"
    # the executor's own runner is the one used (no hidden second runner)
    assert captured["runner"] is ex.runner


def test_finish_refusal_is_a_result_not_a_crash(tmp_path):
    """Rule 9 through the tool boundary: the REAL finish_delivery runs the
    chain on a stub runner, refuses at emission, and the brain receives
    refused=true + the reason — never an exception."""
    class _ChainRecorder:
        def __init__(self):
            self.ops = []

        def execute_op(self, op, params=None, timeout_sec=None):
            self.ops.append(op)
            if op == "prepare_delivery_scene":
                return {"success": True, "uv_atlas": {"pack_scale": 0.75},
                        "uv": {"islands_total": 120}}
            if op == "bake_maps":
                return {"success": True, "device": {"compute_device_type": "OPTIX"},
                        "maps": {"basecolor": {"stats": {"std": 0.35}}},
                        "hp_triangle_equivalent": 201600}
            if op == "decimate_to_budget":
                return {"success": True, "triangle_equivalent": 3468,
                        "decimated": False}
            if op == "render_views":
                out = Path(params["output_dir"])
                out.mkdir(parents=True, exist_ok=True)
                (out / f"{params.get('prefix', 'job')}_front.png").write_bytes(
                    b"\x89PNG\r\n\x1a\n")
                return {"success": True}
            raise AssertionError(f"unexpected op {op!r} in the refusal path")

    rec = _ChainRecorder()
    ex = AgentToolExecutor(runner=rec, workdir=tmp_path / "work")
    ex.last_spec = ObjectSpec.model_validate(BOX_SPEC)
    r = ex.execute("finish", {"job": str(_write_job(tmp_path, placeholder=True)),
                              "out_root": str(tmp_path / "packages")})
    assert r["success"] is False
    assert r["refused"] is True
    assert r["reason"] == "dims_placeholder"
    assert "REFUSED" in r["error"]
    # the chain ran for structural review, but no deliverable export
    assert rec.ops == ["prepare_delivery_scene", "bake_maps",
                       "decimate_to_budget", "render_views"]
    # refusal evidence on disk (sibling blocked dir), no package emitted
    blocked = json.loads((tmp_path / "blocked" / "AGT0001" / "qa_report.json")
                         .read_text(encoding="utf-8"))
    assert blocked["refused"] is True
    assert not (tmp_path / "packages" / "AGT0001").exists()


def test_finish_requires_job_and_spec(tmp_path):
    ex = _executor(tmp_path)
    r = ex.execute("finish", {})
    assert r["success"] is False and "job" in r["error"]
    ex.last_spec = None
    r = ex.execute("finish", {"job": str(_write_job(tmp_path))})
    assert r["success"] is False and "No spec" in r["error"]
    r = ex.execute("finish", {"job": str(tmp_path / "nope.yaml")})
    assert r["success"] is False and "failed to load" in r["error"]


# ── package ───────────────────────────────────────────────────────────────────


def test_package_refusal_needs_no_source(tmp_path):
    """The rule-9 refusal fires before the source is touched: the tool must
    not demand a GLB the refusal would never read (S4: keep refusing)."""
    ex = AgentToolExecutor(runner=StubRunner(), workdir=tmp_path / "work")
    assert ex.last_built_glb is None
    r = ex.execute("package", {"job": str(_write_job(tmp_path, placeholder=True,
                                                    code="MAYA2"))})
    assert r["success"] is False
    assert r["refused"] is True
    assert r["reason"] == "dims_placeholder"
    assert "REFUSED" in r["error"]
    # real-dims job without any source: clean error, not a crash
    r2 = ex.execute("package", {"job": str(_write_job(tmp_path, code="REAL1"))})
    assert r2["success"] is False
    assert "No source GLB" in r2["error"]


def test_package_returns_manifest_and_gates(tmp_path, monkeypatch):
    import src.client.package as pkg_mod

    def fake_package(job, source, **kw):
        assert Path(source).is_file(), "last_built_glb must be threaded"
        return {
            "package_dir": str(tmp_path / "packages" / job.job_code),
            "all_passed": True,
            "gates": [{"gate": "naming", "passed": True}],
            "files": [{"name": f"{job.job_code}.fbx", "size_bytes": 10,
                        "sha256": "ab12", "placeholder": False}],
            "placeholders": {"textures": "CC0 placeholder set"},
            "axis_convention": {"axis_up": "Y", "axis_forward": "-Z"},
        }

    monkeypatch.setattr(pkg_mod, "package_delivery", fake_package)
    ex = _executor(tmp_path)
    r = ex.execute("package", {"job": str(_write_job(tmp_path)),
                                "out_root": str(tmp_path / "packages")})
    assert r["success"], r.get("error")
    assert r["all_gates_passed"] is True
    assert r["files"][0]["sha256"] == "ab12"
    assert r["placeholders"]["textures"] == "CC0 placeholder set"
    assert r["axis_convention"]["axis_up"] == "Y"


def test_package_on_the_real_maya_card_refuses(tmp_path):
    """The permanent placeholder card (MAYA00053153) through the tool: the
    refusal is the correct outcome (S4 — never infer dims)."""
    ex = AgentToolExecutor(runner=StubRunner(), workdir=tmp_path / "work")
    r = ex.execute("package", {"job": str(MAYA_JOB),
                                "out_root": str(tmp_path / "packages")})
    assert r["success"] is False
    assert r["refused"] is True and r["reason"] == "dims_placeholder"


# ── real-Blender round trip (auto-skip without an install) ────────────────────


@pytest.mark.blender
def test_inspect_real_build_round_trip(tmp_path):
    """Build → inspect → UV atlas → inspect again, through REAL Blender ops:
    pins that uv_report and objects_detail run in-process and the executor's
    facts survive a real glTF round trip. The chiral fixture is two closed
    boxes — 12 tris each after glTF triangulation, default cube UVs (6
    islands per box), no manifold defects."""
    from src.blender.locate import locate_blender

    if locate_blender() is None:
        pytest.skip("Blender 3.3+ not found on this machine")
    from src.blender.runner import BlenderRunner

    runner = BlenderRunner()
    ex = AgentToolExecutor(runner=runner, workdir=tmp_path / "work")
    spec = json.loads((PROJECT_ROOT / "input" / "fixtures" /
                       "chiral_test.spec.json").read_text(encoding="utf-8"))
    built = ex.execute("build_spec", {"spec": spec})
    assert built["success"], built.get("error")

    insp = ex.execute("inspect", {})
    assert insp["success"], insp.get("error")
    assert set(insp["parts"]) == {"base", "boss"}
    assert insp["polycount"]["triangle_equivalent"] == 24
    # closed_solid is computed on a WELDED copy: raw boundary edges are 24
    # per box (glTF splits vertices per attribute) yet both parts are closed
    assert all(p["closed_solid"] for p in insp["parts"].values())
    assert insp["parts"]["base"]["boundary_edges"] == 24
    assert insp["gates"]["ngons"]["passed"] is True
    assert insp["gates"]["closed_solids"]["passed"] is True
    assert insp["gates"]["dimensions"]["passed"] is True
    assert insp["all_gates_passed"] is True
    # default cube UVs survive the glTF round trip: 6 islands per box
    assert insp["uv"]["available"] is True
    assert insp["uv"]["islands_total"] == 12
    assert insp["uv"]["in_bounds"] is True

    # after smart_project the diagnostics still read (per-object texels)
    uv_glb = tmp_path / "uv.glb"
    r = runner.execute_op("generate_uvs", {"input": ex.last_built_glb,
                                            "output": str(uv_glb)})
    assert r["success"], r.get("error")
    insp2 = ex.execute("inspect", {"model_path": str(uv_glb)})
    assert insp2["success"], insp2.get("error")
    assert insp2["uv"]["available"] is True
    assert insp2["uv"]["islands_total"] > 0
    assert set(insp2["uv"]["texel_density_per_object"]) == {"base", "boss"}
    assert insp2["all_gates_passed"] is True
