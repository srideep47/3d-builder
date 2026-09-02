"""ObjectSpec v2 — Industrial CAD & 3D Model Specification Schema.

Defines:
- Advanced geometric shapes (rounded_box, tapered_extrude, revolve_lathe, cylinder, sphere, etc.)
- Modifiers: bevel, subdivision, radial_array, mirror, boolean cuts
- Procedural & Texture-Mapped PBR Materials
- Hierarchical Measurements & Tolerances
- Assembly Constraints (ground_contact, coaxial, aligned, touching)
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator


class Unit(str, Enum):
    METERS = "meters"
    CENTIMETERS = "cm"
    MILLIMETERS = "mm"
    INCHES = "inches"
    FEET = "feet"

    def to_meters(self, val: float) -> float:
        """Convert a value in this unit to meters."""
        if self == Unit.METERS:
            return float(val)
        elif self == Unit.CENTIMETERS:
            return float(val) * 0.01
        elif self == Unit.MILLIMETERS:
            return float(val) * 0.001
        elif self == Unit.INCHES:
            return float(val) * 0.0254
        elif self == Unit.FEET:
            return float(val) * 0.3048
        return float(val)


class ShapeType(str, Enum):
    BOX = "box"
    ROUNDED_BOX = "rounded_box"
    CYLINDER = "cylinder"
    TAPERED_CYLINDER = "tapered_cylinder"
    SPHERE = "sphere"
    CONE = "cone"
    TORUS = "torus"
    TAPERED_EXTRUDE = "tapered_extrude"
    REVOLVE_LATHE = "revolve_lathe"
    EXTRUDE = "extrude"
    SWEEP = "sweep"
    ORGANIC = "organic"


class GenerationMethod(str, Enum):
    """Where a part's geometry comes from — the mesh-source contract
    (Phase 8 item 3). Every part declares exactly ONE source:

    - ``parametric``    — synthesized from the shape vocabulary by the harness.
    - ``custom_script`` — synthesized by the part's ``code`` in the harness.
    - ``image_to_3d``   — GENERATED at build time by the neural service from
      a reference image; the loop materializes ``mesh_path`` (run cache).
    - ``imported``      — an existing mesh FILE supplied with the spec (an
      owner asset, a purchased model). Never regenerated; the file is the
      geometry. ``mesh_path`` + ``target_size`` are authored, not derived.
    - ``scanned``       — reality capture (3D-scan / photogrammetry file).
      Same mechanics as ``imported``; different provenance: raw capture is
      expected dense and noisy and MUST be retopologized before delivery
      (Phase 8 item 4) — the method value is the hook that flags it.

    All file-backed sources (image_to_3d, imported, scanned) pass through
    ONE mechanical path in the harness: import → join → rescale to
    ``target_size`` → place. Provenance differs; the contract does not.
    """

    PARAMETRIC = "parametric"
    IMAGE_TO_3D = "image_to_3d"
    CUSTOM_SCRIPT = "custom_script"
    IMPORTED = "imported"
    SCANNED = "scanned"

    @property
    def is_file_backed(self) -> bool:
        """True when the part's geometry arrives as a mesh file."""
        return self in (GenerationMethod.IMAGE_TO_3D, GenerationMethod.IMPORTED, GenerationMethod.SCANNED)


class PBRMaterial(BaseModel):
    name: str = "default_pbr"
    preset: str | None = None  # e.g. "oak_wood", "brushed_steel", "chrome", "leather_black"
    color: list[float] = Field(default_factory=lambda: [0.8, 0.8, 0.8])  # [r, g, b] in 0..1 range
    roughness: float = 0.5
    metallic: float = 0.0
    transmission: float = 0.0
    emission: list[float] | None = None
    texture_dir: str | None = None  # Folder path containing Albedo, Normal, Roughness, Metallic maps
    texture_size: list[float] | None = None  # metres per texture tile (triplanar mapping scale)
    bump_strength: float | None = None  # height-map bump strength (0..1); None = material default
    triplanar: bool = False
    procedural: bool = False  # Attach procedural node shaders (pair with bake_materials for export)


