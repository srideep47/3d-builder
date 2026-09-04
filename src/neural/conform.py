"""Conform (GLM_PROMPT_NEURAL_INTAKE.md §4.4) — the adapter that makes a
generated neural GLB a deliverable.

The order's chain, and where each step lives:

  1. Import the GLB — weld-on-import ships (Phase 8.5 R1): every
     file-backed import runs `_weld_imported_mesh` in the harness.
  2. Scale to the JobCard's L×W×H on the axis map — HERE, with the S1
     refusal: if the source aspect ratio is off beyond tolerance,
     non-uniform scaling would visibly smear texture and geometry, so
     REFUSE and report. Never quietly distort (the square-mattress case).
  3. Consolidate bodies — voxel retopology (measured: collapses
     multi-shell neural output to one closed body; QuadriFlow does not
     consolidate). Lives in the harness via the part's retopology block.
  4. Re-quad / preserve quads for the FBX — the SAME voxel remesh: Blender's
     Remesh VOXEL outputs all-quad geometry (docs/MESH_SOURCES.md §5.3),
     so one tool delivers consolidation AND the quads the FBX needs from
     the live scene. The R2 schema allows one tool per part; voxel is the
     choice that satisfies both steps. `requad=True` switches to
     QuadriFlow with a face target for a known-single-body source.
  5. Split the packed metallic-roughness into named PNGs — HERE
     (glTF convention: G = roughness, B = metallic).
  6. Bake normal + AO HP→LP — ships (bake_maps, finish_delivery).
  7. Package via the delivery chain — ships (finish_delivery: gates,
     qa_report, the file contract).

The conform output is an ObjectSpec whose single file-backed part carries
everything the delivery chain needs: mesh_path, card-axis target_size,
the retopology block, and a material pointed at the split maps.
`finish_delivery(job, spec)` then builds, verifies, bakes and packages —
conform never touches Blender itself (one process per op stays the
runner's business).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh
from PIL import Image

from ..client.job import JobCard
from ..spec.schema import (
    GenerationMethod,
    ObjectSpec,
    PBRMaterial,
    PartSpec,
    RetopologySpec,
    ShapeType,
)
from .analyse import NeuralAnalyseReport, aspect_deviation

# Measured usable band for voxel remesh (docs/MESH_SOURCES.md §5.3): the
# remesh COLLAPSES at voxel_size 0.004 (400-quad blob, −61 mm) on ~0.4 m
# objects; ≥ 0.005 is the measured-safe floor. ~64 voxels across the
# smallest axis lands mid-band (0.006 on a 0.4 m object → 25k quads).
VOXEL_MIN_M = 0.005
VOXEL_MAX_M = 0.02
VOXELS_ACROSS_MIN_AXIS = 64

# glTF packed metallic-roughness channel convention (§3.2)
ROUGHNESS_CHANNEL = 1  # G
METALLIC_CHANNEL = 2  # B


class ConformRefusal(Exception):
    """S1: aspect ratio off beyond tolerance — refuse and report, never
    quietly distort."""


def split_packed_maps(glb_path: str | Path, out_dir: str | Path) -> dict[str, dict]:
    """§4.4 step 5: split the GLB's PBR textures into the canonical named
    PNGs a PBRMaterial.texture_dir consumes (albedo.png / roughness.png /
    metallic.png; normal + AO are absent from TRELLIS output BY DESIGN and
    come from the delivery bake).

    Returns {map: {"path": str, "resolution": [w, h]}} for the maps
    actually written — the caller records which exist, never assumes.
    """
    path = Path(glb_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    loaded = trimesh.load(str(path))
    geometries = loaded.geometry.values() if isinstance(loaded, trimesh.Scene) else [loaded]
    base_tex = packed_tex = None
    for geom in geometries:
        mat = getattr(getattr(geom, "visual", None), "material", None)
        if mat is None:
            continue
        if base_tex is None:
            base_tex = getattr(mat, "baseColorTexture", None)
        if packed_tex is None:
            packed_tex = getattr(mat, "metallicRoughnessTexture", None)
        if base_tex is not None and packed_tex is not None:
            break

    written: dict[str, dict] = {}

    def _record(name: str, img) -> None:
        p = out / f"{name}.png"
        img.save(p)
        written[name] = {"path": str(p), "resolution": list(img.size)}

    if base_tex is not None:
        _record("albedo", base_tex.convert("RGBA"))
    if packed_tex is not None:
        arr = np.asarray(packed_tex.convert("RGBA"))
        for name, channel in (("roughness", ROUGHNESS_CHANNEL), ("metallic", METALLIC_CHANNEL)):
            chan = arr[..., channel]
            _record(name, Image.fromarray(chan, mode="L"))
    return written


def check_aspect(
    measured_extents: list[float] | tuple[float, ...],
    job_card: JobCard,
    tolerance: float = 0.05,
) -> tuple[bool, str]:
    """The S1 gate as a standalone verdict: (ok, refusal/ok message)."""
    expected = job_card.expected_bounds_m()
    card = [expected["x"], expected["y"], expected["z"]]
    deviation = aspect_deviation(measured_extents, card)
    if deviation is None:
        return False, "degenerate extents — aspect ratio cannot be compared"
    worst = float(deviation.max())
    if worst <= tolerance:
        return True, (
            f"aspect within tolerance (worst per-axis deviation {worst:.1%} "
            f"≤ {tolerance:.0%})"
        )
    axes = ("x", "y", "z")
    per_axis = ", ".join(
        f"{axes[i]}: {deviation[i]:.1%}" for i in range(3)
    )
    return False, (
        f"aspect ratio off beyond tolerance — measured extents "
        f"{[round(float(v), 4) for v in measured_extents]} m vs card "
        f"{[round(float(v), 4) for v in card]} m on the axis map; per-axis "
        f"deviation {per_axis} (worst {worst:.1%} > {tolerance:.0%}). "
        "Non-uniform scaling to the card would visibly smear texture and "
        "geometry — REFUSED (S1). Re-shoot the reference views (§3.1: low "
        "view diversity is the usual cause) or correct the card."
    )


def _voxel_size_for(target_size_m: list[float]) -> float:
    """~64 voxels across the smallest axis, clamped into the measured-safe
    band [0.005, 0.02] m (collapse hazard below, wasted detail above)."""
    smallest = min(target_size_m)
    return float(min(max(smallest / VOXELS_ACROSS_MIN_AXIS, VOXEL_MIN_M), VOXEL_MAX_M))


def build_conform_spec(
    glb_path: str | Path,
    job_card: JobCard,
    *,
    analyse_report: NeuralAnalyseReport | None = None,
    maps_dir: str | Path | None = None,
    requad: bool = False,
    declared_fabric: bool = False,
    part_name: str = "body",
) -> tuple[ObjectSpec, dict]:
    """Author the conform ObjectSpec for one generated neural GLB.

    Steps 2–4 of §4.4: S1 aspect refusal (raises ConformRefusal), target
    sizing on the card's axis map, retopology choice. Returns (spec,
    decisions) — the decisions dict is manifest evidence: every choice and
    its reason, never invisible.
    """
    path = Path(glb_path)
    if not path.is_file():
        raise FileNotFoundError(f"generated mesh not found: {path}")

    extents = list(analyse_report.extents_m) if analyse_report is not None else None
    if extents is None:
        loaded = trimesh.load(str(path))
        mesh = loaded.to_mesh() if isinstance(loaded, trimesh.Scene) else loaded
        extents = [float(v) for v in mesh.extents]

    ok, message = check_aspect(extents, job_card)
    if not ok:
        raise ConformRefusal(message)

    expected = job_card.expected_bounds_m()
    target = [expected["x"], expected["y"], expected["z"]]  # card axis map

    decisions: dict = {
        "aspect": message,
        "target_size_m": target,
        "axis_map": {"length": job_card.axis_map.length,
                     "width": job_card.axis_map.width,
                     "height": job_card.axis_map.height},
    }

    # Retopology (steps 3+4): voxel consolidates multi-shell output AND
    # outputs quads; quadriflow is the opt-in for a known single body.
    ceiling = job_card.effective_polycount_ceiling()
    if requad:
        target_faces = int(ceiling or 50000)
        retopology = RetopologySpec(tool="quadriflow", target_faces=target_faces)
        decisions["retopology"] = (
            f"quadriflow target_faces={target_faces} — re-quad requested; "
            "assumes a single body (QuadriFlow does NOT consolidate)"
        )
    else:
        voxel_size = _voxel_size_for(target)
        retopology = RetopologySpec(tool="voxel", voxel_size=voxel_size)
        decisions["retopology"] = (
            f"voxel voxel_size={voxel_size:.4f} m (~{VOXELS_ACROSS_MIN_AXIS} voxels "
            "across the smallest card axis, clamped to the measured-safe band "
            f"[{VOXEL_MIN_M}, {VOXEL_MAX_M}] m) — consolidates multi-shell neural "
            "output to one closed body AND outputs quads for the FBX"
        )

    # Material: the split maps when available; metallic override for
    # declared fabric (§3.6: the neural metalness channel measured 34% on
    # fabric — unreliable, so the card/intake decision wins).
    metallic = analyse_report.metallic if analyse_report is not None else None
    roughness = analyse_report.roughness if analyse_report is not None else None
    maps_path = Path(maps_dir) if maps_dir else None
    has_maps = maps_path is not None and any(maps_path.glob("albedo.png"))
    if declared_fabric:
        decisions["metallic"] = (
            f"declared fabric → metallic pinned to 0.0 (measured channel value "
            f"{metallic if metallic is not None else 'unknown'} is unreliable on "
            "soft goods, §3.6)"
        )
        metallic_value = 0.0
    else:
        metallic_value = float(metallic) if metallic is not None else 0.0
        decisions["metallic"] = f"measured channel value {metallic}"
    material = PBRMaterial(
        name="neural_maps" if has_maps else "neural_flat",
        texture_dir=str(maps_path) if has_maps else None,
        roughness=float(roughness) if roughness is not None else 0.7,
        metallic=metallic_value,
        # UV-sampled (the neural atlas maps the generated surface); triplanar
        # tiling is for tileable scans — the generated maps are not tileable.
        triplanar=False,
    )
    decisions["maps_dir"] = str(maps_path) if has_maps else None
    decisions["maps_note"] = (
        "normal + AO are absent from TRELLIS output by design (§3.2) — the "
        "delivery HP→LP bake supplies them"
    )

    part = PartSpec(
        name=part_name,
        method=GenerationMethod.IMAGE_TO_3D,
        shape=ShapeType.ORGANIC,
        mesh_path=str(path.resolve()),
        target_size=target,
        dimensions=target,
        retopology=retopology,
        material=material,
    )
    spec = ObjectSpec(
        name=f"{job_card.job_code} neural conform",
        parts=[part],
    )
    return spec, decisions
