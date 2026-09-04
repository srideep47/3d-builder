"""Phase 4 intake tests — dynamic constraints + prompt → JobCard.

Three groups, all Blender-free:
1. SizeCap / effective-value helpers / card validation — the card is the
   single place an owner prompt overrides a constraint, and the helpers are
   the single resolution point (card > contract default).
2. intake_from_prompt — deterministic extraction; every ambiguity or silence
   is a LOUD IntakeError (rule 9 extends to every constraint: never a unit,
   never a cap target, never one of two ceilings).
3. Gates + finish_delivery threading with overridden cards (stubbed runner
   + fake independent FBX parse — the real chain is blender-marked
   elsewhere); pins that the enforced numbers equal the card's.
"""

import json
import zipfile
from pathlib import Path

import pytest

from src.client import package as package_mod
from src.client.contract import MB, REQUIRED_DELIVERABLES
from src.client.gates import MeshFacts, check_file_sizes, check_naming, check_polycount
from src.client.job import (
    IntakeError,
    JobCard,
    SizeCap,
    dump_job_yaml,
    intake_from_prompt,
    load_job,
)
from src.spec.schema import ObjectSpec

BOX_SPEC = {
    "schema_version": "2.0.0",
    "name": "intake_box",
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
}


def make_job(**overrides) -> JobCard:
    data = {
        "job_code": "INTAKE0001",
        "dims": {"length": 12.0, "width": 34.0, "height": 5.0, "unit": "in"},
        "complexity": "simple",
        "orientation": "floor",
        "product_class": "widget",
        "reference_dir": "input/refs",
    }
    data.update(overrides)
    return JobCard.model_validate(data)


def intake(prompt: str, **kw) -> JobCard:
    """intake_from_prompt with the structural args prefilled."""
    base = dict(job_code="INTAKE0001", product_class="widget",
                reference_dir=Path("input/refs"))
    base.update(kw)
    return intake_from_prompt(prompt, **base)


def expect_intake_error(prompt: str, *, match: str, **kw) -> None:
    with pytest.raises(IntakeError, match=match):
        intake(prompt, **kw)


# ── SizeCap ───────────────────────────────────────────────────────────────────


def test_sizecap_basis_changes_the_byte_count():
    """MB and MiB differ by ~4.9% — the basis is carried, never assumed."""
    decimal = SizeCap(value=10, basis="MB")
    binary = SizeCap(value=10, basis="MiB")
    assert decimal.max_bytes == 10_000_000
    assert binary.max_bytes == 10 * (1 << 20)
    assert binary.max_bytes > decimal.max_bytes
    assert decimal.describe() == "10MB" and binary.describe() == "10MiB"


def test_sizecap_requires_positive_value():
    with pytest.raises(ValueError):
        SizeCap(value=0, basis="MB")


# ── effective-value helpers ──────────────────────────────────────────────────


def test_effective_polycount_ceiling_card_overrides_tier():
    assert make_job().effective_polycount_ceiling() == 50_000  # contract simple
    assert make_job(polycount_ceiling=1234).effective_polycount_ceiling() == 1234


def test_effective_polycount_ceiling_unblocks_complex_tier():
    """complex has no known ceiling (fail closed) until the owner states one."""
    complex_job = make_job(complexity="complex")
    assert complex_job.effective_polycount_ceiling() is None
    assert make_job(complexity="complex",
                    polycount_ceiling=250_000).effective_polycount_ceiling() == 250_000


def test_effective_semantics_defaults_to_triangle_equivalent():
    assert make_job().effective_polycount_semantics() == "triangle_equivalent"
    assert make_job(polycount_semantics="faces").effective_polycount_semantics() == "faces"


def test_effective_required_suffixes_default_and_override():
    assert make_job().effective_required_suffixes() == \
        [d.suffix for d in REQUIRED_DELIVERABLES]
    job = make_job(required_formats=[".fbx", ".spp"])
    assert job.effective_required_suffixes() == [".fbx", ".spp"]


