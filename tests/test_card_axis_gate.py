"""Phase 7 card-axis gate tests — the job card's axis convention and
delivery tolerance enforced INSIDE the build loop.

Cold-path defect these pin (found in the Phase 7 shakedown): the analyst's
spec declares its own measurement→axis binding, so the internal dimension
gate verifies whatever binding the analyst DECLARED. STEPSTOOL0001 bound
length→Y while the client card binds L→X — every internal gate passed and
the Dimensions gate failed at package time with a 90° Z rotation. The fix
threads the job card into the loop (CLI ``build --job`` → pipeline →
AgentLoop) and adds ``evaluate_card_axis_gate`` to verification, with the
CARD's delivery tolerance (default 0.01 in the card's declared unit) so a
build that is internally-green at ±1 mm is still driven to client-green —
CHAMBERSTICK0001 failed delivery by +0.100 mm exactly this way.

No Blender, no network, no live keys (S1: no live vision calls).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import trimesh

from src.agent.loop import AgentLoop
from src.agent.verifier import Verifier, evaluate_card_axis_gate
from src.run_store import RunStore
from src.spec.schema import MeasurementSpec, ObjectSpec, PartSpec, ShapeType


def make_job(**overrides) -> "JobCard":
    from src.client.job import JobCard

    data = {
        "job_code": "CARDAXIS0001",
        "dims": {"length": 450.0, "width": 420.0, "height": 480.0, "unit": "mm"},
        "complexity": "medium",
        "orientation": "floor",
        "product_class": "step_stool",
        "reference_dir": "input/references/PHASE7-D-STEPSTOOL",
    }
    data.update(overrides)
    return JobCard.model_validate(data)


def measure_data(x: float, y: float, z: float) -> dict:
    return {"overall": {"dimensions": [x, y, z]}, "parts": {}}


def _spec() -> ObjectSpec:
    return ObjectSpec(
        name="card_axis_fixture",
        parts=[PartSpec(name="body", shape=ShapeType.BOX,
                        dimensions=[0.45, 0.42, 0.48],
                        position=[0.0, 0.0, 0.24])],
        measurements=[
            MeasurementSpec(name="overall_length", target_value=0.45,
                            applies_to="overall.width_x", tolerance_m=0.001),
            MeasurementSpec(name="overall_width", target_value=0.42,
                            applies_to="overall.depth_y", tolerance_m=0.001),
            MeasurementSpec(name="overall_height", target_value=0.48,
                            applies_to="overall.height_z", tolerance_m=0.001),
        ],
    )


@pytest.fixture(scope="module")
def glb_path(tmp_path_factory) -> Path:
    """A real watertight GLB so verify_run's mesh gate runs for real."""
    p = tmp_path_factory.mktemp("card_axis") / "fixture.glb"
    trimesh.creation.box(extents=[0.45, 0.42, 0.48]).export(p)
    return p


# ── evaluate_card_axis_gate (pure) ──────────────────────────────────────────


def test_axis_swap_caught():
    """The shakedown failure: extents swapped between X and Y (90° rotation)."""
    passed, details, feedback = evaluate_card_axis_gate(
        make_job(), measure_data(0.42, 0.45, 0.48))
    assert not passed
    failed_names = [d["name"] for d in details if not d["passed"]]
    assert failed_names == ["job card length (axis X)", "job card width (axis Y)"]
    assert len(feedback) == 2
    assert "X extent is 0.42000 m but the card's length is 0.45000 m" in feedback[0]
    assert "card axis map L→X, W→Y, H→Z" in feedback[0]
    assert "rotate or" in feedback[0]


def test_matching_extents_pass_with_card_tolerance():
    """Passing extents land in the details with the CARD tolerance (mm card
    → 0.01 mm = 1e-5 m), not the internal 1 mm."""
    passed, details, feedback = evaluate_card_axis_gate(
        make_job(), measure_data(0.45, 0.42, 0.48))
    assert passed and feedback == []
    assert all(d["passed"] for d in details)
    assert all(d["tolerance_m"] == 1e-5 for d in details)


