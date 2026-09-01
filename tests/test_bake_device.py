"""Cycles bake device selection (GPU fix, 2026-09-02 session log).

The bug: ``scene.cycles.device`` was hardcoded ``"CPU"`` in both bake ops —
no GPU setup anywhere in the codebase. A 4K mattress bake burned 1348
CPU-seconds at 0% GPU utilisation. The fix threads a ``device`` parameter
through ``finish_delivery`` → the bake op exactly like ``bake_timeout_sec``
and enables the device PROPERLY inside the harness (compute_device_type in
addon preferences → get_devices() → per-device ``use`` flags → only then
scene.cycles.device — setting the scene device alone silently does nothing,
which is how the bug survived review).

Non-blender tests (fake runner) verify the parameter threading — they run
everywhere, including CI and GPU-less hosts. Blender-marked tests verify the
harness's device evidence and the clean CPU fallback.
"""

import json
from pathlib import Path

import pytest

from src.client.job import load_job
from src.client.package import PlaceholderDimensionsError, finish_delivery
from src.spec.template import compile_spec, load_template

PROJECT_ROOT = Path(__file__).resolve().parents[1]
JOB = PROJECT_ROOT / "input" / "jobs" / "MAYA00053153.yaml"
TEMPLATE = PROJECT_ROOT / "templates" / "mattress.yaml"

# What the real harness returns on a GPU host (shape pinned from the live
# probe on the RTX 4080 Super; the fake runner replays it so the refusal
# report can be asserted without Blender).
_GPU_EVIDENCE = {
    "requested": "optix",
    "device": "GPU",
    "compute_device_type": "OPTIX",
    "devices_enabled": ["NVIDIA GeForce RTX 4080 SUPER"],
    "fallback_reason": None,
}