def test_effective_size_cap_override_and_fallbacks():
    job = make_job(file_size_caps={".fbx": SizeCap(value=12, basis="MiB")})
    assert job.effective_size_cap(".fbx").max_bytes == 12 * (1 << 20)
    assert job.effective_size_cap("_LP.glb").max_bytes == 15 * MB  # contract
    assert job.effective_size_cap("_LP.usdz") is None  # unknown cap
    assert job.effective_size_cap(".nope") is None  # not a deliverable


def test_effective_texture_resolution():
    assert make_job().effective_texture_resolution() == 1024
    assert make_job(texture_resolution=4096).effective_texture_resolution() == 4096
    assert make_job(texture_resolution=4096).effective_texture_resolution(2048) == 4096


# ── card validation ──────────────────────────────────────────────────────────


def test_required_formats_must_be_producible():
    with pytest.raises(ValueError, match=r"cannot produce.*\.obj"):
        make_job(required_formats=[".fbx", ".obj"])


def test_required_formats_must_be_nonempty():
    with pytest.raises(ValueError, match="non-empty"):
        make_job(required_formats=[])


def test_file_size_caps_must_target_known_deliverables():
    with pytest.raises(ValueError, match="unknown"):
        make_job(file_size_caps={".obj": SizeCap(value=5, basis="MB")})


def test_fbx_axes_must_be_set_as_a_pair():
    with pytest.raises(ValueError, match="TOGETHER"):
        make_job(fbx_axis_up="Z")


def test_fbx_axes_must_not_be_parallel():
    with pytest.raises(ValueError, match="parallel"):
        make_job(fbx_axis_up="Y", fbx_axis_forward="-Y")
    make_job(fbx_axis_up="Z", fbx_axis_forward="Y")  # valid pair


# ── intake_from_prompt: extraction ───────────────────────────────────────────


def test_intake_full_prompt_extracts_every_constraint():
    prompt = (
        "Desk for the west-wing office. Dims 16 x 12 x 5 in.\n"
        "Max 80,000 triangles. FBX under 12 MiB; LP max 20 MB.\n"
        "2K textures. Formats: FBX, GLB and USDZ.\n"
        "Floor-standing. complexity: simple.\n"
        "length along Y, width along X, height along Z.\n"
        "FBX axis convention: up Y, forward -Z."
    )
    card = intake(prompt)
    assert (card.dims.length, card.dims.width, card.dims.height, card.dims.unit) \
        == (16.0, 12.0, 5.0, "in")
    assert card.polycount_ceiling == 80_000
    assert card.polycount_semantics == "triangles"
    assert card.file_size_caps[".fbx"] == SizeCap(value=12, basis="MiB")
    assert card.file_size_caps["_LP.glb"] == SizeCap(value=20, basis="MB")
    assert card.texture_resolution == 2048
    assert card.required_formats == [".fbx", "_LP.glb", "_HP.glb", "_LP.usdz"]
    assert card.axis_map.model_dump() == {"length": "y", "width": "x", "height": "z"}
    assert (card.fbx_axis_up, card.fbx_axis_forward) == ("Y", "-Z")
    assert card.complexity == "simple" and card.orientation == "floor"
    # provenance rides along into qa_report.json
    assert card.intake_evidence["polycount_ceiling"].startswith("80,000 from")


def test_intake_metric_dims_and_noun_first_polycount():
    card = intake(
        "Cabinet. 0.4 x 0.3 x 0.2 m. Polycount ceiling 200k. Wall-mounted.",
        complexity="medium", orientation=None)
    assert (card.dims.length, card.dims.width, card.dims.height, card.dims.unit) \
        == (0.4, 0.3, 0.2, "m")
    assert card.polycount_ceiling == 200_000
    assert card.polycount_semantics is None  # "polycount" states no semantics
    assert card.orientation == "wall"


def test_intake_faces_semantics_and_k_suffix():
    card = intake("Panel. Dims 12x12x65IN. Faces limited to 8,000. Tabletop.",
                  complexity="simple", orientation=None)
    assert card.polycount_ceiling == 8_000
    assert card.polycount_semantics == "faces"
    assert card.dims.unit == "in"  # stored lowercased; canonical_unit handles case
    assert card.orientation == "tabletop"