def test_near_miss_inside_internal_tolerance_fails():
    """The CHAMBERSTICK0001 regression: +0.1 mm is inside the internal ±1 mm
    (so the loop used to stop) but outside the card's ±0.01 mm delivery
    tolerance — the gate must fail and say by how much."""
    card = make_job(
        job_code="CHAMBERSTICK0001",
        dims={"length": 190.0, "width": 140.0, "height": 65.0, "unit": "mm"},
    )
    passed, details, feedback = evaluate_card_axis_gate(
        card, measure_data(0.1901, 0.14, 0.065))
    assert not passed
    x = next(d for d in details if "length" in d["name"])
    assert x["delta_mm"] == pytest.approx(0.1, abs=1e-6)
    assert not x["passed"]
    assert "delta 0.100 mm" in feedback[0]
    assert "±0.010 mm" in feedback[0]


def test_inch_card_uses_inch_tolerance():
    """Default tolerance is 0.01 in the card's declared unit: for an inch
    card that is 0.254 mm — a 0.1 mm miss passes there (and must, that is
    the client contract for inch work)."""
    card = make_job(
        dims={"length": 18.0, "width": 12.0, "height": 5.0, "unit": "in"},
    )
    passed, details, _ = evaluate_card_axis_gate(
        card, measure_data(0.4572 + 0.0001, 0.3048, 0.127))
    assert passed
    assert details[0]["tolerance_m"] == pytest.approx(0.000254)


def test_non_default_axis_map_honored():
    """A card that maps length→Y is judged through ITS map, not the default."""
    card = make_job(axis_map={"length": "y", "width": "x", "height": "z"})
    passed, _, _ = evaluate_card_axis_gate(
        card, measure_data(0.42, 0.45, 0.48))
    assert passed


# ── verify_run integration (real Verifier, real GLB) ────────────────────────


def test_verify_run_fails_on_swap_and_carries_evidence(glb_path):
    report = Verifier().verify_run(
        spec=_spec(), measurement_data=measure_data(0.42, 0.45, 0.48),
        glb_path=glb_path, job_card=make_job())
    assert not report.passed
    assert not report.dimension_gate.passed
    assert "JOB CARD AXIS GATE FAILED" in report.feedback_for_agent
    # Card details ride in the dimension gate's details → run manifest
    # metrics.dimension_details carries the evidence.
    card_names = [d["name"] for d in report.dimension_gate.details
                  if d["name"].startswith("job card ")]
    assert len(card_names) == 3


def test_verify_run_passes_when_card_matches(glb_path):
    report = Verifier().verify_run(
        spec=_spec(), measurement_data=measure_data(0.45, 0.42, 0.48),
        glb_path=glb_path, job_card=make_job())
    assert report.passed
    assert "JOB CARD AXIS GATE PASSED" in report.feedback_for_agent


def test_verify_run_without_card_unchanged(glb_path):
    """No card → no card gate, no card details: existing behavior is
    byte-for-byte backward compatible."""
    report = Verifier().verify_run(
        spec=_spec(), measurement_data=measure_data(0.45, 0.42, 0.48),
        glb_path=glb_path)
    assert report.passed
    assert "JOB CARD" not in report.feedback_for_agent
    assert not [d for d in report.dimension_gate.details
                if d["name"].startswith("job card ")]


# ── Loop wiring: analyst prompt section + verifier receives the card ────────


@dataclass
class _FakeResult:
    content: str
    finish_reason: str = "stop"
    max_tokens: int = 4096
    completion_tokens: int = 100
    raw_response: dict = field(default_factory=dict)


class RecordingProvider:
    """Returns the fixture spec; records the analyst's user message."""

    def __init__(self, spec: ObjectSpec):
        self.config = {"agent": {}}
        self.spec_dict = json.loads(spec.model_dump_json())
        self.analyst_user_text = ""

    def supports_vision(self) -> bool:
        return False

    def chat(self, messages, role="general", **kw):
        if role == "analyst":
            self.analyst_user_text = messages[-1].content
        return _FakeResult(json.dumps(self.spec_dict))

    def complete_json(self, system_prompt, user_prompt, role="analyst", **kw):
        return json.dumps(self.spec_dict), dict(self.spec_dict)


