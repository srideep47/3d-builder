"""T4 template layer: TemplateSpec validation, footprint math, and the
compile_spec geometry contract (band stacking, tape inset, decal placement,
quilt cell reference, measurements).

The geometry contract is the load-bearing part: the NOMINAL L/W/H from the
job card must be the model's outer silhouette (client dimension gate,
±0.01 in) — bands are inset by the tape protrusion so tape outer faces land
exactly on nominal, and the decal stays recessed behind the tape plane.
"""

import math
from pathlib import Path

import pytest
import yaml

from src.client.job import JobCard, JobDims, load_job
from src.spec.template import (DecalSpec, TemplateSpec, compile_spec,
                               footprint_outline, load_template)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = PROJECT_ROOT / "templates" / "mattress.yaml"
JOB = PROJECT_ROOT / "input" / "jobs" / "MAYA00053153.yaml"


def _job(l, w, h, code="T999", unit="m"):
    return JobCard(job_code=code, product_class="mattress",
                   dims=JobDims(length=l, width=w, height=h, unit=unit),
                   complexity="simple", orientation="floor",
                   reference_dir=PROJECT_ROOT / "input" / "reference")


@pytest.fixture(scope="module")
def mattress():
    return load_template(TEMPLATE)


@pytest.fixture(scope="module")
def compiled(mattress):
    spec, _warnings = compile_spec(mattress, load_job(JOB))
    return spec


def _part(spec, name):
    return {p.name: p for p in spec.parts}[name]


# ── template model validation ────────────────────────────────────────────────


def test_mattress_template_loads_and_validates(mattress):
    # §5.2 structure (round-4 correction, reviewer's re-read): crown +
    # air-mesh + knit rib + ONE velvet mass (two faint seams inside) +
    # knit rib + base — NO white ribs between velvet bands
    assert mattress.product_class == "mattress"
    assert len(mattress.bands) == 6
    assert [b.name for b in mattress.bands] == [
        "crown", "air_mesh", "knit_top", "velvet", "knit_bottom", "base",
    ]
    assert len(mattress.tape_edges) == 3  # §5.2 bands 2/4/6
    assert mattress.crown is not None and mattress.crown.quilt is not None
    assert mattress.decal is not None


def test_band_fractions_must_sum_to_one():
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    data["bands"][0]["height_fraction"] += 0.05
    with pytest.raises(ValueError, match="sum"):
        TemplateSpec.model_validate(data)


def test_band_names_must_be_unique():
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    data["bands"][1]["name"] = data["bands"][0]["name"]
    with pytest.raises(ValueError, match="[Uu]nique"):
        TemplateSpec.model_validate(data)


def test_tape_boundary_must_be_a_band_name():
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    data["tape_edges"][0]["at_boundary_below"] = "nonexistent_band"
    with pytest.raises(ValueError, match="boundary"):
        TemplateSpec.model_validate(data)


def test_material_refs_must_exist():
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    data["bands"][0]["material"] = "not_a_surface"
    with pytest.raises(ValueError, match="material"):
        TemplateSpec.model_validate(data)


def test_decal_requires_aspect():
    with pytest.raises(Exception):
        DecalSpec.model_validate({"texture": "x", "height_fraction": 0.3})


# ── footprint outline ────────────────────────────────────────────────────────


def test_footprint_outline_shape():
    pts = footprint_outline(0.5, 0.3, 5.0, 48)
    assert len(pts) == 48
    assert pts[0] == [0.5, 0.0]  # starts on +X axis
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    assert abs(max(xs) - 0.5) < 1e-9 and abs(min(xs) + 0.5) < 1e-9
    assert abs(max(ys) - 0.3) < 1e-9 and abs(min(ys) + 0.3) < 1e-9


def test_footprint_superellipse_rounds_corners():
    """Exponent 5 rounds the rectangle corners: the 45-degree point sits
    well inside the corner a plain rectangle would occupy (hypot 1.414)
    but outside an ellipse (1.0)."""
    pts = footprint_outline(1.0, 1.0, 5.0, 360)
    d45 = [math.hypot(x, y) for x, y in pts if abs(x - y) < 0.02]
    assert 1.15 < max(d45) < 1.32


