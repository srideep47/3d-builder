"""T1 compliance-spine tests — pure gate logic, no Blender required.

A synthetic package that passes every gate, plus one deliberately broken
fixture per gate proving each fails independently (GLM_BRIEF T1 exit
criteria). Mesh-dependent gates consume MeshFacts values directly; the
harness ops that produce them from real files are covered (Blender-marked)
in tests/test_client_ops.py.
"""

from pathlib import Path

import pytest
import yaml

from src.client import units
from src.client.contract import REQUIRED_DELIVERABLES, required_filenames
from src.client.gates import (
    MeshFacts,
    check_dimensions,
    check_file_sizes,
    check_naming,
    check_ngons,
    check_orientation,
    check_polycount,
    run_all_gates,
)
from src.client.job import JobCard, load_job

JOB_CODE = "TESTJOB0001"

# 12 × 34 × 5 in — deliberately non-square so a transposed L/W is detectable
# (their validator derives aspect ratios from the L/W/H assignment).
DIMS_IN = {"length": 12.0, "width": 34.0, "height": 5.0, "unit": "in"}


def make_job(**overrides) -> JobCard:
    data = {
        "job_code": JOB_CODE,
        "dims": DIMS_IN,
        "complexity": "simple",
        "orientation": "floor",
        "product_class": "widget",
        "part_scope": "test fixture only",
        "reference_dir": "input/refs",
    }
    data.update(overrides)
    return JobCard.model_validate(data)


def make_facts(job: JobCard, *, ngon_count: int = 0, triangle_equivalent: int = 8_000,
               swap_xy: bool = False, min_z: float = 0.0,
               width_delta_in: float = 0.0) -> MeshFacts:
    """Facts matching the job's expected bounds (metres), with knobs to break
    individual gates. swap_xy transposes the two horizontal extents — the
    multiset of extents stays correct while the axis assignment goes wrong."""
    b = job.expected_bounds_m()
    x, y = (b["y"], b["x"]) if swap_xy else (b["x"], b["y"])
    y -= units.to_metres(width_delta_in, "in")
    z = b["z"]
    return MeshFacts(
        tri_count=triangle_equivalent,
        quad_count=0,
        ngon_count=ngon_count,
        triangle_equivalent=triangle_equivalent,
        bounds_min_m=(0.0, 0.0, min_z),
        bounds_max_m=(x, y, min_z + z),
        source=f"{job.job_code}.fbx",
    )


def write_package(pkg: Path, job_code: str = JOB_CODE) -> Path:
    pkg.mkdir(parents=True, exist_ok=True)
    for d in REQUIRED_DELIVERABLES:
        (pkg / (job_code + d.suffix)).write_bytes(b"fixture-bytes")
    return pkg


