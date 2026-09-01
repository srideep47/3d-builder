"""T4 harness geometry contracts (blender-marked; auto-skip without Blender).

Pins the empirical findings from the T4 build verification as regression
tests:

- the compiled mattress builds into a LIVE QUAD-CLEAN scene (a .blend via
  export_any; a GLB round trip would triangulate and make the n-gon gate
  vacuous);
- every part is a real closed mesh: 0 n-gons, 0 boundary/non-manifold
  edges (the tapes were glTF EMPTY nodes before the bevel_mode='OBJECT'
  fix — "the object exists" proves nothing, faces do);
- the GEOMETRY CONTRACT: band walls are inset by the tape protrusion so
  every tape's outer face lands EXACTLY on the nominal L/W silhouette and
  the overall bounds equal the job card (client dimension gate ±0.01 in);
- the decal sits proud of the band wall but recessed behind the tape
  plane — it can never widen the silhouette;
- triplanar label orientation: with the Mapping Location offset
  (0.5, 0.5, 0.5) a one-tile-across patch renders UPRIGHT and unmirrored
  (normalized-cross-correlation probe against the authored pattern).
"""

import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

pytestmark = pytest.mark.blender

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = PROJECT_ROOT / "templates" / "mattress.yaml"
JOB = PROJECT_ROOT / "input" / "jobs" / "MAYA00053153.yaml"

# the placeholder job card: 12 x 12 x 65 IN stand-ins
NOM = {"x": 0.3048, "y": 0.3048, "z": 1.651}
EZ = NOM["z"]
SCALE = min(NOM.values())  # tape fractions are of the cross-section scale
P_MAX = 0.023 * SCALE  # tape cord radius*2 (round-3: rounded cord, 2-3% of H)
A_BODY = NOM["x"] / 2 - P_MAX  # inset band wall half-width


@pytest.fixture(scope="module")
def runner():
    from src.blender.locate import locate_blender
    from src.blender.runner import BlenderRunner

    if locate_blender() is None:
        pytest.skip("Blender 3.3+ not found on this machine")
    return BlenderRunner()


@pytest.fixture(scope="module")
def quad_scene(runner, tmp_path_factory):
    """The compiled mattress built into a live .blend — same build path the
    delivery pipeline uses, quads preserved."""
    from src.client.job import load_job
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.template import compile_spec, load_template

    spec, _warnings = compile_spec(load_template(TEMPLATE), load_job(JOB))
    blend = tmp_path_factory.mktemp("t4h") / "mattress.blend"
    res = runner.execute_op(
        "build_from_spec",
        resolve_spec_to_build_params(spec, output_glb_path=str(blend)))
    assert res["success"], res.get("error")
    assert res["warnings"] == []
    assert res["parts_created"] == 18
    return blend


@pytest.fixture(scope="module")
def objects(runner, quad_scene):
    """Per-object world bounds + topology, read Blender-side (Z-up, fresh
    view_layer — the glTF Y-up convention never enters this analysis)."""
    code = r"""
import bpy
from mathutils import Vector
bpy.context.view_layer.update()
out = {}
for o in bpy.data.objects:
    if o.type != "MESH":
        continue
    corners = [o.matrix_world @ Vector(c) for c in o.bound_box]
    xs = sorted(c.x for c in corners)
    ys = sorted(c.y for c in corners)
    zs = sorted(c.z for c in corners)
    me = o.data
    ngon = sum(1 for p in me.polygons if len(p.vertices) > 4)
    quad = sum(1 for p in me.polygons if len(p.vertices) == 4)
    tri = sum(1 for p in me.polygons if len(p.vertices) == 3)
    use = {}
    for p in me.polygons:
        for e in p.edge_keys:
            use[e] = use.get(e, 0) + 1
    out[o.name] = {
        "x": [xs[0], xs[-1]], "y": [ys[0], ys[-1]], "z": [zs[0], zs[-1]],
        "ngon": ngon, "quad": quad, "tri": tri,
        "boundary_edges": sum(1 for n in use.values() if n == 1),
        "nonmanifold_edges": sum(1 for n in use.values() if n > 2),
    }
RESULT = out
"""
    res = runner.execute_op("run_script",
                            {"code": code, "input": str(quad_scene)})
    assert res["success"], res.get("error")
    return res["result"]


