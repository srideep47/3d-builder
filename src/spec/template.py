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
  - a script-method dome part for the crown band (radial rings over the
    same footprint, quads + triangle pole/cap fans);
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
from .schema import (ConstraintSpec, DetailSpec, DisplacementSpec, ObjectSpec,
                     PBRMaterial, PartSpec)

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
    """HP displacement on the crown (bakes into the LP normal map; the LP
    bounds never move). Cells are SQUARE in metres: frequency_y is derived
    from the footprint aspect at compile time."""

    pattern: Literal["grid_diamond", "grid_square", "bumps"] = "grid_diamond"
    cells_across: int = Field(default=8, ge=1)
    amplitude_fraction: float = Field(gt=0)  # of H
    exponent: float = Field(default=1.6, gt=0)


class CrownSpec(BaseModel):
    """The top band as a dome instead of a prism: radial rings over the
    shared footprint, z(s) = H*(1 - s^Q)^(1/Q). Q ~ 3-4 gives a pillow-like
    profile (near-full height over the middle, roll-off near the wall)."""

    profile_exponent: float = Field(default=3.5, gt=0)
    rings: int = Field(default=10, ge=2)
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


class TemplateSpec(BaseModel):
    product_class: str
    description: str = ""
    footprint: FootprintSpec = Field(default_factory=FootprintSpec)
    bands: list[TemplateBand]
    crown: CrownSpec | None = None  # if set, bands[0] is the domed crown
    tape_edges: list[TapeEdgeSpec] = Field(default_factory=list)
    decal: DecalSpec | None = None
    textures: dict[str, SurfaceSpec]
    features: dict[str, Any] = Field(default_factory=dict)  # e.g. carry_handles: {enabled: false}
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
        if self.crown is not None and self.crown.quilt is not None and len(names) < 2:
            raise ValueError("a quilted crown needs at least one band below it for the tape to sit on")
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


# Self-contained dome builder (runs inside Blender via the script-method part;
# the harness script namespace only exposes bpy). __TOKENS__ are substituted
# by compile_spec. Builds from z=0 up to __H__ at the origin; the caller
# positions it via obj.location.
_DOME_SCRIPT = """
import bpy, bmesh, math

A = __A__
B = __B__
H = __H__
N = __N__
Q = __Q__
SEG = __SEG__
RINGS = __RINGS__
NAME = "__NAME__"
Z0 = __Z0__


def footprint(t):
    ct, st = math.cos(t), math.sin(t)
    x = A * math.copysign(abs(ct) ** (2.0 / N), ct)
    y = B * math.copysign(abs(st) ** (2.0 / N), st)
    return x, y


bm = bmesh.new()
ts = [2.0 * math.pi * i / SEG for i in range(SEG)]
center = bm.verts.new((0.0, 0.0, H))
rings = []
for k in range(1, RINGS + 1):
    s = k / RINGS
    z = H * (1.0 - s ** Q) ** (1.0 / Q)
    rings.append([
        bm.verts.new((x * s, y * s, z)) for x, y in (footprint(t) for t in ts)
    ])
r1 = rings[0]
for i in range(SEG):
    bm.faces.new((center, r1[i], r1[(i + 1) % SEG]))
for k in range(RINGS - 1):
    r0, r1 = rings[k], rings[k + 1]
    for i in range(SEG):
        bm.faces.new((r0[i], r1[i], r1[(i + 1) % SEG], r0[(i + 1) % SEG]))
outer = rings[-1]
cap = bm.verts.new((
    sum(v.co.x for v in outer) / SEG,
    sum(v.co.y for v in outer) / SEG,
    0.0,
))
for i in range(SEG):
    bm.faces.new((cap, outer[i], outer[(i + 1) % SEG]))
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
me = bpy.data.meshes.new(NAME)
bm.to_mesh(me)
bm.free()
obj = bpy.data.objects.new(NAME, me)
bpy.context.collection.objects.link(obj)
obj.location.z += Z0
obj.data = me
RESULT = {"verts": len(me.vertices), "faces": len(me.polygons)}
"""


def _dome_part(name: str, a: float, b: float, height: float, crown: CrownSpec,
               footprint: FootprintSpec, z0: float) -> PartSpec:
    code = (_DOME_SCRIPT
            .replace("__A__", repr(a))
            .replace("__B__", repr(b))
            .replace("__H__", repr(height))
            .replace("__N__", repr(footprint.exponent))
            .replace("__Q__", repr(crown.profile_exponent))
            .replace("__SEG__", str(footprint.segments))
            .replace("__RINGS__", str(crown.rings))
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
        part.material = _material_for(template, band.material, "band")
        if is_crown and template.crown.quilt is not None:
            q = template.crown.quilt
            # Quilt puffs are per-cell features: the amplitude references the
            # CELL SIZE (footprint / cells), not the mattress height, so the
            # quilting reads identically at any mattress size.
            cell = 2.0 * a_body / max(q.cells_across, 1)
            part.detail = DetailSpec(
                displacement=DisplacementSpec(
                    pattern=q.pattern,
                    amplitude=q.amplitude_fraction * cell,
                    frequency=float(q.cells_across),
                    # square cells in metres: repeats across Y scaled by aspect
                    frequency_y=round(q.cells_across * ey / ex, 6) or None,
                    exponent=q.exponent,
                    restrict="up",
                )
            )
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
    )
    return spec, warnings
