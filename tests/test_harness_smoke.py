"""End-to-end Blender harness tests. Marked `blender` — skipped automatically
when no Blender installation is found on this machine."""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.blender

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
def desk_run(tmp_path_factory):
    """Build the sample desk spec once; other tests reuse its GLB."""
    runner = _get_runner()
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import ObjectSpec

    spec_path = PROJECT_ROOT / "input" / "sample_desk.spec.json"
    spec = ObjectSpec.model_validate(json.loads(spec_path.read_text(encoding="utf-8")))
    out = tmp_path_factory.mktemp("desk") / "desk.glb"
    params = resolve_spec_to_build_params(spec, output_glb_path=str(out))
    result = runner.execute_op("build_from_spec", params)
    return result, out


def test_info(runner):
    result = runner.execute_op("info", {})
    assert result["success"]
    assert result["blender_version"].startswith("Blender")


def test_build_sample_desk(desk_run):
    result, glb = desk_run
    assert result["success"], result.get("error")
    assert glb.exists()
    assert result["parts_created"] == 5
    assert set(result["part_names"]) == {"tabletop", "leg_fl", "leg_fr", "leg_bl", "leg_br"}


def test_measure_sample_desk(runner, desk_run):
    _, glb = desk_run
    result = runner.execute_op("measure", {"model_path": str(glb)})
    assert result["success"]
    dims = result["overall"]["dimensions"]
    assert dims[0] == pytest.approx(1.4, abs=0.003)
    assert dims[1] == pytest.approx(0.7, abs=0.003)
    assert dims[2] == pytest.approx(0.76, abs=0.003)
    # Ground contact: the whole model sits on Z = 0.
    assert result["overall"]["min"][2] == pytest.approx(0.0, abs=0.001)
    assert "leg_fl" in result["parts"]


def test_dimension_gate_on_sample_desk(runner, desk_run):
    from src.spec.schema import ObjectSpec
    from src.spec.validation import evaluate_dimension_gate

    _, glb = desk_run
    spec = ObjectSpec.model_validate(
        json.loads((PROJECT_ROOT / "input" / "sample_desk.spec.json").read_text(encoding="utf-8"))
    )
    measure = runner.execute_op("measure", {"model_path": str(glb)})
    result = evaluate_dimension_gate(spec, measure)
    assert result.passed, result.ground_contact_failures


def test_render_views(runner, desk_run, tmp_path):
    _, glb = desk_run
    result = runner.execute_op(
        "render_views",
        {
            "model_path": str(glb),
            "views": ["front", "iso"],
            "output_dir": str(tmp_path),
            "resolution": [256, 256],
        },
    )
    assert result["success"]
    for view in ("front", "iso"):
        assert Path(result["views"][view]).exists()


def test_render_views_closeups_frame_the_named_part(runner, desk_run, tmp_path):
    """Round 4: close-ups are the reviewer's instrument — whole-model views
    crush small features. The op must frame the NAMED part (not the whole
    model): the framed part's content is centred in the image, and a bogus
    part name is reported as a skip, never a crash."""
    import numpy as np
    from PIL import Image

    _, glb = desk_run
    result = runner.execute_op(
        "render_views",
        {
            "model_path": str(glb),
            "views": ["front"],
            "output_dir": str(tmp_path),
            "resolution": [256, 256],
            "closeups": [
                {"name": "leg", "part": "leg_fl", "direction": "front",
                 "frame": "part", "pad": 0.5},
                {"name": "bogus", "part": "not_a_part", "direction": "front",
                 "frame": "part", "pad": 0.5},
            ],
        },
    )
    assert result["success"]
    assert result["closeup_skips"] == ["bogus: part 'not_a_part' not in scene"]
    leg_png = Path(result["views"]["leg"])
    assert leg_png.exists()
    a = np.asarray(Image.open(leg_png).convert("RGBA"))
    opaque = a[:, :, 3] > 0
    assert opaque.any()
    ys, xs = np.nonzero(opaque)
    # the framed part is centred (camera aims at its bounds centre)
    assert abs(xs.mean() - 127.5) < 40 and abs(ys.mean() - 127.5) < 40
    # ...and the frame is leg-sized, not model-sized: the desk overflows the
    # tight frame (the tabletop crosses the frame's right edge — the left
    # edge hangs past the model into background), and the model's vertical
    # extent grows from ~47% of the image (whole-model view: ortho scale is
    # 1.15x the largest extent) to ~70% (leg bounds + 50% pad), i.e. the
    # framed part renders ~1.5x taller in pixels
    full = np.asarray(Image.open(result["views"]["front"]).convert("RGBA"))
    full_op = full[:, :, 3] > 0
    assert opaque[:, 0].any() or opaque[:, -1].any()
    assert not full_op[:, 0].any() and not full_op[:, -1].any()
    fys, _ = np.nonzero(full_op)
    assert (ys.max() - ys.min()) > 1.3 * (fys.max() - fys.min())