# ── every part is a real, closed, quad/tri-only mesh ─────────────────────────


def test_all_real_mesh_parts(objects):
    assert len(objects) == 18  # 10 bands + 3 tapes + 4 handles + decal (round 3)
    for name, o in objects.items():
        assert o["quad"] + o["tri"] > 0, name


def test_strict_ngon_gate_passes_by_construction(objects):
    for name, o in objects.items():
        assert o["ngon"] == 0, (name, o["ngon"])


def test_every_part_is_an_independently_closed_solid(objects):
    """Each part closes on itself (edge shared by exactly 2 faces) — the
    merged mesh is watertight in the edge-degree sense without any part
    having to mate with another."""
    for name, o in objects.items():
        assert o["boundary_edges"] == 0, (name, o["boundary_edges"])
        assert o["nonmanifold_edges"] == 0, (name, o["nonmanifold_edges"])


def test_dome_is_quads_plus_pole_fans(objects):
    """The crown script part (round-3 quilt rework): a Cartesian grid cap —
    (lines_x-1) x (lines_y-1) quads with stitch valleys exactly on grid
    lines — plus one triangle fan closing the flat bottom. On this square
    placeholder job both axes derive 17 cells -> 85 grid lines each."""
    crown = objects["crown"]
    assert crown["quad"] == 84 * 84  # grid quads (17 cells x 4 divisions)
    assert crown["tri"] == 2 * 84 + 2 * 84  # boundary-loop cap fan


# ── the geometry contract: nominal silhouette ────────────────────────────────


def test_band_walls_are_inset_by_the_tape_protrusion(objects):
    body = objects["air_mesh"]
    assert body["x"][1] - body["x"][0] == pytest.approx(2 * A_BODY, abs=2e-4)
    assert body["y"][1] - body["y"][0] == pytest.approx(2 * A_BODY, abs=2e-4)
    assert body["z"][1] - body["z"][0] == pytest.approx(0.162 * EZ, abs=2e-4)
    # centred on the footprint
    assert body["x"][0] == pytest.approx(-A_BODY, abs=2e-4)


def test_tape_outer_faces_land_exactly_on_nominal(objects):
    for tape in ("tape_1", "tape_2", "tape_3"):
        t = objects[tape]
        assert t["x"][1] == pytest.approx(NOM["x"] / 2, abs=2e-4), tape
        assert t["x"][0] == pytest.approx(-NOM["x"] / 2, abs=2e-4), tape
        assert t["y"][0] == pytest.approx(-NOM["y"] / 2, abs=2e-4), tape
        assert t["y"][1] == pytest.approx(NOM["y"] / 2, abs=2e-4), tape
        assert t["z"][1] - t["z"][0] == pytest.approx(0.023 * SCALE, abs=2e-4)  # cord diameter