def test_intake_px_resolution_and_num_first_caps():
    card = intake(
        "Sign. Dims 40 x 10 x 40 cm. Textures at 4096 px. "
        "No more than 12 MiB for the HP.",
        complexity="simple", orientation="wall")
    assert card.texture_resolution == 4096
    assert card.file_size_caps["_HP.glb"] == SizeCap(value=12, basis="MiB")


def test_intake_explicit_args_beat_prompt_statements():
    card = intake("Dims 16 x 12 x 5 in. complexity: medium.",
                  complexity="simple", orientation="floor")
    assert card.complexity == "simple"


# ── intake_from_prompt: rule 9 and loud refusals ─────────────────────────────


def test_intake_bare_dims_without_unit_never_defaults_a_unit():
    expect_intake_error("Box. Dims 16 x 12 x 5.", match="NO unit")


def test_intake_no_dims_refuses_to_invent():
    expect_intake_error("A lovely product, no numbers.", match="never inferred")


def test_intake_conflicting_dims_error():
    expect_intake_error("Dims 16 x 12 x 5 in. Again: 17 x 12 x 5 in.",
                        match="DIFFERENT values")


def test_intake_conflicting_polycount_error():
    expect_intake_error("Dims 16 x 12 x 5 in. Max 50k tris; ceiling 80,000 triangles.",
                        match="polycount.*DIFFERENT")


def test_intake_orphan_size_cap_never_guesses_the_target():
    expect_intake_error("Dims 16 x 12 x 5 in. File size max 20 MB.",
                        match="without naming which deliverable")


def test_intake_unknown_format_token_fails_loud():
    expect_intake_error("Dims 16 x 12 x 5 in. Formats: FBX, OBJ.",
                        match="does not produce")


def test_intake_partial_axis_map_error():
    expect_intake_error("Dims 16 x 12 x 5 in. length along Y only.",
                        match=r"partial axis map|only \['length'\]")


def test_intake_fbx_up_without_forward_error():
    expect_intake_error("Dims 16 x 12 x 5 in. The FBX must be Y-up.",
                        match="without a forward")


def test_intake_missing_complexity_and_orientation_error():
    expect_intake_error("Dims 16 x 12 x 5 in.", match="no complexity")
    expect_intake_error("Dims 16 x 12 x 5 in.", match="no orientation",
                        complexity="simple")


def test_intake_conflicting_resolution_error():
    expect_intake_error(
        "Dims 16 x 12 x 5 in. 1024px bake, but 2K textures on the label.",
        match="texture resolution.*DIFFERENT",
        complexity="simple", orientation="floor")


def test_intake_placeholder_dims_need_a_unit():
    with pytest.raises(IntakeError, match="placeholder_unit"):
        intake("No dims.", placeholder_dims=(1.0, 2.0, 3.0),
               complexity="simple", orientation="floor")


def test_intake_placeholder_path_builds_a_refused_card():
    card = intake("No dims in this prompt.",
                  placeholder_dims=(60.0, 80.0, 10.0), placeholder_unit="in",
                  complexity="simple", orientation="floor")
    assert card.dims_placeholder is True
    assert (card.dims.length, card.dims.width, card.dims.height) == (60.0, 80.0, 10.0)
    assert "PLACEHOLDER" in card.intake_evidence["dims"]


# ── YAML round-trip ──────────────────────────────────────────────────────────


def test_dump_job_yaml_round_trips(tmp_path):
    card = intake(
        "Desk. Dims 16 x 12 x 5 in. Max 80,000 triangles. "
        "FBX under 12 MiB. 2K textures. Floor-standing.",
        complexity="simple", orientation=None)
    path = tmp_path / "job.yaml"
    path.write_text(dump_job_yaml(card), encoding="utf-8")
    reloaded = load_job(path)
    assert reloaded == card
    assert reloaded.effective_size_cap(".fbx").max_bytes == 12 * (1 << 20)
    assert reloaded.effective_polycount_ceiling() == 80_000


# ── gates with overridden cards ──────────────────────────────────────────────