# ── compile: geometry contract ───────────────────────────────────────────────


def test_measurements_target_nominal_dims(compiled):
    job = load_job(JOB)
    ex, ey, ez = job.expected_bounds_m().values()
    by_apply = {m.applies_to: m.target_value for m in compiled.measurements}
    assert by_apply["overall.width_x"] == pytest.approx(ex)
    assert by_apply["overall.depth_y"] == pytest.approx(ey)
    assert by_apply["overall.height_z"] == pytest.approx(ez)


def test_bands_stack_to_full_height(compiled):
    """Band heights chain from z=0 and sum to exactly H (tapes and the decal
    are not height contributors)."""
    job = load_job(JOB)
    ez = job.expected_bounds_m()["z"]
    band_parts = [p for p in compiled.parts
                  if p.name != "decal_patch"
                  and not p.name.startswith("tape_")
                  and not p.name.startswith("handle_")]
    assert len(band_parts) == 6
    assert sum(p.dimensions[2] for p in band_parts) == pytest.approx(ez, abs=1e-6)
    # and they stack: sorted by z-base, bases chain exactly
    ordered = sorted(band_parts, key=lambda p: p.position[2])
    z = 0.0
    for p in ordered:
        assert p.position[2] == pytest.approx(z, abs=1e-9)
        z += p.dimensions[2]
    assert z == pytest.approx(ez, abs=1e-6)


def test_crown_compiles_to_script_dome(compiled):
    crown = _part(compiled, "crown")
    assert crown.method == "custom_script"
    assert crown.code and "__A__" not in crown.code  # tokens substituted
    assert "bpy" in crown.code
    assert crown.position_mode == "base"


def test_crown_quilt_references_cell_size(compiled, mattress):
    """Round-3 rework: the quilt is REAL LP geometry baked into the crown
    script. The puff AMPLITUDE is a fraction of one quilt CELL (footprint /
    cells), not of the mattress height — scale-invariant puff depth — and
    the profile is lowered by exactly that amplitude so peaks land on the
    nominal band top (bounds contract). Cross-cell count follows the
    footprint aspect (whole cells, square in metres). The HP must not
    subsurf-smooth the puffs away: subdivision_levels=0 (bevel-only)."""
    job = load_job(JOB)
    bounds = job.expected_bounds_m()
    ex, ey = bounds["x"], bounds["y"]
    q = mattress.crown.quilt
    p_max = max(t.protrusion_fraction for t in mattress.tape_edges) * min(bounds.values())
    a_body = ex / 2 - p_max
    b_body = ey / 2 - p_max
    amp = q.amplitude_fraction * (2 * a_body / q.cells_across)
    crown = _part(compiled, "crown")
    # bevel-only HP: explicit zero, not the absent-key default
    assert crown.detail is not None
    assert crown.detail.subdivision_levels == 0
    assert crown.detail.displacement is None
    # tokens substituted into the script: amplitude, lowered profile, cells
    assert crown.code and "__AMP__" not in crown.code
    assert f"AMP = {amp!r}" in crown.code
    assert f"H = {crown.dimensions[2] - amp!r}" in crown.code
    assert f"CX = {q.cells_across}" in crown.code
    assert f"CY = {round(q.cells_across * b_body / a_body)}" in crown.code


def test_bands_inset_by_tape_protrusion(compiled, mattress):
    """THE client-gate contract: band walls sit p_max inside nominal so the
    tapes' outer faces land exactly on the nominal L/W silhouette."""
    job = load_job(JOB)
    bounds = job.expected_bounds_m()
    ex, ey = bounds["x"], bounds["y"]
    p_max = max(t.protrusion_fraction for t in mattress.tape_edges) * min(bounds.values())
    body = _part(compiled, "air_mesh")
    assert body.dimensions[0] == pytest.approx(ex - 2 * p_max)
    assert body.dimensions[1] == pytest.approx(ey - 2 * p_max)
    # profile points live on the inset wall
    xs = [p[0] for p in body.profile_points]
    assert max(xs) == pytest.approx(ex / 2 - p_max)


