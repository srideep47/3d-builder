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
from pydantic import BaseModel, Field


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
    PARAMETRIC = "parametric"
    IMAGE_TO_3D = "image_to_3d"
    CUSTOM_SCRIPT = "custom_script"


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
    caps: Literal["ngon", "fan"] = "ngon"  # extrude cap fill: one n-gon face or a triangle fan
    position_mode: Literal["center", "base"] | None = None  # None = auto per shape
    method: GenerationMethod = GenerationMethod.PARAMETRIC
    material: PBRMaterial | None = None
    modifiers: Modifiers | None = None
    smooth_shade: bool = False
    segments: int | None = None
    # image_to_3d parts: crop of the reference image, generated mesh, final size
    image_crop: str | None = None
    mesh_path: str | None = None
    target_size: list[float] | None = None
    # script method: agent-authored bpy code
    code: str | None = None
    # optional high-poly detail instructions for the bake pass (DetailSpec)
    detail: DetailSpec | None = None
    # pressed seam rings on extruded walls (metres; template converts fractions)
    seam_rings: list[SeamRingSpec] | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


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