class BevelModifier(BaseModel):
    width: float = 0.005  # Bevel width in meters (e.g. 5mm)
    segments: int = 3


class SubdivisionModifier(BaseModel):
    levels: int = 2


class RadialArrayModifier(BaseModel):
    count: int = 5
    axis: Literal["x", "y", "z"] = "z"
    center: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])


class LinearArrayModifier(BaseModel):
    count: int = 2
    direction: list[float] = Field(default_factory=lambda: [0.0, 0.0, 1.0])
    spacing: float = 0.1


class MirrorModifier(BaseModel):
    axis: Literal["x", "y", "z", "xy", "xz", "yz"] = "x"


class BooleanModifier(BaseModel):
    operation: Literal["difference", "union", "intersect"] = "difference"
    target_part: str


class Modifiers(BaseModel):
    bevel: BevelModifier | None = None
    subdivision: SubdivisionModifier | None = None
    radial_array: RadialArrayModifier | None = None
    linear_array: LinearArrayModifier | None = None
    mirror: MirrorModifier | None = None
    boolean: BooleanModifier | None = None


class DisplacementSpec(BaseModel):
    """Generic surface-displacement pattern for the high-poly detail pass.

    Patterns are pure geometry functions. Product knowledge — WHICH part gets
    WHICH pattern at what proportion — belongs only in
    templates/<product_class>.yaml, never in pipeline code (GLM_BRIEF: "the
    mattress is not the point"). All lengths are in the spec's unit like every
    other part length; the resolver converts them to meters.
    """
    pattern: Literal["noise", "waves", "grid_diamond", "grid_square", "bumps"]
    amplitude: float                       # peak displacement, spec units
    frequency: float = 8.0                 # repeats across the part's largest horizontal span
    frequency_y: float | None = None       # separate v-axis repeats (None = same as frequency;
    #                                           set for SQUARE cells in metres on non-square parts)
    axis: Literal["x", "y", "z"] = "z"     # travel direction (waves)
    seed: int = 0                          # deterministic noise seed
    exponent: float = 1.0                  # grid puffiness: 1=soft sine, 2=boxy
    # Only displace vertices whose local-space normal points along +z ("up")
    # or -z ("down"); "none" displaces the whole surface. Puff patterns on
    # panels (e.g. quilted tops) need "up" or the side walls distort too.
    restrict: Literal["none", "up", "down"] = "none"


class DetailSpec(BaseModel):
    """Optional per-part HIGH-POLY detail instructions (T3).

    Consumed by the harness bake pass (`bake_maps` op): the low-poly geometry
    is untouched (dimensions/gates stay exact); detail only shapes the HP copy
    the normal map is baked from. Without a detail block the bake uses the
    default bevel + subdivision shell.
    """
    bevel_width: float | None = None       # spec units; None = bake default
    subdivision_levels: int | None = None  # HP subsurf levels
    displacement: DisplacementSpec | None = None


class SeamRingSpec(BaseModel):
    """A faint pressed seam ring around an extruded wall (round 4): the wall
    is subdivided at this height and the ring's vertices are pushed inward
    by `depth` along the local wall normal, forming a soft crease that
    shades like a stitched seam. Real LP geometry — resolution-independent,
    survives decimation — because a texture-space line cannot be positioned
    reliably against the atlas packer (island phase is arbitrary).
    """
    z: float      # ring height above the part base, metres
    depth: float  # inward inset at the ring, metres


class ReviewCloseupSpec(BaseModel):
    """Review-render close-up on one named part (round 4): whole-model views
    crush small features (a 48x104 mm label is 21x45 px at 1K) — the close-up
    frames the part so the reviewer can actually read it. Product knowledge
    (WHICH part, which side) lives in the template, never in pipeline code.
    """
    name: str                                        # file suffix: <prefix>_<name>.png
    part: str                                        # part name to frame
    direction: Literal["front", "back", "left", "right"] = "front"
    pad: float = 0.3                                 # frame margin, fraction of the framed extent
    frame: Literal["part", "model_height"] = "part"  # part bounds, or full model height at the part's x/y