def make_facts(**kw) -> MeshFacts:
    base = dict(tri_count=8_000, quad_count=0, ngon_count=0,
                triangle_equivalent=8_000, faces_total=8_000,
                bounds_min_m=(0.0, 0.0, 0.0), bounds_max_m=(0.3, 0.8, 0.1))
    base.update(kw)
    return MeshFacts(**base)


def test_polycount_gate_complex_fails_closed_until_overridden():
    job = make_job(complexity="complex")
    r = check_polycount(job, make_facts(triangle_equivalent=200_000))
    assert not r.passed and "no polycount_ceiling override" in r.message

    job = make_job(complexity="complex", polycount_ceiling=250_000)
    r = check_polycount(job, make_facts(triangle_equivalent=200_000))
    assert r.passed and "Max: 250,000" in r.message


def test_polycount_gate_semantics_change_the_counted_value():
    """faces semantics: 150k faces (290k tri-eq) passes a 200k ceiling that
    triangle_equivalent would fail — semantics is a real constraint."""
    facts = make_facts(faces_total=150_000, triangle_equivalent=290_000,
                       tri_count=290_000)
    job = make_job(polycount_ceiling=200_000, polycount_semantics="faces")
    r = check_polycount(job, facts)
    assert r.passed and "counted as faces" in r.message

    job_default = make_job(polycount_ceiling=200_000)
    assert not check_polycount(job_default, facts).passed


def test_naming_gate_honours_required_formats(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "INTAKE0001.fbx").write_bytes(b"x")
    job = make_job(required_formats=[".fbx"])
    assert check_naming(pkg, job).passed  # only the fbx is required
    assert not check_naming(pkg, make_job()).passed  # full contract still demands 9


def test_file_size_gate_honours_basis(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "INTAKE0001.fbx").write_bytes(b"\0" * 12_000_000)
    # 12 MiB = 12,582,912 bytes: passes; the contract's 10 MB decimal would fail
    job = make_job(file_size_caps={".fbx": SizeCap(value=12, basis="MiB")})
    r = check_file_sizes(pkg, job)
    assert r.passed and ".fbx ≤ 12MiB" in r.expected

    job = make_job(file_size_caps={".fbx": SizeCap(value=12, basis="MB")})
    (pkg / "INTAKE0001.fbx").write_bytes(b"\0" * 12_000_001)
    r = check_file_sizes(pkg, job)
    assert not r.passed and "12.00MB > 12MB" in r.message


# ── finish_delivery threading (stubbed chain + fake FBX parse) ───────────────


class _FakeFbxInfo:
    """The independent-parse seam: _assemble_and_audit cross-checks against
    this instead of a real binary parse (the real one is blender-marked)."""

    version = 7400
    creator = "stub"

    class axes:
        @staticmethod
        def to_dict():
            return {"up": "Z", "forward": "Y"}

    @staticmethod
    def world_extents_m():
        return [0.4, 0.3, 0.2]  # sorted desc, matching the stub topology

    @staticmethod
    def ngon_count():
        return 0

    @staticmethod
    def faces_total():
        return 6

    @staticmethod
    def triangle_equivalent():
        return 6


