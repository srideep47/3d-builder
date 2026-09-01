"""Product-class templates — the ONLY place product knowledge lives
(GLM_BRIEF rule 11: no product noun in the finishing layer).

A template describes a product class entirely in PROPORTIONS (fractions of
the job's owner-supplied L x W x H) plus material/texture recipes. Nothing
here is mattress-specific: the vocabulary is generic stacked-band geometry
(banded prism shell + optional domed crown band + perimeter tape sweeps +
side decal), which covers any "layered shell" product. The product noun
lives in `templates/<product_class>.yaml` and in the band/surface NAMES it
declares — never in this code.

`compile_spec(template, job)` turns template + JobCard (with REAL, owner-
supplied dimensions — never inferred, rule 9) into a plain ObjectSpec the
existing pipeline already understands. The compiler emits:

  - one fan-capped `extrude` part per band, all sharing the same
    superellipse footprint outline (flush wall junctions, zero n-gons);
  - a script-method quilted-dome part for the crown band (a Cartesian grid
    cap FG-mapped onto the same footprint superellipse: square quilt cells,
    stitch valleys on grid lines, quads + a triangle cap fan);
  - one closed-loop `sweep` part per tape edge (thin binding strip: the
    rectangular section seats flush on the band wall and stands proud by
    exactly one protrusion, outer face on the nominal silhouette);
  - a thin box part for the side decal, standing off past the tapes;
  - overall-extent measurements + a ground-contact constraint.

Every part is quad/tri only and each band is a closed solid, so the strict
n-gon gate passes by construction and the merged mesh stays watertight in
the edge-degree sense (independently closed parts).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from ..client.job import JobCard
from .schema import (ConstraintSpec, DetailSpec, ObjectSpec,
                     PBRMaterial, PartSpec, ReviewCloseupSpec,
                     SeamRingSpec)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEXTURES_ROOT = PROJECT_ROOT / "assets" / "textures"


# ── template schema ─────────────────────────────────────────────────────────


class FootprintSpec(BaseModel):
    """Superellipse cross-section of the shell (squircle): exponent 2 is an
    ellipse, 4-6 approximates a rounded rectangle. Shared by bands, dome and
    tape paths so junctions are flush."""

    exponent: float = Field(default=5.0, gt=0)
    segments: int = Field(default=48, ge=8)


class TemplateBand(BaseModel):
    """One horizontal band of the shell, top to bottom."""

    name: str
    height_fraction: float = Field(gt=0)  # of total H
    material: str  # key into textures
    seams: list["BandSeamSpec"] | None = None  # faint stitched lines INSIDE the band


class BandSeamSpec(BaseModel):
    """A faint stitched seam line inside a band (round 4, reviewer's eyes:
    the velvet border is ONE dark mass with two faint seams — NOT white
    ribs between velvet bands). The seam is real geometry (a pressed crease
    in the wall), so it shades under raking light and reads as a stitch
    line, not a colour change — the mass stays continuous at normal viewing
    distance. Fractions: height is of the BAND, depth of min(L, W, H)."""

    height_fraction: float = Field(gt=0, lt=1)  # of the band height, from its base
    depth_fraction: float = Field(gt=0)         # crease depth, of min(L, W, H)


class CloseupSpec(BaseModel):
    """Review-render close-up on one part (round 4): whole-model views
    crush small features, and the reviewer's eyes are the quality gate.
    `frame: part` frames the part's own bounds; `frame: model_height`
    keeps the part's x/y but frames the model's full height (the part in
    its stack context)."""

    name: str
    part: str
    direction: Literal["front", "back", "left", "right"] = "front"
    pad: float = Field(default=0.3, gt=0)
    frame: Literal["part", "model_height"] = "part"


class TapeEdgeSpec(BaseModel):
    """A closed-loop swept strip centred on the bottom boundary of
    `at_boundary_below` (the named band): thin binding tape that hugs the
    wall (GLM_BRIEF §5.2) — seated flush, standing proud by one protrusion.

    Fractions are of min(L, W, H), the cross-section scale: a tape is a
    detail of the side face, not a fraction of the product height
    (H-relative fractions made 74 mm collars on a tall mattress)."""

    at_boundary_below: str
    width_fraction: float = Field(gt=0)  # vertical extent, of min(L, W, H)
    protrusion_fraction: float = Field(gt=0)  # radial stick-out, of min(L, W, H)
    material: str


class QuiltSpec(BaseModel):
    """REAL low-poly quilt geometry on the crown band (reviewer round-3
    correction: in the reference photos the puffs break the top silhouette,
    so the quilt must be mesh, not a baked normal map — a normal map carries
    no silhouette). The dome profile is lowered by the puff amplitude so the
    peaks land exactly on the band top: overall bounds stay nominal at any
    template scale. Cells are square in metres: the cross-cell count is
    derived from the footprint aspect at compile time (rounded to a whole
    cell so the grid ends on a valley, not a partial cell)."""

    pattern: Literal["grid_diamond", "grid_square", "bumps"] = "grid_square"
    cells_across: int = Field(default=17, ge=1)      # cells along the LENGTH
    amplitude_fraction: float = Field(gt=0)          # of one CELL — puff depth
    exponent: float = Field(default=1.6, gt=0)       # soft-edged puffs
    divisions: int = Field(default=4, ge=2)          # grid samples per cell
    restrict_z: float = Field(default=0.85, gt=0.0, lt=1.0)
    # ^^ displace only vertices whose normal.z >= this: the near-vertical
    # shoulder stays put, so radial bulges can never push the silhouette
    # past the footprint (the profile reaches normal.z=0.85 only ~12 mm
    # inside the wall — below that the horizontal puff component would
    # outrun the dome's radial inset and break the bounds contract)


class CrownSpec(BaseModel):
    """The top band as a quilted dome: a Cartesian grid cap mapped onto the
    shared footprint superellipse (FG-style square-to-squircle map — square
    quilt cells in straight rows/columns, matching the reference photos),
    z from the pillow profile z(s) = H*(1 - s^Q)^(1/Q), plus the quilt puff
    displacement applied directly to the LP vertices."""

    profile_exponent: float = Field(default=3.5, gt=0)
    quilt: QuiltSpec | None = None


class TextureLayerSpec(BaseModel):
    """One procedural pattern printed/embossed onto a surface. Geometry
    params accept `*_m` variants (converted via the surface tile size) so
    patterns scale physically, not per-tile."""

    kind: Literal["oval_holes", "herringbone", "chevron"]
    params: dict[str, Any] = Field(default_factory=dict)
    color: list[float] | None = None  # print colour (None = no albedo print)
    opacity: float = Field(default=0.5, ge=0, le=1)
    height_delta: float = 0.0  # emboss (+) / recess (-) where the mask is 1
    roughness_delta: float = 0.0


class SurfaceSpec(BaseModel):
    """A composed texture surface (consumed by scripts/gen_template_textures.py
    via src/textures/compose.py). Writes albedo/roughness/height PNGs to
    assets/textures/<product_class>/<name>/."""

    base: Literal["scan", "flat"] = "flat"
    scan: str | None = None  # CC0 scan dir name under input/textures/cc0/
    tint: list[float] | None = None  # force the colour family, keep structure
    # rotate the composed maps by quarter turns: a scan's directional nap
    # (e.g. horizontal streaks) can be turned to render vertical on walls
    rotate_deg: Literal[0, 90, 180, 270] = 0
    roughness: float = Field(default=0.7, ge=0, le=1)
    bump_strength: float = Field(default=0.15, ge=0, le=1)
    layers: list[TextureLayerSpec] = Field(default_factory=list)
    tile_m: float = Field(default=0.3, gt=0)  # physical metres per output tile
    resolution: int = Field(default=1024, ge=64)

    @model_validator(mode="after")
    def _scan_required(self) -> "SurfaceSpec":
        if self.base == "scan" and not self.scan:
            raise ValueError("base='scan' requires scan (dir name under input/textures/cc0/)")
        return self


class DecalSpec(BaseModel):
    """A rectangular patch on one side face (e.g. a brand label)."""

    face: Literal["front", "back", "left", "right"] = "front"
    along_fraction: float = Field(default=0.0, ge=-1.0, le=1.0)  # of half face width
    center_height_fraction: float = Field(ge=0.0, le=1.0)  # of H
    aspect: float = Field(gt=0.0, le=4.0)  # patch width / height (<1 portrait)
    height_fraction: float = Field(gt=0, le=1.0)  # of H
    texture: str  # DIR containing albedo.png (repo-relative), like surfaces


class CarryHandleSpec(BaseModel):
    """Vertical straps on the long-side faces crossing the border stack
    (reviewer round-3 correction: the carry handles EXIST — photo 9.28.35 —
    the §9.4 open question is closed; they were previously deferred)."""

    enabled: bool = False
    count_per_side: int = Field(default=2, ge=1, le=4)
    width_fraction: float = Field(default=0.08, gt=0)  # of cross-section scale
    # of the tape protrusion: the strap's outer face stays just BEHIND the
    # tape plane — flush enough to read as surface-mounted, recessed enough
    # that straps crossing the tapes never z-fight on the shared outer plane
    protrusion_fraction: float = Field(default=0.92, gt=0.0, le=1.0)
    from_boundary: str  # strap bottom at this band's bottom edge
    to_boundary: str    # strap top at this band's bottom edge (full stack span)
    material: str


class FeaturesSpec(BaseModel):
    carry_handles: CarryHandleSpec | None = None


class TemplateSpec(BaseModel):
    product_class: str
    description: str = ""
    footprint: FootprintSpec = Field(default_factory=FootprintSpec)
    bands: list[TemplateBand]
    crown: CrownSpec | None = None  # if set, bands[0] is the domed crown
    tape_edges: list[TapeEdgeSpec] = Field(default_factory=list)
    decal: DecalSpec | None = None
    textures: dict[str, SurfaceSpec]
    features: FeaturesSpec = Field(default_factory=FeaturesSpec)
    review_closeups: list[CloseupSpec] = Field(default_factory=list)
    tri_budget: int = Field(default=50000, gt=0)

    @field_validator("product_class")
    @classmethod
    def _safe_class(cls, v: str) -> str:
        v = v.strip().lower()
        if not v or not all(c.isalnum() or c in "-_" for c in v):
            raise ValueError(f"product_class {v!r} must be slug-like (templates/<class>.yaml)")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> "TemplateSpec":
        total = sum(b.height_fraction for b in self.bands)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"band height_fractions must sum to 1.0 (got {total:.6f} over "
                f"{len(self.bands)} bands) — proportions only, absolute sizes "
                "come from the job card"
            )
        names = [b.name for b in self.bands]
        if len(set(names)) != len(names):
            raise ValueError(f"band names must be unique: {names}")
        band_materials = {b.material for b in self.bands} | {t.material for t in self.tape_edges}
        missing = band_materials - set(self.textures)
        if missing:
            raise ValueError(f"materials referenced by bands/tapes but not defined in textures: {sorted(missing)}")
        for tape in self.tape_edges:
            if tape.at_boundary_below not in names:
                raise ValueError(
                    f"tape at_boundary_below {tape.at_boundary_below!r} is not a band name ({names})"
                )
        ch = self.features.carry_handles
        if ch is not None and ch.enabled:
            for field in ("from_boundary", "to_boundary"):
                if getattr(ch, field) not in names:
                    raise ValueError(
                        f"carry_handles {field} {getattr(ch, field)!r} is not a band name ({names})"
                    )
            if ch.material not in self.textures:
                raise ValueError(
                    f"carry_handles material {ch.material!r} is not defined in textures"
                )
        if self.crown is not None and self.crown.quilt is not None and len(names) < 2:
            raise ValueError("a quilted crown needs at least one band below it for the tape to sit on")
        part_names = {b.name for b in self.bands}
        part_names |= {f"tape_{i + 1}" for i in range(len(self.tape_edges))}
        if self.features.carry_handles is not None and self.features.carry_handles.enabled:
            ch = self.features.carry_handles
            part_names |= {
                f"handle_{face}_{k + 1}"
                for face in ("front", "back")
                for k in range(ch.count_per_side)
            }
        if self.decal is not None:
            part_names.add("decal_patch")
        for cu in self.review_closeups:
            if cu.part not in part_names:
                raise ValueError(
                    f"review_closeups part {cu.part!r} is not a part this template "
                    f"builds ({sorted(part_names)})"
                )
        return self


def load_template(path: str | Path) -> TemplateSpec:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Template not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Template {p} must be a YAML mapping")
    try:
        return TemplateSpec.model_validate(raw)
    except Exception as e:
        raise ValueError(f"Template {p} failed validation:\n{e}") from e


# ── geometry helpers ────────────────────────────────────────────────────────


def footprint_outline(a: float, b: float, exponent: float, segments: int) -> list[list[float]]:
    """Superellipse outline points [[x, y], ...], CCW, centred at origin.
    `a`/`b` are the half-extents. Exponent 2 = ellipse, larger = squircle.
    Bands, dome boundary ring and tape paths all sample THIS function so
    every junction is flush by construction."""
    pts = []
    for i in range(segments):
        t = 2.0 * math.pi * i / segments
        ct, st = math.cos(t), math.sin(t)
        x = a * math.copysign(abs(ct) ** (2.0 / exponent), ct)
        y = b * math.copysign(abs(st) ** (2.0 / exponent), st)
        pts.append([x, y])
    return pts


def _offset_loop(points: list[list[float]], delta: float) -> list[list[float]]:
    """Offset a closed 2D loop outward by `delta` along point normals
    (from cyclic neighbours). The footprint loop is star-shaped about the
    origin, so each normal is oriented away from it. Used to seat tape
    sections flush on the band wall: offsetting the wall loop by half a
    protrusion puts the section's inner face exactly back on the wall."""
    n = len(points)
    out = []
    for i, (x, y) in enumerate(points):
        x0, y0 = points[(i - 1) % n]
        x1, y1 = points[(i + 1) % n]
        tx, ty = x1 - x0, y1 - y0
        ln = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / ln, tx / ln
        if nx * x + ny * y < 0.0:  # keep the outward side
            nx, ny = -nx, -ny
        out.append([x + delta * nx, y + delta * ny])
    return out