class ContrastProbeSpec(BaseModel):
    """Absolute-contrast probe on one rendered view (Phase 8 item 2).

    The §H defect: a fill-flattened quilt passed review because the FFT
    axis RATIO looked healthy — a ratio reaches 1.0 when both terms go to
    zero. A probe pins an absolute grey-level amplitude floor (owner's
    suggestion: 6+, i.e. a 12-level peak-to-trough swing) at the product's
    relief pitch. Product knowledge (which view, which region, what pitch)
    lives in the template — rule 11; the finishing layer only threads it
    through and records the numbers.
    """
    name: str                                              # report label
    view: Literal["front", "side", "top", "iso"] = "top"   # which rendered view
    # normalized image coords (x0, y0, x1, y1), y from the TOP
    region: list[float]
    # expected relief cycles ACROSS the region, [x, y]
    cycles: list[float]
    band: list[float] = Field(default_factory=lambda: [0.6, 1.4])
    min_amplitude: float = 6.0                             # grey levels
    axes: Literal["both", "x", "y"] = "both"               # which axes the floor gates

    @model_validator(mode="after")
    def _sane(self) -> "ContrastProbeSpec":
        if len(self.region) != 4:
            raise ValueError("region must be [x0, y0, x1, y1]")
        x0, y0, x1, y1 = self.region
        if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
            raise ValueError(
                f"region {self.region} must satisfy 0<=x0<x1<=1 and 0<=y0<y1<=1 "
                "(normalized image coordinates, y from the top)")
        if len(self.cycles) != 2 or any(c <= 0 for c in self.cycles):
            raise ValueError(f"cycles {self.cycles} must be two positive values [x, y]")
        if len(self.band) != 2 or not (0.0 < self.band[0] < self.band[1]):
            raise ValueError(f"band {self.band} must be 0 < lo < hi")
        if self.min_amplitude < 0:
            raise ValueError("min_amplitude must be >= 0")
        return self