class _RecordingRunner:
    """Stands in for BlenderRunner: records every op's params (and the bake
    timeout), returns plausible results, writes the review renders."""

    def __init__(self, device_evidence=None):
        self.ops = []
        self.params = {}
        self.timeouts = {}
        self.device_evidence = device_evidence or {
            "requested": "auto", "device": "CPU", "compute_device_type": None,
            "devices_enabled": [], "fallback_reason": "no usable GPU (test fake)",
        }

    def execute_op(self, op, params=None, timeout_sec=None):
        self.ops.append(op)
        self.params[op] = dict(params or {})
        if op not in self.timeouts or timeout_sec is not None:
            self.timeouts[op] = timeout_sec
        if op == "prepare_delivery_scene":
            return {"success": True, "uv_atlas": {"pack_scale": 0.75},
                    "uv": {"islands_total": 120}}
        if op == "bake_maps":
            return {"success": True, "device": self.device_evidence,
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


def _run_refusal_chain(rec, out_root, **kwargs):
    job = load_job(JOB)
    spec, _ = compile_spec(load_template(TEMPLATE), job)
    with pytest.raises(PlaceholderDimensionsError, match="REFUSED"):
        finish_delivery(job, spec, out_root=out_root,
                        runner=rec, log=lambda _m: None, resolution=64, **kwargs)


def test_bake_device_flows_to_the_bake_op_only(tmp_path):
    """`bake_device` is a real finish_delivery parameter threading exactly
    like bake_timeout_sec: it must reach ONLY the bake op's params."""
    rec = _RecordingRunner(device_evidence=dict(_GPU_EVIDENCE))
    _run_refusal_chain(rec, tmp_path / "pkgs", bake_device="optix")
    assert rec.params["bake_maps"]["device"] == "optix"
    for op in ("prepare_delivery_scene", "decimate_to_budget", "render_views"):
        assert "device" not in rec.params[op], f"{op} must not carry a device"
    # the bake timeout still threads independently
    assert rec.timeouts["bake_maps"] == 300.0


def test_bake_device_default_is_auto(tmp_path):
    """Default is auto-detect — NOT hardcoded either way (constraint of the
    GPU fix: CI and the old laptop must keep working via CPU fallback)."""
    rec = _RecordingRunner()
    _run_refusal_chain(rec, tmp_path / "pkgs")
    assert rec.params["bake_maps"]["device"] == "auto"


def test_bake_device_evidence_recorded_in_report(tmp_path):
    """The device ACTUALLY used is recorded in qa_report.json (requested,
    GPU/CPU, enabled devices, fallback reason) — a GPU bake must be a
    recorded fact, never an assumption."""
    rec = _RecordingRunner(device_evidence=dict(_GPU_EVIDENCE))
    _run_refusal_chain(rec, tmp_path / "pkgs", bake_device="optix")
    report = json.loads(
        (tmp_path / "blocked" / "MAYA00053153" / "qa_report.json")
        .read_text(encoding="utf-8"))
    fin = report["finish"]
    assert fin["bake_device"] == "optix"
    assert fin["bake_device_resolved"] == _GPU_EVIDENCE


def test_step_timings_recorded_in_report(tmp_path):
    """Per-step wall clocks land in qa_report under finish.step_timings_sec
    (PLAN_AUTONOMOUS §7 states budgets; the report states reality)."""
    rec = _RecordingRunner()
    _run_refusal_chain(rec, tmp_path / "pkgs")
    report = json.loads(
        (tmp_path / "blocked" / "MAYA00053153" / "qa_report.json")
        .read_text(encoding="utf-8"))
    timings = report["finish"]["step_timings_sec"]
    for step in ("prepare_scene", "bake_maps", "decimate_lp",
                 "review_renders", "total_chain"):
        assert step in timings, f"missing per-step timing: {step}"
        assert timings[step] >= 0.0
    assert timings["total_chain"] >= max(
        v for k, v in timings.items() if k != "total_chain")


# ── harness side (blender-marked; auto-skip without Blender) ─────────────────


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
def probe_blend(tmp_path_factory) -> Path:
    """Tiny UV'd plane .blend — the cheapest scene a real bake accepts."""
    runner = _get_runner()
    tmp = tmp_path_factory.mktemp("devprobe")
    blend = tmp / "probe.blend"
    code = f"""
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_grid_add(x_subdivisions=8, y_subdivisions=8, size=1.0, location=(0, 0, 0))
obj = bpy.context.active_object
obj.name = "probe"
me = obj.data
uv = me.uv_layers.new(name="UVMap")
me.uv_layers.active = uv
for loop in me.loops:
    v = me.vertices[loop.vertex_index]
    uv.data[loop.index].uv = (v.co.x + 0.5, v.co.y + 0.5)
bpy.ops.wm.save_as_mainfile(filepath=r"{blend}")
RESULT = {{"blend": r"{blend}"}}
"""
    result = runner.execute_op("run_script", {"code": code})
    assert result["success"], result.get("error")
    return blend


def _bake(runner, blend, tmp_path, device):
    return runner.execute_op("bake_maps", {
        "input": str(blend),
        "out_dir": str(tmp_path),
        "maps": ["ao"],
        "resolution": 64,
        "samples": 4,
        "device": device,
    }, timeout_sec=300)


@pytest.mark.blender
def test_device_evidence_shape_auto(runner, probe_blend, tmp_path):
    """`auto` picks a device and reports consistent evidence: GPU ⇒ a
    compute type and at least one enabled device; CPU ⇒ a fallback reason.
    (On the RTX 4080 Super desktop this is OPTIX; on CI, CPU — both valid.)"""
    result = _bake(runner, probe_blend, tmp_path, "auto")
    assert result["success"], result.get("error")
    info = result["device"]
    assert info["requested"] == "auto"
    assert info["device"] in ("GPU", "CPU")
    if info["device"] == "GPU":
        assert info["compute_device_type"] in ("OPTIX", "CUDA", "HIP",
                                               "ONEAPI", "METAL")
        assert info["devices_enabled"], "GPU without any enabled device?"
        assert info["fallback_reason"] is None
    else:
        assert info["fallback_reason"], "CPU without a stated fallback reason"


@pytest.mark.blender
def test_device_cpu_explicit(runner, probe_blend, tmp_path):
    """Explicit cpu: CPU, and NOT a fallback (the operator asked for it)."""
    result = _bake(runner, probe_blend, tmp_path, "cpu")
    assert result["success"], result.get("error")
    info = result["device"]
    assert info["device"] == "CPU"
    assert info["fallback_reason"] is None


@pytest.mark.blender
def test_unknown_device_falls_back_cleanly(runner, probe_blend, tmp_path):
    """An unavailable/unknown device type must fall back to CPU CLEANLY —
    the bake still succeeds, the fallback is stated in the evidence AND in
    the op warnings. CI and GPU-less hosts depend on this."""
    result = _bake(runner, probe_blend, tmp_path, "nosuchtype")
    assert result["success"], result.get("error")
    info = result["device"]
    assert info["device"] == "CPU"
    assert "NOSUCHTYPE" in (info["fallback_reason"] or "")
    assert any("NOSUCHTYPE" in w for w in result.get("warnings", []))