# Self-contained QUILTED-dome builder (runs inside Blender via the script-
# method part; the harness script namespace only exposes bpy). __TOKENS__ are
# substituted by compile_spec. Builds from z=0 up to the band top at the
# origin; the caller positions it via obj.location.
#
# Round-3 rework (reviewer's eyes, GLM_BRIEF.md §5.2): the quilt is REAL
# low-poly geometry — the puffs must break the top silhouette, which a baked
# normal map cannot do. Construction, in order:
#   1. a Cartesian grid in (u, v) in [-1, 1]^2: uniform over the interior at
#      exactly 2/(cells*divisions) spacing ANCHORED AT u=-1, so every
#      `divisions`-th line is a cell boundary and the stitch valleys land
#      exactly on grid lines; past the last whole cell the lines cluster
#      quadratically toward the wall (the profile's near-vertical drop is
#      resolved without over-tessellating the flat top);
#   2. an FG-style superellipse map (u, v) -> (x, y): square quilt cells in
#      metres in the interior, boundary EXACTLY on the footprint
#      superellipse at z=0 (seats on the band walls with no gap), bijective;
#   3. a flat bottom cap fanned from a centre vertex (seats on the band
#      below, never visible);
#   4. puffs displaced along vertex normals, the pattern evaluated in (u, v)
#      PARAMETER space (so mesh grid lines ARE stitch valleys), gated by
#      normal.z >= RZ so the near-vertical shoulder stays on the footprint.
_QUILT_DOME_SCRIPT = """
import bpy, bmesh, math

A = __A__          # half length of the body-inset footprint
B = __B__          # half width
H = __H__          # profile height = band height MINUS puff amplitude, so
                   # puff peaks land exactly on the nominal band top
N = __N__          # footprint superellipse exponent
Q = __Q__          # crown profile exponent
CX = __CX__        # quilt cells along the length (x)
CY = __CY__        # quilt cells along the width (y) — whole cells, square in m
AMP = __AMP__      # puff amplitude (m), along vertex normals
EXP = __EXP__      # puff softness exponent
DIV = __DIV__      # grid samples per quilt cell (interior spacing)
RZ = __RZ__        # displace only verts with normal.z >= RZ (bounds guard)
PATTERN = "__PATTERN__"
NAME = "__NAME__"
Z0 = __Z0__

EDGE_MAX = 0.85    # uniform region ends at the last cell boundary <= this


def dome_point(u, v):
    # FG superellipse map of the unit square onto the footprint: square
    # cells in the interior, boundary exactly ON the superellipse (sigma=1,
    # z=0 — flush with the band walls), bijective with no folds. Closed
    # form of the mapped radius: sigma^N = |u|^N+|v|^N-|u|^N*|v|^N.
    x = u * (1.0 - 0.5 * abs(v) ** N) ** (1.0 / N)
    y = v * (1.0 - 0.5 * abs(u) ** N) ** (1.0 / N)
    sig = (abs(u) ** N + abs(v) ** N - (abs(u) * abs(v)) ** N) ** (1.0 / N)
    z = H * (1.0 - sig ** Q) ** (1.0 / Q) if sig < 1.0 else 0.0
    return x * A, y * B, z


def grid_lines(cells):
    # Interior: spacing exactly 2/(cells*DIV) anchored at u=-1 — every
    # DIV-th line is a cell boundary, so pattern valleys coincide with grid
    # lines (crisp stitched creases instead of sampled wiggles). Uniform up
    # to the last whole cell boundary <= EDGE_MAX. Shoulder: quadratic
    # clustering to the wall — spacing ramps smoothly from the interior
    # step down to ~1 mm, and the last line IS u=1.0 exactly. Only the
    # positive side is generated; the negative side is the mirror.
    step = 2.0 / (cells * DIV)
    k_edge = int(math.floor((EDGE_MAX + 1.0) / step)) // DIV * DIV
    inner = [u for u in (-1.0 + k * step for k in range(k_edge + 1))
             if u >= -1e-12]
    e0 = inner[-1]
    span = 1.0 - e0
    m = max(6, min(16, int(round(2.0 * span / step))))
    shoulder = [e0 + span * (1.0 - (1.0 - i / m) ** 2) for i in range(1, m + 1)]
    pos = inner + shoulder            # centre (or near) .. 1.0, ascending
    return [-p for p in reversed(pos) if p > 1e-12] + pos


def puff(u, v):
    # Pattern in (u, v) PARAMETER space: valleys exactly on grid lines,
    # peaks at cell centres (grid_square) or cell corners (grid_diamond).
    cu = (u + 1.0) * 0.5 * CX
    cv = (v + 1.0) * 0.5 * CY
    if PATTERN == "grid_diamond":
        return abs(math.sin(math.pi * (cu + cv))
                   * math.sin(math.pi * (cu - cv))) ** EXP
    if PATTERN == "bumps":
        du = cu - math.floor(cu) - 0.5
        dv = cv - math.floor(cv) - 0.5
        return max(0.0, 1.0 - 4.0 * (du * du + dv * dv)) ** EXP
    return (abs(math.sin(math.pi * cu))
            * abs(math.sin(math.pi * cv))) ** EXP  # grid_square


us = grid_lines(CX)
vs = grid_lines(CY)
if len(us) < 3 or len(vs) < 3:
    raise ValueError("degenerate quilt grid")
bm = bmesh.new()
grid = [[bm.verts.new(dome_point(u, v)) for v in vs] for u in us]
for i in range(len(us) - 1):
    for j in range(len(vs) - 1):
        bm.faces.new((grid[i][j], grid[i + 1][j],
                      grid[i + 1][j + 1], grid[i][j + 1]))
# flat bottom cap: fan from a centre vertex at z=0 around the boundary loop
imu, jmv = len(us) - 1, len(vs) - 1
loop = ([grid[0][j] for j in range(jmv + 1)]
        + [grid[i][jmv] for i in range(1, imu + 1)]
        + [grid[imu][j] for j in range(jmv - 1, -1, -1)]
        + [grid[i][0] for i in range(imu - 1, 0, -1)])
cap = bm.verts.new((0.0, 0.0, 0.0))
for k in range(len(loop)):
    bm.faces.new((cap, loop[k], loop[(k + 1) % len(loop)]))
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
bm.normal_update()
moved = 0
for i, u in enumerate(us):
    for j, v in enumerate(vs):
        vert = grid[i][j]
        if vert.normal.z < RZ:
            continue
        vert.co = vert.co + vert.normal * (AMP * puff(u, v))
        moved += 1
me = bpy.data.meshes.new(NAME)
bm.to_mesh(me)
bm.free()
obj = bpy.data.objects.new(NAME, me)
bpy.context.collection.objects.link(obj)
obj.location.z += Z0
obj.data = me
RESULT = {"verts": len(me.vertices), "faces": len(me.polygons),
          "quads": sum(1 for p in me.polygons if len(p.vertices) == 4),
          "tris": sum(1 for p in me.polygons if len(p.vertices) == 3),
          "displaced_verts": moved}
"""