def test_tapes_sweep_flush_on_the_inset_wall(compiled, mattress):
    """§5.2: thin binding tape that WRAPS AND HUGS the perimeter edge — a
    [protrusion x width] section seated flush on the wall (path offset half
    a protrusion along the normals), standing proud by one protrusion.
    This is the DEFECT 1 regression pin: the old [2*protrusion x thickness]
    section of H rode the wall half-buried and rendered as 58 mm collars."""
    job = load_job(JOB)
    bounds = job.expected_bounds_m()
    ex = bounds["x"]
    scale = min(bounds.values())
    p_max = max(t.protrusion_fraction for t in mattress.tape_edges) * scale
    tape = _part(compiled, "tape_1")
    assert tape.shape == "sweep"
    assert tape.path_closed is True
    protrusion = mattress.tape_edges[0].protrusion_fraction * scale
    width = mattress.tape_edges[0].width_fraction * scale
    assert tape.dimensions == pytest.approx([protrusion, width])
    # the path rides half a protrusion off the wall so the section's inner
    # face seats ON it — never buried, never floating
    xs = [p[0] for p in tape.path_points]
    assert max(xs) == pytest.approx(ex / 2 - p_max + protrusion / 2)
    # closed path: no repeated endpoint (the cyclic spline handles the seam)
    assert tape.path_points[0] != tape.path_points[-1]


def test_tape_fractions_are_of_the_cross_section_scale(mattress):
    """Tape size follows min(L, W, H), not H: the same template must give a
    ~13 mm strip on the tall 12x12x65 in placeholder AND a ~11 mm strip on
    a queen — a tape is a detail of the side face, not of the height."""
    for job_dims, expected_width in (((0.3048, 0.3048, 1.651), 0.023 * 0.3048),
                                     ((2.032, 1.524, 0.254), 0.023 * 0.254)):
        spec, _warnings = compile_spec(mattress, _job(*job_dims, code="SC"))
        tape = {p.name: p for p in spec.parts}["tape_1"]
        assert tape.dimensions[1] == pytest.approx(expected_width), job_dims


def test_tape_protrusion_larger_than_footprint_raises(mattress):
    """A template whose tape protrusion swallows the footprint must fail
    loudly at compile time, not produce inverted geometry."""
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    for t in data["tape_edges"]:
        t["protrusion_fraction"] = 0.6  # > half of min(L, W, H) for any job
    tpl = TemplateSpec.model_validate(data)
    job = _job(0.04, 0.04, 2.0)
    with pytest.raises(ValueError, match="protrusion"):
        compile_spec(tpl, job)


def test_decal_sits_on_wall_recessed_behind_tape(compiled, mattress):
    """The label is proud of the band wall (a sewn patch) but recessed
    behind the tape plane — it can never widen the overall silhouette."""
    job = load_job(JOB)
    bounds = job.expected_bounds_m()
    ey = bounds["y"]
    p_max = max(t.protrusion_fraction for t in mattress.tape_edges) * min(bounds.values())
    wall_y = -(ey / 2 - p_max)
    decal = _part(compiled, "decal_patch")
    w, t_patch, h = decal.dimensions
    # box centred at wall - t/2 (front face): inner face ON the wall
    assert decal.position[1] == pytest.approx(wall_y - t_patch / 2)
    # outer face stays inside nominal
    assert decal.position[1] - t_patch / 2 > -(ey / 2) + 1e-12
    assert t_patch < p_max
    # portrait patch per §5.3
    assert h > w