def test_tape_section_is_a_round_cord_flush_on_the_wall(runner, quad_scene):
    """§5.2 DEFECT 1 regression pin (round-3 cord form): binding tape HUGS
    the wall — a round cord whose INNER TANGENT sits on the band wall and
    whose OUTER TANGENT lands exactly on the nominal silhouette (one
    protrusion of stand-off). Measured on tape_1's +X-flat ring (path point
    0 sits at y = 0, x > 0, so the whole section ring is on that flat).
    The old [2*protrusion x thickness] half-buried section of H rendered as
    58 mm collars; the flat-strip form read too thin vs the reference
    photos (round-3 correction: a rounded cord, 2-3% of H)."""
    code = r"""
import bpy
from mathutils import Vector
tape = bpy.data.objects["tape_1"]
xs, zs = [], []
for v in tape.data.vertices:
    w = tape.matrix_world @ v.co
    if abs(w.y) < 1e-6 and w.x > 0:  # the +X flat only (y=0 also hits -X)
        xs.append(w.x)
        zs.append(w.z)
RESULT = {"xs": sorted(xs), "z0": min(zs), "z1": max(zs), "n": len(zs)}
"""
    res = runner.execute_op("run_script", {"code": code, "input": str(quad_scene)})
    assert res["success"], res.get("error")
    sec = res["result"]
    assert sec["n"] == 12  # one 12-gon section ring at the +X flat
    assert min(sec["xs"]) == pytest.approx(A_BODY, abs=1e-5)  # inner tangent on wall
    assert max(sec["xs"]) == pytest.approx(A_BODY + P_MAX, abs=1e-5)  # outer on nominal
    assert sec["z1"] - sec["z0"] == pytest.approx(P_MAX, abs=1e-5)  # cord diameter
    # centred on the crown/air-mesh boundary
    assert (sec["z0"] + sec["z1"]) / 2 == pytest.approx(0.72 * EZ, abs=1e-5)


@pytest.fixture(scope="module")
def prepared_scene(runner):
    """The compiled mattress through prepare_delivery_scene (build + UV
    atlas + diagnostics) — the UV-side evidence for the defect-2 fix."""
    from src.client.job import load_job
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.template import compile_spec, load_template

    spec, _warnings = compile_spec(load_template(TEMPLATE), load_job(JOB))
    res = runner.execute_op(
        "prepare_delivery_scene",
        {"build": resolve_spec_to_build_params(spec)})
    assert res["success"], res.get("error")
    return res


def test_uv_contiguous_faces_merge_into_few_islands(prepared_scene):
    """DEFECT 2 regression pin (UV half): _uv_face_groups must merge
    UV-contiguous faces across shared edges. The old loop-matching bug
    compared ONE corner per face across a shared edge — consistent winding
    puts those two corners at opposite ends of the edge, so they can never
    match — and every face became a one-face island: margin-dominated
    packing (~1/3 target texel density), then bake-margin bleed across the
    wall islands (the black-and-white blotch defect). With vertex-keyed
    matching the mattress collapses to ~150 islands. (Counts are round-3
    values: the crown became a real quilted grid cap, the border gained a
    white band, and four carry-handle boxes were added.)"""
    uv = prepared_scene["uv"]
    topo = prepared_scene["topology"]
    assert topo["faces_total"] == 10446  # round-3: quilted crown + 10 bands
    # + handles + 576-face round-cord tapes (12-gon ring x 48 segments)
    # island count shifts a little with quilt softness (smart-project groups
    # by face-normal adjacency — exponent/amplitude tuning moves a few
    # faces across the 66-degree limit) and with feature count (each handle
    # box adds 6); the regression this pins is the loop-matching bug, which
    # produced ONE ISLAND PER FACE (~9000)
    assert 120 <= uv["islands_total"] <= 170
    assert uv["overlapping_island_pairs"] == 0
    assert uv["in_bounds"] is True
    assert uv["texel_density_texels_per_m"]["ratio"] < 1.05
    # round-3: the round-cord tapes carry ~3x the strip surface into the
    # atlas, so the pack scale dropped from 0.75 (flat strips)
    assert prepared_scene["uv_atlas"]["pack_scale"] == pytest.approx(0.5625, abs=0.01)


def test_overall_bounds_exactly_nominal(objects):
    """THE client dimension gate: the union of all parts spans exactly the
    job card's nominal L x W x H, bottom on the ground plane."""
    xs = [v for o in objects.values() for v in o["x"]]
    ys = [v for o in objects.values() for v in o["y"]]
    zs = [v for o in objects.values() for v in o["z"]]
    assert max(xs) == pytest.approx(NOM["x"] / 2, abs=2e-4)
    assert min(xs) == pytest.approx(-NOM["x"] / 2, abs=2e-4)
    assert max(ys) == pytest.approx(NOM["y"] / 2, abs=2e-4)
    assert min(ys) == pytest.approx(-NOM["y"] / 2, abs=2e-4)
    assert min(zs) == pytest.approx(0.0, abs=2e-4)  # ground contact
    assert max(zs) == pytest.approx(NOM["z"], abs=2e-4)