class RecordingVerifier(Verifier):
    """Passes gates; records the job_card it was handed."""

    def __init__(self):
        super().__init__()
        self.seen_job_cards = []

    def verify_run(self, spec, measurement_data, glb_path, job_card=None):
        from src.agent.verifier import MeshGateResult, VerificationReport
        from src.spec.validation import DimensionGateResult

        self.seen_job_cards.append(job_card)
        dim = DimensionGateResult(passed=True, measurements_checked=1,
                                  passed_count=1, failed_count=0, details=[],
                                  max_delta_m=0.0)
        mesh = MeshGateResult(passed=True, is_watertight=True, faces_count=12,
                              vertices_count=8, bounding_box_m=[1, 1, 1],
                              volume_m3=1.0, warnings=[], errors=[])
        return VerificationReport(passed=True, dimension_gate=dim,
                                  mesh_gate=mesh, feedback_for_agent="")


def _fake_runner():
    class FakeRunner:
        def execute_op(self, op: str, params: dict):
            if op == "build_from_spec":
                out = Path(params["output_path"])
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b"fake glb")
                return {"success": True, "output_path": str(out)}
            if op == "measure":
                return {"success": True,
                        "overall": {"dimensions": [0.45, 0.42, 0.48]},
                        "parts": {}}
            if op == "render_views":
                out_dir = Path(params["output_dir"])
                out_dir.mkdir(parents=True, exist_ok=True)
                views = {}
                for v in params.get("views", ["front", "side", "top", "iso"]):
                    p = out_dir / f"{params['prefix']}_{v}.png"
                    p.write_bytes(b"fake png")
                    views[v] = str(p)
                return {"success": True, "views": views, "closeup_skips": []}
            return {}
    return FakeRunner()


def _loop(tmp_path: Path, spec: ObjectSpec):
    verifier = RecordingVerifier()
    loop = AgentLoop(
        provider=RecordingProvider(spec),
        runner=_fake_runner(),
        verifier=verifier,
        run_store=RunStore(root_dir=tmp_path / "runs"),
    )
    return loop, verifier, loop.provider


def test_analyst_prompt_states_card_contract(tmp_path):
    """The analyst's user text carries the card's axis map, meter-converted
    dims, and the applies_to bindings — the swap becomes visible BEFORE the
    first build."""
    loop, _, provider = _loop(tmp_path, _spec())
    loop.run(prompt="step stool", run_name="card_axis_prompt",
             job_card=make_job())
    text = provider.analyst_user_text
    assert "CLIENT JOB CARD CONTRACT (delivery-critical)" in text
    assert "CARDAXIS0001" in text
    assert "LENGTH = 0.4500 m is the overall extent along the X axis" in text
    assert "WIDTH = 0.4200 m is the overall extent along the Y axis" in text
    assert "HEIGHT = 0.4800 m is the overall extent along the Z axis" in text
    assert "applies_to 'overall.width_x'" in text
    assert "rotated" in text  # the fails-delivery warning


def test_no_card_no_contract_section(tmp_path):
    loop, _, provider = _loop(tmp_path, _spec())
    loop.run(prompt="step stool", run_name="card_axis_absent")
    assert "CLIENT JOB CARD CONTRACT" not in provider.analyst_user_text


def test_loop_passes_card_to_verifier(tmp_path):
    """The card given to run() reaches every verify_run call."""
    card = make_job()
    loop, verifier, _ = _loop(tmp_path, _spec())
    result = loop.run(prompt="step stool", run_name="card_axis_thread",
                      job_card=card)
    assert result.success
    assert verifier.seen_job_cards and all(c is card for c in verifier.seen_job_cards)


def test_run_started_event_carries_job_code(tmp_path):
    events = []
    loop, _, _ = _loop(tmp_path, _spec())
    loop.run(prompt="step stool", run_name="card_axis_event",
             progress=lambda e: events.append(e), job_card=make_job())
    started = next(e for e in events if e["event"] == "run_started")
    assert started["job_code"] == "CARDAXIS0001"