def test_decal_clears_tapes_or_warns(mattress):
    """A decal overlapping a tape's z-span must produce a compile warning
    (they would interpenetrate)."""
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    data["decal"]["center_height_fraction"] = 0.10  # right on tape_3
    tpl = TemplateSpec.model_validate(data)
    _spec, warnings = compile_spec(tpl, _job(1.5, 1.5, 0.3))
    assert any("tape_" in w for w in warnings), warnings


def test_decal_width_clamp_warns_on_pathological_dims(mattress):
    """When job dims make the aspect-driven patch wider than a quarter of
    the wall, it is clamped AND surfaced as a warning — never silent."""
    _spec, warnings = compile_spec(mattress, _job(0.3, 0.3, 1.6))
    assert any("clamped" in w for w in warnings), warnings


def test_decal_falls_back_to_flat_black_without_texture(mattress):
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    data["decal"]["texture"] = "input/decals/DOES_NOT_EXIST"
    tpl = TemplateSpec.model_validate(data)
    spec, warnings = compile_spec(tpl, _job(1.5, 1.5, 0.3))
    assert any("albedo.png" in w for w in warnings), warnings
    assert _part(spec, "decal_patch").material.texture_dir is None


def test_no_warnings_on_sane_dims(mattress):
    """Queen-proportioned dims: no clamp, no tape overlap, no corner
    warning — the refusal machinery lives downstream, not here."""
    _spec, warnings = compile_spec(
        mattress, _job(2.032, 1.524, 0.254, code="QUEEN"))
    assert warnings == [], warnings


def test_tri_budget_flows_into_spec(compiled, mattress):
    assert compiled.tri_budget == mattress.tri_budget


# ── round 4: seam rings + review close-ups ──────────────────────────────────


def test_velvet_seams_compile_to_metre_ring_spec(compiled, mattress):
    """Round-4 band correction: the velvet border is ONE part carrying two
    faint pressed seam rings (stitch lines, not colour changes). The
    template's band-relative fractions must land as metre z/depth on the
    part, derived from the band height and min(L, W, H)."""
    job = load_job(JOB)
    ez = job.expected_bounds_m()["z"]
    scale = min(job.expected_bounds_m().values())
    velvet = _part(compiled, "velvet")
    band = next(b for b in mattress.bands if b.name == "velvet")
    h = band.height_fraction * ez
    assert velvet.seam_rings is not None and len(velvet.seam_rings) == 2
    for seam, spec_ring in zip(band.seams, velvet.seam_rings):
        assert spec_ring.z == pytest.approx(seam.height_fraction * h)
        assert spec_ring.depth == pytest.approx(seam.depth_fraction * scale)
    # the one-mass contract: no other band carries seams
    others = [p for p in compiled.parts if p.seam_rings and p.name != "velvet"]
    assert others == []


def test_review_closeups_flow_into_spec(mattress):
    """Round 4: the label/border close-ups are template product knowledge,
    threaded verbatim into the ObjectSpec for the render op — the finishing
    layer never decides WHAT to frame."""
    spec, _warnings = compile_spec(mattress, _job(2.032, 1.524, 0.254, code="Q4"))
    assert spec.review_closeups is not None
    by_name = {c.name: c for c in spec.review_closeups}
    assert set(by_name) == {"label", "border"}
    assert by_name["label"].part == "decal_patch"
    assert by_name["label"].frame == "part"
    assert by_name["border"].frame == "model_height"
    assert by_name["border"].direction == "front"


def test_closeup_part_must_exist_in_template():
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    data["review_closeups"] = [{"name": "x", "part": "not_a_part"}]
    with pytest.raises(ValueError, match="not a part"):
        TemplateSpec.model_validate(data)


def test_decal_is_centred_on_the_velvet_mass(compiled, mattress):
    """Round 4: with the single velvet mass at z 0.21..0.48 of H, the label
    is re-centred on it (0.345 of H) — the patch overhangs the ribs like a
    sewn patch, and still clears both tapes it spans between."""
    job = load_job(JOB)
    ez = job.expected_bounds_m()["z"]
    decal = _part(compiled, "decal_patch")
    assert decal.position[2] == pytest.approx(0.345 * ez)