def _cross_cells(cells_x: int, a: float, b: float) -> int:
    """Whole cells across the WIDTH so cells stay square in metres
    (cell_y = 2b/cells_y ~= cell_x = 2a/cells_x)."""
    return max(1, int(round(cells_x * b / a)))


def _dome_part(name: str, a: float, b: float, height: float, crown: CrownSpec,
               footprint: FootprintSpec, z0: float) -> PartSpec:
    q = crown.quilt
    cells_x = q.cells_across if q else 1
    cell = 2.0 * a / max(cells_x, 1)
    amp = q.amplitude_fraction * cell if q else 0.0
    code = (_QUILT_DOME_SCRIPT
            .replace("__A__", repr(a))
            .replace("__B__", repr(b))
            # peaks land exactly on the band top: the profile is lowered by
            # the puff amplitude (bounds contract — any template scale)
            .replace("__H__", repr(max(height - amp, 1e-4)))
            .replace("__N__", repr(footprint.exponent))
            .replace("__Q__", repr(crown.profile_exponent))
            .replace("__CX__", str(cells_x))
            .replace("__CY__", str(_cross_cells(cells_x, a, b)))
            .replace("__AMP__", repr(amp))
            .replace("__EXP__", repr(q.exponent if q else 1.6))
            .replace("__DIV__", str(q.divisions if q else 4))
            .replace("__RZ__", repr(q.restrict_z if q else 0.85))
            .replace("__PATTERN__", q.pattern if q else "grid_square")
            .replace("__NAME__", name)
            .replace("__Z0__", repr(z0)))
    return PartSpec(
        name=name,
        shape="extrude",  # placeholder; method=custom_script drives the build
        method="custom_script",
        code=code,
        dimensions=[2.0 * a, 2.0 * b, height],
        position=[0.0, 0.0, z0],
        position_mode="base",
        smooth_shade=True,
    )


