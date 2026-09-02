"""Phase 5 closed-loop tests — the build → gates → vision contract, pinned
without Blender, GPU, network, or live keys (S1: no live vision calls).

Three groups:

1. **Gates before eyes** (master order Phase 5): a run whose measured gates
   stay red makes ZERO vision calls; a green run takes exactly one advisory
   verdict AFTER the gates pass.
2. **Honest iteration-cap report** ("Hard iteration cap, start at 8. On cap:
   stop, report exactly what failed with the evidence. Never claim a success
   you cannot evidence."): a cap-exhausted run gets manifest status
   ``iteration_cap_exhausted``, a non-None unresolved_error, and a
   metrics.cap_report carrying the failed dimension/mesh gate evidence —
   including the case where the corrector "fixed" the spec on the final
   iteration and the fix was never rebuilt (previously exited with
   unresolved_error: null).
3. **Owner texture library in the analyst prompt**: the indexed surfaces +
   selection contract (never diffusion) ride into the analyst's user text;
   no library → no section; the config cap is pinned at 8.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from PIL import Image

from src.agent.loop import AgentLoop
from src.agent.prompts import ANALYST_SYSTEM_PROMPT
from src.agent.verifier import MeshGateResult, Verifier, VerificationReport
from src.run_store import RunStore
from src.spec.schema import (MeasurementSpec, ObjectSpec, PartSpec,
                             ReviewCloseupSpec, ShapeType)
from src.spec.validation import DimensionGateResult

CONFIG_AI_YAML = Path("config/ai.yaml")


def _spec(with_closeups: bool = False) -> ObjectSpec:
    spec = ObjectSpec(
        name="closed_loop_fixture",
        parts=[
            PartSpec(
                name="body",
                shape=ShapeType.ROUNDED_BOX,
                dimensions=[0.20, 0.20, 0.30],
                position=[0.0, 0.0, 0.15],
            )
        ],
        measurements=[
            MeasurementSpec(
                name="overall_height", target_value=0.30,
                applies_to="overall.height_z", tolerance_m=0.001,
            ),
            MeasurementSpec(
                name="body_width", target_value=0.20,
                applies_to="body.width_x", tolerance_m=0.001,
            ),
        ],
    )
    if with_closeups:
        spec.review_closeups = [
            ReviewCloseupSpec(name="label", part="body", direction="front")
        ]
    return spec


@dataclass
class _FakeResult:
    content: str
    finish_reason: str = "stop"
    max_tokens: int = 4096
    completion_tokens: int = 100
    raw_response: dict = field(default_factory=dict)


class FakeProvider:
    """AptosGLMProvider stand-in: records every chat/complete_json call and
    always answers with the fixture spec (so the corrector 'succeeds' every
    round — the loop must therefore exhaust its cap on red gates)."""

    def __init__(self, spec: ObjectSpec, agent_cfg: dict | None = None):
        self.config = {"agent": agent_cfg or {}}
        self.spec_dict = json.loads(spec.model_dump_json())
        self.chat_messages: list[tuple[str, list]] = []
        self.corrector_calls = 0

    def supports_vision(self) -> bool:
        return False

    def chat(self, messages, role="general", **kw):
        self.chat_messages.append((role, messages))
        return _FakeResult(json.dumps(self.spec_dict))

    def complete_json(self, system_prompt, user_prompt, role="analyst", **kw):
        if role == "corrector":
            self.corrector_calls += 1
        return json.dumps(self.spec_dict), deepcopy(self.spec_dict)


class FakeRunner:
    """BlenderRunner stand-in: writes the step GLB (the loop checks its
    existence) and returns render views as real files."""

    def __init__(self, fail_build: bool = False):
        self.fail_build = fail_build
        self.ops: list[tuple[str, dict]] = []

    def execute_op(self, op: str, params: dict):
        self.ops.append((op, deepcopy(params)))
        if op == "build_from_spec":
            if self.fail_build:
                raise RuntimeError("boom: harness rejected the spec")
            out = Path(params["output_path"])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"fake glb")
            return {"success": True, "output_path": str(out)}
        if op == "measure":
            return {"success": True, "overall": {"dimensions": [0.2, 0.2, 0.24]},
                    "parts": {}}
        if op == "render_views":
            out_dir = Path(params["output_dir"])
            out_dir.mkdir(parents=True, exist_ok=True)
            views = {}
            for v in params.get("views", ["front", "side", "top", "iso"]):
                p = out_dir / f"{params['prefix']}_{v}.png"
                p.write_bytes(b"fake png")
                views[v] = str(p)
            for cu in params.get("closeups") or []:
                p = out_dir / f"{params['prefix']}_{cu['name']}.png"
                p.write_bytes(b"fake png")
                views[cu["name"]] = str(p)
            return {"success": True, "views": views, "closeup_skips": []}
        return {}


class FakeVerifier(Verifier):
    """Deterministic gate outcome per run (no Blender, no trimesh)."""

    def __init__(self, fail: bool = True):
        super().__init__()
        self.fail = fail

    def verify_run(self, spec, measurement_data, glb_path):
        # partial failure when red: overall_height fails, body_width passes
        dim = DimensionGateResult(
            passed=not self.fail,
            measurements_checked=2,
            passed_count=1 if self.fail else 2,
            failed_count=1 if self.fail else 0,
            details=[
                {"name": "overall_height", "target_m": 0.30, "actual_m": 0.24,
                 "delta_m": -0.06, "passed": False},
                {"name": "body_width", "target_m": 0.20, "actual_m": 0.20,
                 "delta_m": 0.0, "passed": True},
            ],
            max_delta_m=0.06 if self.fail else 0.0,
        )
        mesh = MeshGateResult(
            passed=True, is_watertight=True, faces_count=1234,
            vertices_count=620, bounding_box_m=[0.2, 0.2, 0.24],
            volume_m3=0.0011, warnings=[], errors=[],
        )
        return VerificationReport(
            passed=not self.fail,
            dimension_gate=dim,
            mesh_gate=mesh,
            feedback_for_agent=(
                "overall_height: target 0.300 m, actual 0.240 m "
                "(delta -60.0 mm) — exceeds tolerance"
            ),
        )


class CountingVLM:
    """Vision stand-in that counts calls — the gates-before-eyes pin."""

    escalation_model = "gemini-3.6-flash"

    def __init__(self):
        self.calls = 0
        self.describe_calls = 0

    def is_available(self):
        return True

    def describe_reference_images(self, image_paths):
        self.describe_calls += 1
        return "1. a simple box."

    def visual_verdict(self, renders, refs, model_summary="", escalate=False):
        self.calls += 1
        return {"available": True, "parsed": True, "matches_reference": True,
                "score": 9, "issues": [], "summary": "fine",
                "model": "gemini-3.5-flash-lite", "escalated": False}


def _ref_image(tmp_path: Path) -> Path:
    p = tmp_path / "ref.png"
    Image.new("RGB", (32, 32), (90, 90, 130)).save(p)
    return p


def _make_loop(tmp_path: Path, *, fail: bool, max_iterations: int = 3,
               spec: ObjectSpec | None = None, fail_build: bool = False,
               agent_cfg: dict | None = None):
    spec = spec or _spec()
    cfg = agent_cfg if agent_cfg is not None else {
        "max_iterations": max_iterations, "wall_clock_budget_s": 900}
    loop = AgentLoop(
        provider=FakeProvider(spec, agent_cfg=cfg),
        runner=FakeRunner(fail_build=fail_build),
        verifier=FakeVerifier(fail=fail),
        run_store=RunStore(root_dir=tmp_path / "runs"),
    )
    vlm = CountingVLM()
    loop._vlm = vlm
    loop._vlm_checked = True
    return loop, vlm


def _manifest(run_dir: Path) -> dict:
    return json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))


# ── Group 1: gates before eyes ───────────────────────────────────────────────


def test_red_gates_never_call_vision_and_report_the_cap(tmp_path):
    """The Phase 5 contract: red gates → skip vision entirely, fix, repeat;
    on cap → stop with an explicit report. Never a silent near-success."""
    loop, vlm = _make_loop(tmp_path, fail=True, max_iterations=3)
    events: list[dict] = []
    res = loop.run(prompt="a box", images=[_ref_image(tmp_path)],
                   run_dir=tmp_path / "run", progress=events.append)

    # gates stayed red for all 3 iterations → ZERO vision calls
    # (the analyst-eye describe IS allowed before the loop; verdicts are not)
    assert vlm.calls == 0
    assert vlm.describe_calls == 1
    assert res.success is False
    assert res.iterations == 3
    # the honest cap error: non-None, names the cap and the failed gate
    assert res.error is not None
    assert res.error.startswith("Iteration cap (3) reached without passing gates")
    assert "dimension gate FAILED (1/2 measurements passed, max delta 60.00 mm)" in res.error
    assert "no success is claimed" in res.error

    m = _manifest(tmp_path / "run")
    assert m["status"] == "iteration_cap_exhausted"
    assert m["metrics"]["iteration_cap_hit"] is True
    assert m["metrics"]["unresolved_error"] is not None  # the honesty pin
    cap = m["metrics"]["cap_report"]
    assert cap["max_iterations"] == 3 and cap["iterations_run"] == 3
    failed = cap["dimension_gate"]["failed_details"]
    assert [d["name"] for d in failed] == ["overall_height"]
    assert failed[0]["delta_m"] == -0.06
    assert cap["mesh_gate"]["passed"] is True
    assert any(e["event"] == "iteration_cap_hit" for e in events)

    # the corrector ran every red iteration (fix → rebuild → still red)
    assert loop.provider.corrector_calls == 3


def test_green_gates_take_exactly_one_vision_call_after_them(tmp_path):
    loop, vlm = _make_loop(tmp_path, fail=False, max_iterations=3)
    res = loop.run(prompt="a box", images=[_ref_image(tmp_path)],
                   run_dir=tmp_path / "run")

    assert res.success is True
    assert vlm.calls == 1  # advisory, AFTER verification.passed
    m = _manifest(tmp_path / "run")
    assert m["status"] == "completed"
    assert m["metrics"]["iteration_cap_hit"] is False
    assert m["metrics"]["cap_report"] is None
    assert m["metrics"]["visual_verdict"]["score"] == 9


def test_build_failure_cap_report_has_no_verification(tmp_path):
    """When no iteration ever produced a verified build, the cap report
    carries the last error instead of gate evidence."""
    loop, vlm = _make_loop(tmp_path, fail=True, max_iterations=2,
                           fail_build=True)
    res = loop.run(prompt="a box", run_dir=tmp_path / "run")

    assert res.success is False
    assert vlm.calls == 0
    assert res.error is not None and "boom" in res.error
    m = _manifest(tmp_path / "run")
    assert m["status"] == "iteration_cap_exhausted"
    cap = m["metrics"]["cap_report"]
    assert cap["last_error"] is not None and "boom" in cap["last_error"]
    assert "without a verified build" in cap["message"]
    assert "Iteration cap (2)" in cap["message"]


def test_budget_exhaustion_is_not_an_iteration_cap(tmp_path):
    """The wall-clock branch keeps its own status; cap_report stays None."""
    loop, vlm = _make_loop(
        tmp_path, fail=True,
        agent_cfg={"max_iterations": 50, "wall_clock_budget_s": 0},
    )
    res = loop.run(prompt="a box", run_dir=tmp_path / "run")
    assert res.success is False
    assert "Wall-clock budget" in (res.error or "")
    m = _manifest(tmp_path / "run")
    assert m["status"] == "budget_exhausted"
    assert m["metrics"]["iteration_cap_hit"] is False


def test_spec_review_closeups_are_rendered_for_the_visual_gate(tmp_path):
    """Phase 5: the spec's review_closeups ride into render_views so the
    visual gate sees label/border detail at full resolution."""
    spec = _spec(with_closeups=True)
    loop, vlm = _make_loop(tmp_path, fail=False, spec=spec)
    res = loop.run(prompt="a box", run_dir=tmp_path / "run")
    assert res.success is True
    render_ops = [p for op, p in loop.runner.ops if op == "render_views"]
    assert render_ops and render_ops[0]["closeups"] == [
        {"name": "label", "part": "body", "direction": "front",
         "pad": 0.3, "frame": "part"}
    ]
    assert "label" in res.renders  # the close-up joined the view set


# ── Group 3: owner texture library in the analyst prompt ─────────────────────


def _write_surface(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    Image.new("RGB", (16, 16), (120, 90, 60)).save(d / "albedo.png")
    Image.new("L", (16, 16), 128).save(d / "roughness.png")
    Image.new("L", (16, 16), 64).save(d / "height.png")
    return d


def test_owner_texture_library_rides_into_the_analyst_prompt(tmp_path):
    root = tmp_path / "texroot"
    _write_surface(root, "oak_plank")
    _write_surface(root, "linen_weave")

    spec = _spec()
    loop, _vlm = _make_loop(tmp_path, fail=False, spec=spec)
    loop.owner_texture_root = root
    loop.run(prompt="a box", run_dir=tmp_path / "run")

    role, messages = loop.provider.chat_messages[0]
    assert role == "analyst"
    user_text = messages[-1].content
    assert "OWNER TEXTURE LIBRARY" in user_text
    assert f'texture_dir="{(root / "oak_plank").resolve()}"' in user_text
    assert f'texture_dir="{(root / "linen_weave").resolve()}"' in user_text
    assert "albedo, height, roughness" in user_text
    assert "diffusion" in user_text  # the never-diffusion rule is stated
    assert "texture_dir" in ANALYST_SYSTEM_PROMPT  # and the schema is documented

    # the deterministic index was (re)written for the web UI / reuse
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    assert index["schema"] == "threed-owner-textures/1"
    assert index["surface_count"] == 2


def test_no_owner_library_no_section(tmp_path):
    loop, _vlm = _make_loop(tmp_path, fail=False)
    loop.owner_texture_root = None  # deterministic: no auto-detect in the test
    loop.run(prompt="a box", run_dir=tmp_path / "run")

    user_text = loop.provider.chat_messages[0][1][-1].content
    assert "OWNER TEXTURE LIBRARY" not in user_text


def test_empty_owner_library_is_skipped(tmp_path):
    root = tmp_path / "texroot"
    root.mkdir()
    loop, _vlm = _make_loop(tmp_path, fail=False)
    loop.owner_texture_root = root
    loop.run(prompt="a box", run_dir=tmp_path / "run")
    user_text = loop.provider.chat_messages[0][1][-1].content
    assert "OWNER TEXTURE LIBRARY" not in user_text


def test_owner_texture_root_explicit_wins_and_missing_dir_is_skipped(tmp_path):
    # constructor path: explicit root is kept verbatim even when absent
    built = AgentLoop(
        provider=FakeProvider(_spec()),
        runner=FakeRunner(),
        verifier=FakeVerifier(fail=False),
        run_store=RunStore(root_dir=tmp_path / "runs"),
        owner_texture_root=tmp_path / "nope",
    )
    assert built.owner_texture_root == tmp_path / "nope"
    assert built._owner_texture_section() is None  # missing dir → no section


# ── Config pin ────────────────────────────────────────────────────────────────


def test_config_pins_the_phase_5_iteration_cap():
    """Master order Phase 5: 'Hard iteration cap, start at 8.'"""
    cfg = yaml.safe_load(CONFIG_AI_YAML.read_text(encoding="utf-8"))
    assert cfg["agent"]["max_iterations"] == 8
