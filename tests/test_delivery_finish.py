"""T3 delivery-finishing tests (blender-marked; auto-skip without Blender).

Mechanical evidence for every baked map — the operator is text-only, so
"the file exists" proves nothing. Each test builds an asset whose CORRECT
bake is analytically predictable and asserts the prediction:

- Normal map: a ramp LP (flat plane, UVs u=x+0.5, v=y+0.5) under a bent HP
  (z = 0.6·y² for y>0). The HP normal tilts toward -Y; the OpenGL convention
  (glTF bitangent +V) therefore requires G < 0.5 on the ramp — pinned to the
  analytic value within 2 LSB. This is the green-channel direction proof and
  regression-guards the bake cage (without the cage, Blender casts rays
  INWARD — bake.cc negate_v3 — and everything above the LP bakes neutral).
- AO map: a plane with a box standing on it; texels under the box (cavity)
  must be darker than exposed texels — contact shadows are real, not flat.
- UV atlas: zero overlapping islands, everything inside 0-1, texel-density
  variance within a stated bound.
- Decimation: under budget or fail loud; over-budget scenes left untouched.
- FBX from the LIVE QUAD-CLEAN SCENE (owner decision): polygon sizes all 4.
- DetailSpec displacement: quilt displacement reaches the normal map while
  the LP bounds stay EXACT (detail must never move a dimension).
- Full chain: the golden coffee-table benchmark through `package --spec`
  (finish_delivery) — all six gates green, no placeholders, review renders.
"""

import json
from pathlib import Path

import pytest
import yaml
from PIL import Image

pytestmark = pytest.mark.blender

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHIRAL_SPEC = PROJECT_ROOT / "input" / "fixtures" / "chiral_test.spec.json"


def _get_runner():
    from src.blender.locate import locate_blender
    from src.blender.runner import BlenderRunner

    if locate_blender() is None:
        pytest.skip("Blender 3.3+ not found on this machine")
    return BlenderRunner()


@pytest.fixture(scope="module")
def runner():
    return _get_runner()


def _png_pixel(path: Path, u: float, v: float) -> tuple[int, int, int]:
    """Sample a baked PNG at UV (u, v). PNG rows are top-first, UV v=0 is
    the bottom — proven against the analytic ramp prediction (1 LSB)."""
    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size
        x = min(int(u * w), w - 1)
        y = h - 1 - min(int(v * h), h - 1)
        return img.getpixel((x, y))


# ── fixtures: ramp LP (.blend) and AO cavity LP (.blend) ────────────────────


@pytest.fixture(scope="module")
def ramp_blend(runner, tmp_path_factory) -> Path:
    """Flat 1×1 grid plane, explicit UVs u = x+0.5, v = y+0.5 (so +V is +Y
    world). +Z is up; the plane has no thickness."""
    tmp = tmp_path_factory.mktemp("ramp")
    blend = tmp / "ramp.blend"
    code = f"""
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_grid_add(x_subdivisions=33, y_subdivisions=33, size=1.0, location=(0, 0, 0))
obj = bpy.context.active_object
obj.name = "ramp"
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


# The HP: same plane bent upward for y>0 (z += 0.15*(y/0.5)^2 = 0.6·y²).
# Surface normal on the ramp: (0, -s, 1)/√(1+s²) with s = dz/dy = 1.2·y —
# tilts toward -Y exactly like a bevel edge facing +Y.
_RAMP_HP_SCRIPT = """
import bpy
src = bpy.data.objects["ramp"]
bpy.ops.object.select_all(action="DESELECT")
src.select_set(True)
bpy.context.view_layer.objects.active = src
bpy.ops.object.duplicate()
hp = bpy.context.active_object
hp.name = "ramp__HP"
for v in hp.data.vertices:
    if v.co.y > 0:
        v.co.z += 0.15 * (v.co.y / 0.5) ** 2
bpy.ops.object.shade_smooth()
RESULT = {"hp": "ramp__HP"}
"""


@pytest.fixture(scope="module")
def ramp_normal_png(runner, ramp_blend, tmp_path_factory) -> Path:
    out_dir = tmp_path_factory.mktemp("ramp_maps")
    result = runner.execute_op("bake_maps", {
        "input": str(ramp_blend),
        "out_dir": str(out_dir),
        "maps": ["normal"],
        "hp_mode": "script",
        "hp_script": _RAMP_HP_SCRIPT,
        "resolution": 512,
        "samples": 8,
        "ray_distance_factor": 0.5,
    })
    assert result["success"], result.get("error")
    return out_dir / "normal.png"


@pytest.fixture(scope="module")
def ao_cavity_blend(runner, tmp_path_factory) -> Path:
    """Flat plane (explicit UVs, same convention as the ramp) with a
    0.2×0.2×0.06 box standing on it at (+0.25, +0.25). Texels under the box
    are cavities; texels at (-0.4, -0.4) are exposed."""
    tmp = tmp_path_factory.mktemp("aocav")
    blend = tmp / "aocav.blend"
    code = f"""
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_grid_add(x_subdivisions=33, y_subdivisions=33, size=1.0, location=(0, 0, 0))
obj = bpy.context.active_object
obj.name = "floor_plane"
me = obj.data
uv = me.uv_layers.new(name="UVMap")
me.uv_layers.active = uv
for loop in me.loops:
    v = me.vertices[loop.vertex_index]
    uv.data[loop.index].uv = (v.co.x + 0.5, v.co.y + 0.5)
bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.25, 0.25, 0.03))
box = bpy.context.active_object
box.name = "blocker"
box.scale = (0.2, 0.2, 0.06)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
bpy.ops.wm.save_as_mainfile(filepath=r"{blend}")
RESULT = {{"blend": r"{blend}"}}
"""
    result = runner.execute_op("run_script", {"code": code})
    assert result["success"], result.get("error")
    return blend


@pytest.fixture(scope="module")
def ao_cavity_png(runner, ao_cavity_blend, tmp_path_factory) -> Path:
    out_dir = tmp_path_factory.mktemp("aocav_maps")
    result = runner.execute_op("bake_maps", {
        "input": str(ao_cavity_blend),
        "out_dir": str(out_dir),
        "maps": ["ao"],
        "resolution": 512,
        "samples": 16,
    })
    assert result["success"], result.get("error")
    return out_dir / "ao.png"


@pytest.fixture(scope="module")
def chiral_blend(runner, tmp_path_factory) -> Path:
    """The permanent chiral axis fixture, prepared through the real delivery
    path (build → quad-verify → UV atlas → .blend)."""
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import ObjectSpec

    tmp = tmp_path_factory.mktemp("chiral")
    blend = tmp / "scene.blend"
    spec = ObjectSpec.model_validate_json(CHIRAL_SPEC.read_text(encoding="utf-8"))
    result = runner.execute_op("prepare_delivery_scene", {
        "build": resolve_spec_to_build_params(spec),
        "out_blend": str(blend),
    })
    assert result["success"], result.get("error")
    return blend


# ── Normal map: OpenGL green direction, analytically pinned ─────────────────


def test_ramp_normal_green_is_opengl(ramp_normal_png):
    """THE green-channel proof. With UV +V along world +Y, a normal tilting
    toward -Y must encode G < 0.5 under the OpenGL convention (glTF bitangent
    +V) — i.e. Blender's default normal_g="POS_Y". Asserted against the
    analytic prediction within 2 LSB at three ramp heights, plus neutral
    below the bend and R pinned at 0.5 (no X tilt anywhere)."""
    for wy in (0.15, 0.30, 0.45):
        r, g, b = _png_pixel(ramp_normal_png, 0.5, wy + 0.5)
        slope = 1.2 * wy                       # dz/dy of z = 0.6·y²
        g_pred = 0.5 - 0.5 * (slope / (1 + slope * slope) ** 0.5)
        assert g == pytest.approx(round(g_pred * 255), abs=2), \
            f"wy={wy}: G {g} vs predicted {round(g_pred * 255)} (OpenGL)"
        assert r == pytest.approx(128, abs=2), f"wy={wy}: R {r} tilted in X?"
        assert b > 200, f"wy={wy}: B {b} — normal not roughly +Z?"

    # Below the bend the surface is flat: neutral (128, 128, 255).
    r, g, b = _png_pixel(ramp_normal_png, 0.5, 0.2)   # wy = -0.3
    assert (r, g, b) == pytest.approx((128, 128, 255), abs=2)


def test_ramp_normal_stats_are_real(ramp_normal_png):
    """Distribution-level evidence: the map is not a flat fill (std > 0.02)
    and is dominated by +Z in tangent space (blue-dominant > 0.9)."""
    import numpy as np

    with Image.open(ramp_normal_png) as img:
        arr = np.asarray(img.convert("RGB"), dtype=float) / 255.0
    assert arr.std(axis=(0, 1))[1] > 0.02, "green channel flat — detail lost"
    blue_dominant = float((arr[:, :, 2] > arr[:, :, 0]).mean())
    assert blue_dominant > 0.9, "normal not +Z-dominant in tangent space"


# ── AO map: cavities darker than exposed faces ──────────────────────────────


def test_ao_cavity_darker_than_exposed(ao_cavity_png):
    """Analytically predictable AO: the plane texel under the standing box is
    a cavity (hemisphere fully blocked) and must bake darker than an exposed
    texel far from it. Flat-fill or inverted AO fails this."""
    under_r, under_g, under_b = _png_pixel(ao_cavity_png, 0.75, 0.75)  # (0.25, 0.25)
    far_r, far_g, far_b = _png_pixel(ao_cavity_png, 0.10, 0.10)       # (-0.4, -0.4)
    assert under_r < 0.3 * 255, f"cavity texel not dark: {under_r}"
    assert far_r > 0.6 * 255, f"exposed texel not bright: {far_r}"
    assert under_r < far_r - 0.4 * 255, "no cavity/exposed contrast in AO"


def test_ao_range_spans(ao_cavity_png):
    """The AO map's range must actually span dark-to-bright (min < 0.2,
    max > 0.8) — a mid-grey fill or clipped bake fails."""
    import numpy as np

    with Image.open(ao_cavity_png) as img:
        arr = np.asarray(img.convert("RGB"), dtype=float) / 255.0
    lum = arr[:, :, 0]
    assert lum.min() < 0.2 and lum.max() > 0.8


# ── UV atlas: islands, bounds, overlaps, texel density ──────────────────────


def test_chiral_uv_diagnostics(runner):
    """The atlas must satisfy the owner's UV contract: zero overlapping
    islands, every island inside 0-1, and texel-density variance within 5%
    (uniform density is the point of the area-proportional pack)."""
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import ObjectSpec

    spec = ObjectSpec.model_validate_json(CHIRAL_SPEC.read_text(encoding="utf-8"))
    result = runner.execute_op("prepare_delivery_scene", {
        "build": resolve_spec_to_build_params(spec),
    })
    assert result["success"], result.get("error")
    uv = result["uv"]
    # one island per box SIDE (2 coplanar quads merge: 0° < the 66° smart
    # project angle limit). This is the _uv_face_groups regression pin: the
    # old loop-matching bug compared one corner per face across a shared
    # edge — consistent winding puts those corners at opposite ends, they
    # can never match, and every face became its own island (12 here,
    # 2118 on the mattress — margin-dominated packing, ~1/3 texel density).
    assert uv["islands_total"] == 6, "expected one island per box side"
    assert uv["in_bounds"] is True
    assert uv["overlapping_island_pairs"] == 0
    assert uv["texel_density_texels_per_m"]["ratio"] < 1.05
    # quad-clean by construction; n-gons must be zero (never triangulated here)
    topo = result["topology"]
    assert topo["ngons"] == 0 and topo["quads"] == 12


# ── Decimation ───────────────────────────────────────────────────────────────


def test_decimate_respects_budget(runner, chiral_blend, tmp_path):
    loose = tmp_path / "lp_loose.glb"
    result = runner.execute_op("decimate_to_budget", {
        "input": str(chiral_blend), "output": str(loose), "budget": 50_000})
    assert result["success"], result.get("error")
    assert result["decimated"] is False
    assert result["triangle_equivalent"] == 24

    tight = tmp_path / "lp_tight.glb"
    result = runner.execute_op("decimate_to_budget", {
        "input": str(chiral_blend), "output": str(tight), "budget": 20})
    assert result["success"], result.get("error")
    assert result["decimated"] is True
    assert result["triangle_equivalent"] <= 20


# ── FBX from the LIVE QUAD-CLEAN SCENE (owner decision) ─────────────────────


def test_fbx_from_live_scene_preserves_quads(runner, chiral_blend, tmp_path):
    """Owner decision at T2 review: the deliverable FBX exports from the live
    quad-clean scene, NOT the triangulated GLB. The independent binary-FBX
    parse must see polygon sizes == {4: 12} — pure quads, zero triangles."""
    from src.client.fbx_inspect import read_fbx_info

    fbx = tmp_path / "chiral.fbx"
    result = runner.execute_op("export_fbx", {
        "input": str(chiral_blend), "path": str(fbx),
        "axis_up": "Y", "axis_forward": "-Z"})
    assert result["success"], result.get("error")

    info = read_fbx_info(fbx)
    sizes: dict[int, int] = {}
    for geom in info.geometries:
        for size in geom.polygon_sizes:
            sizes[size] = sizes.get(size, 0) + 1
    assert sizes == {4: 12}, f"FBX not quad-clean: {sizes}"
    # extents independently parsed: 20 × 12 × 11 in
    ext = info.world_extents_m()
    assert ext[0] == pytest.approx(0.508, abs=1e-3)
    assert ext[1] == pytest.approx(0.3048, abs=1e-3)
    assert ext[2] == pytest.approx(0.2794, abs=1e-3)


# ── DetailSpec: displacement bakes into the normal map, LP stays exact ──────


def test_detail_displacement_bakes_into_normal(runner, tmp_path):
    """A grid_diamond displacement (quilt-style puff) must show up as normal-
    map variation, while the LOW-poly bounds stay exactly on spec — detail
    shapes the HP only and can never move a dimension."""
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.schema import ObjectSpec

    spec = ObjectSpec.model_validate({
        "name": "quilt panel",
        "parts": [{
            "name": "panel",
            "shape": "box",
            "dimensions": [0.5, 0.5, 0.1],
            "position": [0, 0, 0.05],
            "detail": {
                "subdivision_levels": 3,
                "displacement": {"pattern": "grid_diamond", "amplitude": 0.01,
                                  "frequency": 5, "restrict": "up"},
            },
        }],
    })
    build = resolve_spec_to_build_params(spec)

    blend = tmp_path / "panel.blend"
    prep = runner.execute_op("prepare_delivery_scene", {
        "build": build, "out_blend": str(blend)})
    assert prep["success"], prep.get("error")
    bounds = prep["overall_bounds"]["dimensions"]
    assert bounds[0] == pytest.approx(0.5, abs=1e-4)
    assert bounds[1] == pytest.approx(0.5, abs=1e-4)
    assert bounds[2] == pytest.approx(0.1, abs=1e-4)

    detail_map = {p["name"]: p["detail"] for p in build["spec"]["parts"] if p.get("detail")}
    bake = runner.execute_op("bake_maps", {
        "input": str(blend), "out_dir": str(tmp_path / "maps"),
        "maps": ["normal"], "resolution": 512,
        "detail": detail_map, "hp_glb": str(tmp_path / "hp.glb"),
        "save_blend": str(blend)})
    assert bake["success"], bake.get("error")
    stats = bake["maps"]["normal"]["stats"]
    assert stats["std"][0] > 0.02 and stats["std"][1] > 0.02, \
        f"displacement did not reach the normal map: std={stats['std']}"
    assert stats["frac_blue_dominant"] > 0.9


# ── Full chain: golden coffee-table benchmark through `package --spec` ──────


def test_finish_delivery_coffee_table_end_to_end(runner, tmp_path):
    """T3 exit criterion: the golden coffee-table benchmark goes through the
    full finish chain (build → atlas → bake → decimate → live-quad FBX) via
    `package --spec`: all six gates green, NO placeholders, mechanical bake
    evidence + review renders in qa_report, and the package on disk
    independently passes `validate`."""
    from typer.testing import CliRunner

    from src.cli import app as cli_app

    job_yaml = tmp_path / "job.yaml"
    job_yaml.write_text(yaml.safe_dump({
        "job_code": "COFFEE0001",
        "dims": {"length": 47.24409, "width": 23.62205, "height": 15.74803,
                  "unit": "in"},
        "complexity": "simple",
        "orientation": "floor",
        "product_class": "coffee_table",
        "part_scope": "golden benchmark finish e2e",
        "reference_dir": "input/benchmarks",
    }), encoding="utf-8")

    out_root = tmp_path / "packages"
    result = CliRunner().invoke(cli_app, [
        "package", "--spec", str(PROJECT_ROOT / "input/benchmarks/coffee_table.spec.json"),
        "--job", str(job_yaml), "--out-root", str(out_root), "--res", "512"])
    assert result.exit_code == 0, result.output
    assert "ALL GATES PASSED" in result.output
    assert "Placeholder Warning" not in result.output

    package_dir = out_root / "COFFEE0001"
    report = json.loads((package_dir / "qa_report.json").read_text(encoding="utf-8"))
    assert report["all_passed"] is True
    assert "placeholders" not in report, "finish chain must not ship placeholders"
    finish = report["finish"]
    assert finish["fbx_source"] == "live_quad_scene"
    assert finish["review_renders"], "review renders must exist for owner review"
    assert finish["uv_diagnostics"]["overlapping_island_pairs"] == 0
    assert finish["uv_diagnostics"]["in_bounds"] is True
    assert finish["uv_diagnostics"]["texel_density_texels_per_m"]["ratio"] < 1.05
    assert report["cross_checks"]["agree"] is True

    # Normal-map mechanical evidence (operator is text-only).
    normal_stats = finish["bake"]["normal"]["stats"]
    assert normal_stats["coverage"] == 1.0
    assert normal_stats["frac_blue_dominant"] > 0.9
    assert normal_stats["std"][2] > 0.01, "normal map is a flat fill — no detail"

    # LP/HP split is real (not byte-identical).
    lp = package_dir / "COFFEE0001_LP.glb"
    hp = package_dir / "COFFEE0001_HP.glb"
    assert lp.stat().st_size != hp.stat().st_size

    # Independent re-validation of the assembled package on disk.
    revalidate = CliRunner().invoke(cli_app, [
        "validate", str(package_dir), "--job", str(job_yaml)])
    assert revalidate.exit_code == 0, revalidate.output
    assert "ALL GATES PASSED" in revalidate.output