# ── the compiler ────────────────────────────────────────────────────────────


def _material_for(template: TemplateSpec, surface_name: str, part_kind: str) -> PBRMaterial:
    surface = template.textures[surface_name]
    textured = surface.base == "scan" or bool(surface.layers)
    if not textured:
        # flat surface (e.g. an unseen base) — no texture set needed
        return PBRMaterial(
            name=f"mat_{surface_name}",
            color=list(surface.tint) if surface.tint else [0.12, 0.12, 0.12],
            roughness=surface.roughness,
        )
    tex_dir = TEXTURES_ROOT / template.product_class / surface_name
    if not (tex_dir / "albedo.png").exists():
        raise FileNotFoundError(
            f"Composed texture surface '{surface_name}' is missing at {tex_dir}. "
            f"Run: python scripts/gen_template_textures.py --template "
            f"templates/{template.product_class}.yaml"
        )
    return PBRMaterial(
        name=f"mat_{surface_name}",
        texture_dir=str(tex_dir),
        triplanar=True,
        texture_size=[surface.tile_m, surface.tile_m, surface.tile_m],
        roughness=surface.roughness,
        bump_strength=surface.bump_strength,
    )


def compile_spec(template: TemplateSpec, job: JobCard) -> tuple[ObjectSpec, list[str]]:
    """Template proportions x owner-supplied job dimensions -> (ObjectSpec,
    warnings).

    Dimensions come ONLY from the job card (rule 9 — never inferred). If the
    job card carries `dims_placeholder: true` the compile succeeds (the
    pipeline can be exercised and renders produced for structural review)
    but package emission is refused downstream.
    """
    bounds = job.expected_bounds_m()  # {axis: metres} per the job's axis map
    ex, ey, ez = bounds["x"], bounds["y"], bounds["z"]
    a, b = ex / 2.0, ey / 2.0
    fp = template.footprint
    # Tape edges are thin binding strips that HUG the band wall (GLM_BRIEF
    # §5.2): the section [protrusion x width] seats its inner face flush on
    # the wall and stands proud by exactly one protrusion. Tape fractions
    # are of the CROSS-SECTION scale min(L, W, H) — a tape is a detail of
    # the side face, not a fraction of the height. The NOMINAL L/W/H must
    # remain the outer silhouette (the client dimension gate reads the
    # overall bounds at ±0.01 in), so the band body is inset by the largest
    # tape protrusion and every tape's outer face lands exactly on the
    # nominal footprint. The decal stays recessed behind the tape plane.
    scale = min(ex, ey, ez)
    protrusions = [t.protrusion_fraction * scale for t in template.tape_edges]
    p_max = max(protrusions) if protrusions else 0.0
    a_body, b_body = a - p_max, b - p_max
    if a_body <= 0 or b_body <= 0:
        raise ValueError(
            f"tape protrusion {p_max:.4f} m >= half footprint ({a:.4f}, {b:.4f}) m — "
            f"tape_edges protrusion_fraction is too large for these job dimensions"
        )
    outline = footprint_outline(a_body, b_body, fp.exponent, fp.segments)

    parts: list[PartSpec] = []
    warnings: list[str] = []

    # bands, stacked from z=0; bands[0] is the TOP band (template order)
    heights = [band.height_fraction * ez for band in template.bands]
    bottoms: dict[str, float] = {}
    z = 0.0
    for band, h in zip(reversed(template.bands), reversed(heights)):
        bottoms[band.name] = z
        z += h

    for idx, (band, h) in enumerate(zip(template.bands, heights)):
        z0 = bottoms[band.name]
        is_crown = template.crown is not None and idx == 0
        if is_crown:
            part = _dome_part(band.name, a_body, b_body, h, template.crown, fp, z0)
        else:
            part = PartSpec(
                name=band.name,
                shape="extrude",
                profile_points=outline,
                dimensions=[2.0 * a_body, 2.0 * b_body, h],
                position=[0.0, 0.0, z0],
                position_mode="base",
                caps="fan",
                smooth_shade=True,
            )
            if band.seams:
                # Faint stitched seams INSIDE the band (round 4): fractions
                # -> metres here; the harness presses the crease into the
                # wall as real LP geometry (see SeamRingSpec).
                part.seam_rings = [
                    SeamRingSpec(z=s.height_fraction * h, depth=s.depth_fraction * scale)
                    for s in band.seams
                ]
        part.material = _material_for(template, band.material, "band")
        if is_crown and template.crown.quilt is not None:
            # The quilt is REAL LP geometry now (round 3): the HP exists only
            # to round the stitch valleys — subdivision_levels=0 keeps the HP
            # a bevel-only shell (subsurf would smooth the puffs away in the
            # baked normal map, the round-2 "absent quilt" failure mode).
            part.detail = DetailSpec(subdivision_levels=0)
        parts.append(part)

    for i, tape in enumerate(template.tape_edges):
        z_tape = bottoms[tape.at_boundary_below]
        width = tape.width_fraction * scale
        protrusion = tape.protrusion_fraction * scale
        # path = the wall loop offset half a protrusion along the point
        # normals, so the section's inner face seats flush on the wall
        path = [[x, y, z_tape]
                for x, y in _offset_loop(outline, protrusion / 2.0)]
        part = PartSpec(
            name=f"tape_{i+1}",
            shape="sweep",
            path_points=path,
            path_closed=True,
            dimensions=[protrusion, width],
            position=[0.0, 0.0, z_tape],
            position_mode="center",
            smooth_shade=True,
            material=_material_for(template, tape.material, "tape"),
        )
        parts.append(part)

    ch = template.features.carry_handles
    if ch is not None and ch.enabled:
        # Reviewer round-3 correction (photo 9.28.35): the carry handles
        # exist — vertical straps crossing the FULL border stack, two per
        # long side at the quarter points. Each strap is a thin box whose
        # inner face is buried a few mm past the local wall (the wall curves
        # away from the strap centre near the quarter points) and whose
        # outer face stays just behind the tape plane: visibly raised, can
        # never widen the nominal silhouette, and never z-fights the tapes
        # it crosses.
        w = ch.width_fraction * scale
        z0, z1 = bottoms[ch.from_boundary], bottoms[ch.to_boundary]
        if z1 <= z0:
            raise ValueError(
                f"carry_handles to_boundary {ch.to_boundary!r} must sit above "
                f"from_boundary {ch.from_boundary!r} in the band stack"
            )
        protr = ch.protrusion_fraction * p_max if p_max > 0 else 0.002
        embed = 0.004  # past the wall curvature drop across the strap width
        mat = _material_for(template, ch.material, "handle")
        n_fp = fp.exponent
        for face, sign in (("front", -1.0), ("back", 1.0)):
            for k in range(ch.count_per_side):
                # quarter points for the observed 2-per-side; even spacing
                # (avoiding centre and ends) for any other count
                if ch.count_per_side == 2:
                    frac = -0.5 if k == 0 else 0.5
                else:
                    frac = 2.0 * (k + 1) / (ch.count_per_side + 1) - 1.0
                xc = frac * (ex / 2.0)
                # local wall y at the strap's outer edge (worst curvature)
                x_edge = min(abs(xc) + w / 2.0, 0.999 * a_body)
                y_wall = b_body * (1.0 - (x_edge / a_body) ** n_fp) ** (1.0 / n_fp)
                y_in, y_out = y_wall - embed, b_body + protr
                parts.append(PartSpec(
                    name=f"handle_{face}_{k + 1}",
                    shape="box",
                    dimensions=[w, y_out - y_in, z1 - z0],
                    position=[xc, sign * (y_in + y_out) / 2.0, (z0 + z1) / 2.0],
                    position_mode="center",
                    material=mat,
                ))

    if template.decal is not None:
        d = template.decal
        h = d.height_fraction * ez
        # The label is a self-contained artifact (a sewn patch): its width
        # follows its own aspect against its height, so it keeps its shape at
        # any mattress size. All horizontal references are the BODY wall
        # (inset by the tape protrusion), because that is the surface the
        # patch is sewn onto. Clamped to a quarter of the wall span so
        # pathological job dims cannot push it past the corners.
        body_span = 2.0 * (a_body if d.face in ("front", "back") else b_body)
        half_span = body_span / 2.0
        w = d.aspect * h
        if w > 0.25 * body_span:
            warnings.append(
                f"decal width {w * 1000:.1f} mm (aspect {d.aspect} x height "
                f"{h * 1000:.1f} mm) exceeds a quarter of the side wall — clamped "
                f"to {0.25 * body_span * 1000:.1f} mm; check height_fraction/aspect "
                f"against these job dimensions"
            )
            w = 0.25 * body_span
        # Patch sits ON the band wall, proud by a fraction of the tape
        # protrusion — visibly raised, still recessed behind the tape plane so
        # it can never widen the overall silhouette.
        t_patch = 0.3 * p_max if p_max > 0 else 0.002
        along = d.along_fraction * half_span
        zc = d.center_height_fraction * ez
        if abs(along) + w / 2.0 > 0.7 * half_span:
            warnings.append(
                "decal extends into the curved corner region of the side wall "
                "(beyond 70% of the half-span) — its ends will leave the flat "
                "wall; move along_fraction toward centre or shrink the patch"
            )
        # Tape z-spans must stay clear of the patch or the two interpenetrate.
        for i, tape in enumerate(template.tape_edges):
            z_t = bottoms[tape.at_boundary_below]
            t_t = tape.width_fraction * scale / 2.0
            if zc - h / 2.0 < z_t + t_t and zc + h / 2.0 > z_t - t_t:
                warnings.append(
                    f"decal z-span [{zc - h / 2.0:.4f}, {zc + h / 2.0:.4f}] m overlaps "
                    f"tape_{i + 1} [{z_t - t_t:.4f}, {z_t + t_t:.4f}] m — adjust "
                    f"center_height_fraction/height_fraction or the tape boundary"
                )
        if d.face in ("front", "back"):
            wall = -b_body if d.face == "front" else b_body
            outward = -1.0 if d.face == "front" else 1.0
            center = (along, wall + outward * (t_patch / 2.0), zc)
            box_dims = [w, t_patch, h]
        else:
            wall = -a_body if d.face == "left" else a_body
            outward = -1.0 if d.face == "left" else 1.0
            center = (wall + outward * (t_patch / 2.0), along, zc)
            box_dims = [t_patch, w, h]
        decal_dir = PROJECT_ROOT / d.texture
        if not (decal_dir / "albedo.png").exists():
            warnings.append(
                f"decal texture dir {d.texture} has no albedo.png — patch falls "
                f"back to flat black; supply the photo crop (GLM_BRIEF §5.3)"
            )
            mat = PBRMaterial(name="mat_decal", color=[0.05, 0.05, 0.05], roughness=0.6)
        else:
            mat = PBRMaterial(
                name="mat_decal",
                texture_dir=str(decal_dir),
                triplanar=True,
                texture_size=[w, t_patch, h],  # one exact tile across the patch
                roughness=0.6,
            )
        parts.append(PartSpec(
            name="decal_patch",
            shape="box",
            dimensions=box_dims,
            position=list(center),
            position_mode="center",
            material=mat,
        ))

    measurements = [
        {"name": "overall_x", "target_value": ex, "unit": "meters", "applies_to": "overall.width_x"},
        {"name": "overall_y", "target_value": ey, "unit": "meters", "applies_to": "overall.depth_y"},
        {"name": "overall_z", "target_value": ez, "unit": "meters", "applies_to": "overall.height_z"},
    ]
    ground_band = template.bands[-1].name
    spec = ObjectSpec(
        name=f"{job.job_code} {template.product_class}",
        description=f"Compiled from templates/{template.product_class}.yaml "
                    f"(proportions) and job {job.job_code} (dimensions). "
                    f"{template.description}".strip(),
        units="meters",
        tolerance_m=0.001,
        parts=parts,
        measurements=measurements,
        constraints=[ConstraintSpec(type="ground_contact", parts=[ground_band])],
        tri_budget=template.tri_budget,
        review_closeups=[
            ReviewCloseupSpec(
                name=cu.name, part=cu.part, direction=cu.direction,
                pad=cu.pad, frame=cu.frame,
            )
            for cu in template.review_closeups
        ] or None,
    )
    return spec, warnings