def write_job_yaml(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "job.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


@pytest.fixture
def job() -> JobCard:
    return make_job()


@pytest.fixture
def package(tmp_path) -> Path:
    return write_package(tmp_path / "packages" / JOB_CODE)


# ── Unit layer ───────────────────────────────────────────────────────────────


def test_unit_conversions_exact():
    assert units.to_metres(1, "in") == pytest.approx(0.0254)
    assert units.to_metres(1, "ft") == pytest.approx(0.3048)
    assert units.from_metres(0.3048, "in") == pytest.approx(12.0)
    assert units.convert(12, "in", "cm") == pytest.approx(30.48)
    assert units.convert(1, "m", "mm") == pytest.approx(1000.0)


def test_unit_aliases_normalise():
    assert units.canonical_unit("IN") == "in"
    assert units.canonical_unit("inches") == "in"
    assert units.canonical_unit("Metres") == "m"
    assert units.canonical_unit("mm") == "mm"


def test_unknown_unit_raises_loudly():
    with pytest.raises(ValueError, match="cubits"):
        units.canonical_unit("cubits")


# ── Job card ─────────────────────────────────────────────────────────────────


def test_load_valid_job(tmp_path):
    p = write_job_yaml(tmp_path, {
        "job_code": "MAYA00053153",
        "dims": {"length": 60.0, "width": 80.0, "height": 10.0, "unit": "IN"},
        "complexity": "simple",
        "orientation": "floor",
        "product_class": "demo",
        "part_scope": "demo only",
        "reference_dir": "input/refs",
    })
    job = load_job(p)
    assert job.job_code == "MAYA00053153"
    assert job.canonical_unit == "in"
    # Default axis map L→X, W→Y, H→Z (owner amendment 2)
    assert job.expected_bounds_m() == pytest.approx(
        {"x": 60 * 0.0254, "y": 80 * 0.0254, "z": 10 * 0.0254})
    # Default client tolerance ±0.01 in (owner amendment 3), separate from
    # the internal ±1 mm build tolerance
    assert job.dim_tolerance_in_job_units() == pytest.approx(0.01)
    assert job.dim_tolerance_m() == pytest.approx(0.01 * 0.0254)


def test_load_job_missing_dims_fails_loudly(tmp_path):
    p = write_job_yaml(tmp_path, {"job_code": "X1", "complexity": "simple",
                                  "orientation": "floor", "product_class": "d",
                                  "reference_dir": "r"})
    with pytest.raises(ValueError, match="dims"):
        load_job(p)


def test_load_job_missing_unit_fails_loudly(tmp_path):
    p = write_job_yaml(tmp_path, {
        "job_code": "X1", "dims": {"length": 1, "width": 2, "height": 3},
        "complexity": "simple", "orientation": "floor",
        "product_class": "d", "reference_dir": "r"})
    with pytest.raises(ValueError, match="unit"):
        load_job(p)


def test_load_job_unknown_unit_rejected(tmp_path):
    p = write_job_yaml(tmp_path, {
        "job_code": "X1", "dims": {"length": 1, "width": 2, "height": 3, "unit": "cubits"},
        "complexity": "simple", "orientation": "floor",
        "product_class": "d", "reference_dir": "r"})
    with pytest.raises(ValueError, match="cubits"):
        load_job(p)


def test_load_job_rejects_pathlike_job_code(tmp_path):
    p = write_job_yaml(tmp_path, {
        "job_code": "MAYA/../../evil", "dims": DIMS_IN, "complexity": "simple",
        "orientation": "floor", "product_class": "d", "reference_dir": "r"})
    with pytest.raises(ValueError, match="job_code"):
        load_job(p)


def test_axis_map_must_be_permutation(tmp_path):
    p = write_job_yaml(tmp_path, {
        "job_code": "X1", "dims": DIMS_IN, "complexity": "simple",
        "orientation": "floor", "product_class": "d", "reference_dir": "r",
        "axis_map": {"length": "x", "width": "x", "height": "z"}})
    with pytest.raises(ValueError, match="axis_map"):
        load_job(p)


def test_custom_axis_map_honoured(tmp_path):
    p = write_job_yaml(tmp_path, {
        "job_code": "X1", "dims": DIMS_IN, "complexity": "simple",
        "orientation": "floor", "product_class": "d", "reference_dir": "r",
        "axis_map": {"length": "y", "width": "x", "height": "z"}})
    job = load_job(p)
    assert job.expected_bounds_m() == pytest.approx(
        {"y": 12 * 0.0254, "x": 34 * 0.0254, "z": 5 * 0.0254})


def test_dim_tolerance_overridable(tmp_path):
    p = write_job_yaml(tmp_path, {
        "job_code": "X1", "dims": DIMS_IN, "complexity": "simple",
        "orientation": "floor", "product_class": "d", "reference_dir": "r",
        "dim_tolerance": 0.05})
    job = load_job(p)
    assert job.dim_tolerance_in_job_units() == pytest.approx(0.05)
    assert job.dim_tolerance_m() == pytest.approx(0.05 * 0.0254)


# ── Shared contract (owner amendment 1) ──────────────────────────────────────


def test_required_deliverables_match_client_contract():
    """Regression guard on the single shared file-set definition. §4.1:
    FBX + LP/HP GLB + LP USDZ + the five texture maps. Editing this list
    must be a conscious act."""
    assert [d.suffix for d in REQUIRED_DELIVERABLES] == [
        ".fbx", "_LP.glb", "_HP.glb", "_LP.usdz",
        "_BaseColor.png", "_Normal.png", "_Roughness.png", "_Metallic.png", "_AO.png",
    ]


def test_naming_and_file_sizes_share_one_definition(package, job):
    """Both gates consume contract.REQUIRED_DELIVERABLES: with every required
    file removed, naming names exactly those files, and file_sizes reports
    nothing outside the shared contract (absence is naming's failure)."""
    for name in required_filenames(JOB_CODE):
        (package / name).unlink()
    naming = check_naming(package, job)
    assert not naming.passed
    for name in required_filenames(JOB_CODE):
        assert name in naming.message
    sizes = check_file_sizes(package, job)
    assert sizes.passed  # no over-size file exists; absence is not its concern


# ── The synthetic passing package ─────────────────────────────────────────────


def test_synthetic_package_passes_every_gate(package, job):
    results = run_all_gates(package, job, make_facts(job))
    assert [r.gate for r in results] == [
        "Naming", "N-gons", "Polycount", "Dimensions", "Orientation", "File sizes"]
    for r in results:
        assert r.passed, f"{r.gate} unexpectedly failed: {r.message}"


def test_gate_result_mirrors_panel_layout(package, job):
    d = check_naming(package, job).to_dict()
    assert set(d) == {"gate", "passed", "expected", "received", "message"}


# ── Naming ───────────────────────────────────────────────────────────────────


def test_naming_fails_when_texture_missing(package, job):
    (package / f"{JOB_CODE}_AO.png").unlink()
    r = check_naming(package, job)
    assert not r.passed and f"{JOB_CODE}_AO.png" in r.message


def test_naming_fails_when_fbx_missing(package, job):
    (package / f"{JOB_CODE}.fbx").unlink()
    r = check_naming(package, job)
    assert not r.passed and f"{JOB_CODE}.fbx" in r.message


def test_naming_requires_exact_texture_names(package, job):
    (package / f"{JOB_CODE}_AO.png").rename(package / f"{JOB_CODE}_ao.png")
    r = check_naming(package, job)
    assert not r.passed and f"{JOB_CODE}_AO.png" in r.message


# ── N-gons ───────────────────────────────────────────────────────────────────


def test_ngons_pass_at_zero(job):
    r = check_ngons(job, make_facts(job))
    assert r.passed and "No n-gons detected" in r.message


def test_ngons_fail_on_ngons(job):
    r = check_ngons(job, make_facts(job, ngon_count=3))
    assert not r.passed
    assert r.received == "Count: 3" and "N-gons detected" in r.message


def test_ngons_fail_closed_without_facts(job):
    r = check_ngons(job, None)
    assert not r.passed and "could not verify" in r.message


# ── Polycount ────────────────────────────────────────────────────────────────


def test_polycount_within_simple_ceiling(job):
    r = check_polycount(job, make_facts(job, triangle_equivalent=50_000))
    assert r.passed and "50,000" in r.message


def test_polycount_over_simple_ceiling(job):
    r = check_polycount(job, make_facts(job, triangle_equivalent=50_001))
    assert not r.passed and "50,001" in r.received


def test_polycount_unknown_tier_fails_closed():
    job = make_job(complexity="complex")
    r = check_polycount(job, make_facts(job))
    assert not r.passed
    assert r.received == "no known ceiling"
    assert "ask the client" in r.message


# ── Dimensions ───────────────────────────────────────────────────────────────


def test_dimensions_pass(job):
    r = check_dimensions(job, make_facts(job))
    assert r.passed, r.message
    assert "L→X" in r.received and "W→Y" in r.received and "H→Z" in r.received


def test_dimensions_fail_on_deviation(job):
    r = check_dimensions(job, make_facts(job, width_delta_in=0.02))
    assert not r.passed and "dimension mismatch" in r.message


def test_dimensions_tolerance_boundary(job):
    # exactly ±0.01 in (the client default) passes; a hair beyond fails
    assert check_dimensions(job, make_facts(job, width_delta_in=0.01)).passed
    assert not check_dimensions(job, make_facts(job, width_delta_in=0.011)).passed


def test_dimensions_fail_on_transposed_length_width(job):
    """Owner amendment 2: extents transposed between X and Y fail even though
    the multiset of extents is correct — their validator derives aspect
    ratios from the L/W/H assignment."""
    r = check_dimensions(job, make_facts(job, swap_xy=True))
    assert not r.passed and "dimension mismatch" in r.message


def test_dimensions_honour_custom_axis_map():
    job = make_job(axis_map={"length": "y", "width": "x", "height": "z"})
    # facts carry X=34in (width) / Y=12in (length): correct under this map…
    r = check_dimensions(job, make_facts(job))
    assert r.passed, r.message
    # …and wrong under the default map (X=length, W→Y) — proving the axis
    # map, not a hardcoded convention, drives the check
    assert not check_dimensions(make_job(), make_facts(job)).passed


def test_dimensions_fail_closed_without_facts(job):
    r = check_dimensions(job, None)
    assert not r.passed and "could not verify" in r.message


# ── Orientation ──────────────────────────────────────────────────────────────


def test_orientation_pass_on_ground(job):
    r = check_orientation(job, make_facts(job))
    assert r.passed and "Floor" in r.message


def test_orientation_tabletop_also_ground_plane():
    r = check_orientation(make_job(orientation="tabletop"),
                          make_facts(make_job(orientation="tabletop")))
    assert r.passed


def test_orientation_fail_when_floating(job):
    r = check_orientation(job, make_facts(job, min_z=0.3))
    assert not r.passed and "floats" in r.message


def test_orientation_fail_when_sinking(job):
    r = check_orientation(job, make_facts(job, min_z=-0.01))
    assert not r.passed and "sinks" in r.message


def test_orientation_wall_fails_closed():
    r = check_orientation(make_job(orientation="wall"), None)
    assert not r.passed and "refusing to guess" in r.message


# ── File sizes ───────────────────────────────────────────────────────────────


def test_file_sizes_pass(package, job):
    r = check_file_sizes(package, job)
    assert r.passed


def test_file_sizes_fail_when_fbx_over_cap(package, job):
    (package / f"{JOB_CODE}.fbx").write_bytes(b"\0" * 10_000_001)
    r = check_file_sizes(package, job)
    assert not r.passed and "10.00MB > 10MB" in r.message


def test_file_sizes_cap_boundary_is_decimal(package, job):
    """Caps read as decimal MB (1 MB = 1,000,000 bytes) — the stricter
    interpretation, so a local pass can never overshoot their cap."""
    (package / f"{JOB_CODE}.fbx").write_bytes(b"\0" * 10_000_000)
    assert check_file_sizes(package, job).passed


def test_file_sizes_ignores_missing_files(package, job):
    (package / f"{JOB_CODE}.fbx").unlink()
    assert check_file_sizes(package, job).passed  # absence is naming's failure


# ── MeshFacts mapping ────────────────────────────────────────────────────────


def test_mesh_facts_from_topology_report():
    report = {
        "triangles": 4, "quads": 6, "ngons": 2, "triangle_equivalent": 20,
        "vertices": 10, "faces_total": 12, "loose_vertices": 1, "loose_edges": 2,
        "boundary_edges": 3, "nonmanifold_edges": 4,
        "bounds": {"min": [0.0, 0.0, 0.0], "max": [1.0, 2.0, 3.0]},
        "model_path": "x.fbx",
    }
    f = MeshFacts.from_topology_report(report)
    assert f.ngon_count == 2 and f.triangle_equivalent == 20
    assert f.extent_m("x") == 1.0 and f.extent_m("y") == 2.0 and f.min_z_m() == 0.0
    assert f.nonmanifold_edges == 4 and f.source == "x.fbx"