def test_decal_recessed_behind_the_tape_plane(objects):
    """Proud of the band wall (a sewn patch), fully behind the tape plane,
    portrait, front face."""
    d = objects["decal_patch"]
    wall = -A_BODY
    assert d["y"][1] == pytest.approx(wall, abs=2e-4)       # on the wall
    assert d["y"][0] > -NOM["y"] / 2                         # inside nominal
    assert d["y"][1] - d["y"][0] == pytest.approx(0.3 * P_MAX, abs=2e-4)
    h = d["z"][1] - d["z"][0]
    w = d["x"][1] - d["x"][0]
    assert h == pytest.approx(0.34 * EZ, abs=2e-4)  # round-3: ~74% of the
    # tape-to-tape border stack — taller and narrower than the first pass
    assert h > w  # portrait per §5.3


def test_carry_handles_cross_the_full_stack_inside_nominal(objects):
    """Round-3 correction (photo 9.28.35): the carry handles exist — two
    vertical straps per long side at the quarter points. Contract: the
    strap z-span is the FULL border stack (bottom tape line to top tape
    line), the outer face stays behind the tape plane (never widens the
    nominal silhouette, never z-fights the tapes it crosses), and the
    inner face is buried past the curved wall (no floating gap)."""
    handles = {n: o for n, o in objects.items() if n.startswith("handle_")}
    assert len(handles) == 4  # 2 per long side, front and back
    z0 = 0.10 * EZ   # white_band bottom  (bottom tape line)
    z1 = 0.72 * EZ   # crown bottom       (top tape line)
    for name, hd in handles.items():
        assert hd["z"][0] == pytest.approx(z0, abs=2e-4), name
        assert hd["z"][1] == pytest.approx(z1, abs=2e-4), name
        # behind the tape plane, proud of the wall, buried past the wall —
        # sign-independent (front handles sit at -y, back handles at +y)
        y_out = max(abs(hd["y"][0]), abs(hd["y"][1]))
        y_in = min(abs(hd["y"][0]), abs(hd["y"][1]))
        assert y_out < NOM["y"] / 2, name   # never widens the silhouette
        assert y_out > A_BODY, name         # visibly raised off the wall
        assert y_in < A_BODY, name          # no floating gap under the strap
        assert (hd["x"][1] - hd["x"][0]) == pytest.approx(0.08 * SCALE, abs=2e-4)
    # quarter points of the length, mirrored front/back
    xcs = sorted({round((hd["x"][0] + hd["x"][1]) / 2.0, 4) for hd in handles.values()})
    assert xcs == pytest.approx([-NOM["x"] / 4, NOM["x"] / 4])
    fronts = [hd for n, hd in handles.items() if "front" in n]
    backs = [hd for n, hd in handles.items() if "back" in n]
    assert len(fronts) == 2 and len(backs) == 2
    for f, b in zip(sorted(fronts, key=lambda o: o["x"][0]),
                    sorted(backs, key=lambda o: o["x"][0])):
        assert f["x"][0] == pytest.approx(b["x"][0])


# ── triplanar label orientation (the +0.5 Mapping offset contract) ───────────


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float64) - a.mean()
    b = b.astype(np.float64) - b.mean()
    denom = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
    return float((a * b).sum() / denom) if denom > 0 else 0.0