class _FinishChainStub:
    """Full finish chain with real files on disk: every op the finish path
    touches, params recorded, deliverables written where the code expects."""

    def __init__(self):
        self.calls = []

    def execute_op(self, op, params=None, timeout_sec=None):
        self.calls.append((op, dict(params or {})))
        if op == "prepare_delivery_scene":
            return {"success": True, "uv_atlas": {"pack_scale": 0.75},
                    "uv": {"islands_total": 12}}
        if op == "bake_maps":
            out = Path(params["out_dir"])
            out.mkdir(parents=True, exist_ok=True)
            for name in ("basecolor", "normal", "roughness", "metallic", "ao"):
                (out / f"{name}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            Path(params["hp_glb"]).write_bytes(b"hp")
            return {"success": True, "device": {"compute_device_type": "OPTIX"},
                    "hp_triangle_equivalent": 2016,
                    "maps": {"basecolor": {"stats": {"std": 0.3}}}}
        if op == "decimate_to_budget":
            Path(params["output"]).write_bytes(b"lp")
            return {"success": True, "triangle_equivalent": 6, "decimated": False}
        if op == "export_fbx":
            Path(params["path"]).write_bytes(b"fbx")
            return {"success": True}
        if op == "export_usdz":
            # a real ZIP_STORED usdz so the structure report has a layer
            with zipfile.ZipFile(params["path"], "w", zipfile.ZIP_STORED) as zf:
                zf.writestr("model.usda", "#usda 1.0\n")
            return {"success": True, "method": "stub"}
        if op == "topology_report":
            return {
                "success": True, "triangles": 6, "quads": 0, "ngons": 0,
                "vertices": 8, "faces_total": 6, "triangle_equivalent": 6,
                "loose_vertices": 0, "loose_edges": 0, "boundary_edges": 24,
                "nonmanifold_edges": 0,
                "bounds": {"min": [0.0, 0.0, 0.0], "max": [0.4, 0.3, 0.2]},
            }
        if op == "info":
            return {"success": True, "blender_version": "stub"}
        raise AssertionError(f"unexpected op {op!r}")


def _card_for_finish(**overrides) -> JobCard:
    # dims match the stub topology bounds → the dimensions gate goes green
    data = {
        "job_code": "INTAKE0001",
        "dims": {"length": 0.4, "width": 0.3, "height": 0.2, "unit": "m"},
        "complexity": "simple",
        "orientation": "floor",
        "product_class": "widget",
        "reference_dir": "input/refs",
        "polycount_ceiling": 1234,
        "texture_resolution": 2048,
        "fbx_axis_up": "Z",
        "fbx_axis_forward": "Y",
        "required_formats": [".fbx", "_LP.glb"],
    }
    data.update(overrides)
    return JobCard.model_validate(data)


@pytest.fixture
def fake_fbx(monkeypatch):
    monkeypatch.setattr(package_mod, "read_fbx_info", lambda p: _FakeFbxInfo())


def test_finish_threads_card_constraints(fake_fbx, tmp_path):
    stub = _FinishChainStub()
    report = package_mod.finish_delivery(
        _card_for_finish(),
        ObjectSpec.model_validate(BOX_SPEC),
        out_root=tmp_path / "packages",
        runner=stub,
        log=lambda msg: None,
        resolution=None,  # the CLI default: the card drives the bake
        review_renders=False,
    )
    by_op = {}
    for op, params in stub.calls:
        by_op.setdefault(op, []).append(params)

    # ceiling: card 1234 overrides the tier table AND the spec tri_budget
    assert by_op["decimate_to_budget"][0]["budget"] == 1234
    assert report["finish"]["lp_budget"] == 1234
    # resolution: explicit None → the card's 2048 (not the 1024 default)
    assert by_op["bake_maps"][0]["resolution"] == 2048
    assert report["finish"]["texture_resolution"] == 2048
    # FBX axes: the card's pair, in the export call and the qa_report
    assert by_op["export_fbx"][0]["axis_up"] == "Z"
    assert by_op["export_fbx"][0]["axis_forward"] == "Y"
    assert report["axis_convention"]["requested"] == \
        {"axis_up": "Z", "axis_forward": "Y"}
    # required-set override: gates check only the card's list; extras flagged
    assert report["contract_note"]["required_suffixes"] == [".fbx", "_LP.glb"]
    by_required = {f["name"]: f["required"] for f in report["files"]}
    assert by_required["INTAKE0001.fbx"] is True
    assert by_required["INTAKE0001_LP.usdz"] is False
    # the stub bounds match the card dims → every gate is green
    assert report["all_passed"] is True, \
        [g for g in report["gates"] if not g["passed"]]


def test_finish_explicit_resolution_beats_the_card(fake_fbx, tmp_path):
    stub = _FinishChainStub()
    package_mod.finish_delivery(
        _card_for_finish(), ObjectSpec.model_validate(BOX_SPEC),
        out_root=tmp_path / "packages", runner=stub, log=lambda msg: None,
        resolution=4096, review_renders=False)
    bake = next(p for op, p in stub.calls if op == "bake_maps")
    assert bake["resolution"] == 4096