class PartSpec(BaseModel):
    name: str
    shape: ShapeType = ShapeType.ROUNDED_BOX
    dimensions: list[float] = Field(default_factory=lambda: [1.0, 1.0, 1.0])  # [x, y, z] in meters
    position: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])   # [x, y, z] in meters
    rotation: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])   # degrees [rx, ry, rz]
    top_scale: list[float] | None = None  # For tapered_extrude [sx, sy]
    profile_points: list[list[float]] | None = None  # revolve_lathe [[r,z],...] or extrude [[x,y],...]
    path_points: list[list[float]] | None = None  # sweep [[x, y, z], ...]
    path_closed: bool = False  # sweep: close the path into a loop (no tube end caps)
    # Extrude cap fill: fan by default — the client delivery gate is strict
    # (0 n-gons) and the analyst has no reason to choose n-gon caps.
    caps: Literal["ngon", "fan"] = "fan"
    position_mode: Literal["center", "base"] | None = None  # None = auto per shape
    method: GenerationMethod = GenerationMethod.PARAMETRIC
    material: PBRMaterial | None = None
    modifiers: Modifiers | None = None
    smooth_shade: bool = False
    segments: int | None = None
    # file-backed parts (image_to_3d / imported / scanned): crop of the
    # reference image (image_to_3d only), the mesh file, and its final size.
    # target_size is the rescale target in spec units — never inferred from
    # the file (rule 9: owner-stated dimensions only).
    image_crop: str | None = None
    mesh_path: str | None = None
    target_size: list[float] | None = None
    # how the raw file bbox is mapped onto target_size: "fit" (default)
    # rescales per-axis so the bbox lands EXACTLY on target_size (dimension
    # gates exact, but a mismatched aspect ratio is stretched); "uniform"
    # applies one factor — min of the per-axis ratios — so aspect is
    # preserved and no axis exceeds target_size (authored assets and scans,
    # where per-axis stretch is damage).
    mesh_scale: Literal["fit", "uniform"] = "fit"
    # script method: agent-authored bpy code
    code: str | None = None
    # optional high-poly detail instructions for the bake pass (DetailSpec)
    detail: DetailSpec | None = None
    # pressed seam rings on extruded walls (metres; template converts fractions)
    seam_rings: list[SeamRingSpec] | None = None
    # Atlas texel-density multiplier for this part's surfaces (Phase 8 item 1:
    # a uniform atlas gives velvet detail it cannot show while printed text
    # starves). 1.0 = the uniform share; a label at 4.0 gets 4x the texels
    # per metre of the shared atlas. Total atlas use is unchanged — the
    # packer renormalises across all parts.
    texel_priority: float = Field(default=1.0, ge=0.25, le=16.0)
    meta: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_mesh_source(self):
        """Mesh-source contract (Phase 8 item 3), enforced fail-closed: a
        part declares exactly one geometry source and carries only the
        fields that source is entitled to. A part with two sources (e.g.
        parametric + mesh_path) or a file-backed part without its file
        would otherwise build silently wrong or skip silently."""
        m = self.method
        if m in (GenerationMethod.PARAMETRIC, GenerationMethod.CUSTOM_SCRIPT):
            if self.mesh_path is not None:
                raise ValueError(
                    f"Part '{self.name}' is {m.value} but carries mesh_path — "
                    "a part has exactly one geometry source; drop mesh_path or "
                    "set method to a file-backed value (image_to_3d/imported/scanned)."
                )
        if m in (GenerationMethod.IMPORTED, GenerationMethod.SCANNED):
            if not self.mesh_path:
                raise ValueError(
                    f"Part '{self.name}' is {m.value} — mesh_path is required "
                    "(the file IS the geometry; nothing generates it)."
                )
            if not self.target_size:
                raise ValueError(
                    f"Part '{self.name}' is {m.value} — target_size is required "
                    "(owner-stated size in spec units; file units are never trusted)."
                )
        if self.image_crop is not None and m != GenerationMethod.IMAGE_TO_3D:
            raise ValueError(
                f"Part '{self.name}' carries image_crop but is {m.value} — "
                "image_crop selects the reference image for image_to_3d parts only."
            )
        if self.code is not None and m != GenerationMethod.CUSTOM_SCRIPT:
            raise ValueError(
                f"Part '{self.name}' carries code but is {m.value} — "
                "code is executed only for custom_script parts."
            )
        if self.target_size is not None and (
            len(self.target_size) != 3 or any(v <= 0 for v in self.target_size)
        ):
            raise ValueError(
                f"Part '{self.name}' target_size must be 3 positive values, "
                f"got {self.target_size}."
            )
        return self


class MeasurementSpec(BaseModel):
    name: str
    target_value: float
    unit: Unit = Unit.METERS
    applies_to: str = "overall.height_z"  # e.g. "overall.width_x", "seat_cushion.height_z"
    tolerance_m: float = 0.001            # Default 1mm tolerance in meters


class ConstraintSpec(BaseModel):
    type: Literal["ground_contact", "coaxial", "coplanar", "aligned", "touching", "symmetry"]
    parts: list[str]
    axis: Literal["x", "y", "z"] | None = None
    offset: float = 0.0


class ObjectSpec(BaseModel):
    schema_name: str = "threed-objectspec"
    schema_version: str = "2.0.0"
    name: str = "Untitled Model"
    description: str = ""
    units: Unit = Unit.METERS
    tolerance_m: float = 0.001
    source_images: list[str] = Field(default_factory=list)
    parts: list[PartSpec] = Field(default_factory=list)
    measurements: list[MeasurementSpec] = Field(default_factory=list)
    constraints: list[ConstraintSpec] = Field(default_factory=list)
    tri_budget: int = 60000
    # review-render close-ups (round 4): threaded verbatim into the
    # render_views op; product knowledge lives in the template
    review_closeups: list[ReviewCloseupSpec] | None = None
    # absolute-contrast probes (Phase 8 item 2): run against the rendered
    # review views; amplitude floor in grey levels, never a ratio
    contrast_probes: list[ContrastProbeSpec] | None = None