def test_triplanar_label_renders_upright_and_unmirrored(runner, tmp_path):
    """A thin box with a one-tile-across triplanar albedo, viewed head-on,
    must show the pattern EXACTLY as authored (identity beats every flip
    by a clear margin). This is the mechanical pin for the decal label
    orientation: without the Mapping node Location offset (0.5, 0.5, 0.5)
    the tile grid anchors at the object origin and one-tile textures wrap,
    showing the label twice mirrored (NCC collapses on all flips)."""
    from src.textures.patterns import save_png

    # strongly asymmetric pattern: bright block top-left, dark block
    # bottom-right, mid-grey diagonal (array rows are bottom-first per the
    # save_png convention; PIL reads top-first like the render)
    res = 128
    pat = np.full((res, res, 3), 0.25, dtype=np.float32)
    pat[res - 50:res - 10, 10:60] = 0.92   # bright block, top-left displayed
    pat[10:50, 68:118] = 0.05              # dark block, bottom-right displayed
    diag = np.linspace(0, res, res, endpoint=False).astype(int)
    pat[diag, diag] = 0.6                  # corner-to-corner diagonal
    tex_dir = tmp_path / "labeltex"
    tex_dir.mkdir()
    save_png(pat, tex_dir / "albedo.png")

    side, thin = 0.15, 0.004
    out_png = tmp_path / "label_render.png"
    code = f"""
import bpy, math, sys
harness = sys.modules["__main__"]

bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0))
obj = bpy.context.active_object
obj.scale = ({side}, {thin}, {side})
# applied: object-space coords == world coords, so the Object tex-coord
# spans [-side/2, side/2] exactly like the real decal part
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
mat_spec = {{
    "texture_dir": r"{tex_dir}",
    "triplanar": True,
    "texture_size": [{side}, {thin}, {side}],  # one exact tile across
    "roughness": 0.5,
}}
mat = harness.build_textured_pbr_material("LabelMat", mat_spec)
obj.data.materials.append(mat)

scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE_NEXT"
scene.view_settings.view_transform = "Standard"
w = bpy.data.worlds.new("FlatWhite")
w.use_nodes = True
bg = w.node_tree.nodes["Background"]
bg.inputs[0].default_value = (1, 1, 1, 1)
bg.inputs[1].default_value = 1.0
scene.world = w

cam_data = bpy.data.cameras.new("Cam")
cam_data.type = "ORTHO"
cam_data.ortho_scale = {side}
cam = bpy.data.objects.new("Cam", cam_data)
cam.location = (0, -1.0, 0)
cam.rotation_euler = (math.radians(90), 0, 0)  # look +Y, up = +Z
scene.collection.objects.link(cam)
scene.camera = cam
scene.render.resolution_x = 256
scene.render.resolution_y = 256
scene.render.film_transparent = False
scene.render.filepath = r"{out_png}"
scene.render.image_settings.file_format = "PNG"
bpy.ops.render.render(write_still=True)
RESULT = {{"png": r"{out_png}"}}
"""
    res_op = runner.execute_op("run_script", {"code": code})
    assert res_op["success"], res_op.get("error")

    with Image.open(out_png) as img:
        render = np.asarray(img.convert("L"), dtype=np.uint8)
    with Image.open(tex_dir / "albedo.png") as img:
        pattern = np.asarray(img.convert("L").resize((256, 256)), dtype=np.uint8)

    # drop the 6-px border (box side strips + antialiased edge)
    sl = np.s_[6:-6, 6:-6]
    r = render[sl].astype(np.float64)
    scores = {
        "identity": _ncc(r, pattern[sl].astype(np.float64)),
        "flipud": _ncc(r, np.flipud(pattern)[sl].astype(np.float64)),
        "fliplr": _ncc(r, np.fliplr(pattern)[sl].astype(np.float64)),
        "both": _ncc(r, np.flipud(np.fliplr(pattern))[sl].astype(np.float64)),
    }
    assert scores["identity"] > 0.5, scores  # the render shows the pattern
    assert scores["identity"] > scores["flipud"] + 0.1, scores   # upright
    assert scores["identity"] > scores["fliplr"] + 0.1, scores   # unmirrored
    assert scores["identity"] > scores["both"] + 0.1, scores