def test_radial_array_build(runner, tmp_path):
    """A radial array must distribute clones around the world origin, not
    rotate them in place (the bug this test guards against)."""
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import Modifiers, ObjectSpec, PartSpec, RadialArrayModifier

    spec = ObjectSpec(
        name="star base",
        parts=[
            PartSpec(
                name="spoke",
                dimensions=[0.30, 0.03, 0.02],
                position=[0.16, 0.0, 0.01],
                modifiers=Modifiers(radial_array=RadialArrayModifier(count=5)),
            )
        ],
    )
    out = tmp_path / "star.glb"
    runner.execute_op("build_from_spec", resolve_spec_to_build_params(spec, output_glb_path=str(out)))
    result = runner.execute_op("measure", {"model_path": str(out)})
    dims = result["overall"]["dimensions"]
    # 5 spokes at radius 0.16 with length 0.30 (centered at x=0.16) span a
    # ~0.62 m circle in X and Y — in-place rotation would give ~0.30 m.
    assert dims[0] > 0.5, f"radial array collapsed: {dims}"
    assert dims[1] > 0.5, f"radial array collapsed: {dims}"


def test_revolve_watertight_vase(runner, tmp_path):
    from src.agent.verifier import load_merged_mesh
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import ObjectSpec, PartSpec

    spec = ObjectSpec(
        name="vase",
        parts=[
            PartSpec(
                name="body",
                shape="revolve_lathe",
                dimensions=[0.2, 0.2, 0.3],
                profile_points=[[0.0, 0.0], [0.08, 0.0], [0.1, 0.15], [0.05, 0.25], [0.06, 0.3], [0.0, 0.3]],
            )
        ],
    )
    out = tmp_path / "vase.glb"
    runner.execute_op("build_from_spec", resolve_spec_to_build_params(spec, output_glb_path=str(out)))
    result = runner.execute_op("measure", {"model_path": str(out)})
    assert result["overall"]["dimensions"][2] == pytest.approx(0.3, abs=0.003)
    mesh = load_merged_mesh(out)
    assert mesh is not None
    assert mesh.is_watertight, "revolved vase should be closed"
    assert mesh.volume > 0


def test_boolean_difference(runner, tmp_path):
    from src.agent.verifier import load_merged_mesh
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import Modifiers, ObjectSpec, PartSpec, BooleanModifier

    spec = ObjectSpec(
        name="slotted block",
        parts=[
            PartSpec(
                name="block",
                dimensions=[0.2, 0.2, 0.2],
                modifiers=Modifiers(boolean=BooleanModifier(operation="difference", target_part="cutter")),
            ),
            PartSpec(name="cutter", shape="cylinder", dimensions=[0.05, 0.05, 0.3]),
        ],
    )
    out = tmp_path / "block.glb"
    runner.execute_op("build_from_spec", resolve_spec_to_build_params(spec, output_glb_path=str(out)))
    result = runner.execute_op("measure", {"model_path": str(out)})
    # The cutter part is consumed by the boolean.
    assert "cutter" not in result["parts"]
    mesh = load_merged_mesh(out)
    assert mesh is not None
    assert mesh.is_watertight
    # Cube 0.008 m³ minus the r=0.025 hole through 0.2 m: π·0.025²·0.2 ≈ 0.00039.
    assert mesh.volume == pytest.approx(0.008 - 3.14159265 * 0.025**2 * 0.2, rel=0.02)


def test_run_script_returns_result(runner, tmp_path):
    result = runner.execute_op("run_script", {"code": "RESULT = {'answer': 42}"})
    assert result["success"]
    assert result["result"] == {"answer": 42}
