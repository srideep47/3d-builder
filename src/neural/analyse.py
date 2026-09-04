"""Neural-output analysis (GLM_PROMPT_NEURAL_INTAKE.md §4.3).

Measured facts on a generated neural GLB, in the work order's order —
gates before eyes, always; vision (if it runs at all) is advisory and
lives elsewhere. Everything here is cheap, deterministic, main-env
(trimesh + PIL, no Blender, no torch):

  | Check                                  | Threshold                  |
  |----------------------------------------|----------------------------|
  | Triangles                              | JobCard ceiling            |
  | Open edges (after POSITION merge)      | 0                          |
  | Bodies                                 | 1 preferred; record count  |
  | Aspect ratio vs JobCard                | flag > 5% on any axis      |
  | n-gons                                 | 0 (real gate is on the FBX)|
  | Metallic on declared-fabric surfaces   | ~0 (measured defect: 34%)  |
  | The 5 maps present                     | record which + resolution  |

§3.5 traps this module is built around:
  1. UV seams inflate open-edge counts — vertices are merged by POSITION
     ONLY (merge_tex=False, merge_norm=False) before counting.
  2. trimesh geometry vertices are LOCAL — the mesh is flattened through
     the scene graph (scene.to_mesh()) before any world-space math.

§3.2 finding baked into the report shape: TRELLIS supplies 3 of the 5
canonical maps (BaseColor + packed metallic-roughness); normal and AO are
ABSENT by design and come from the conform HP→LP bake — so the maps check
RECORDS presence and resolution (reporting discipline: never "textures
working" because a GLB got bigger) but does not gate on normal/AO at
analyse time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh

from ..client.job import JobCard

# >5% deviation on any axis of the normalized extent ratio (§4.3: the
# check that would have caught the square mattress — 0.999 vs 0.750).
ASPECT_TOLERANCE = 0.05
# Fabric should read ~0 metallic; the measured TRELLIS defect was 34%.
# 0.10 leaves room for noise while still failing the defective case hard.
METALLIC_FABRIC_MAX = 0.10

CANONICAL_MAPS = ("albedo", "roughness", "metallic", "normal", "ao")


@dataclass
class NeuralAnalyseReport:
    glb_path: str
    triangles: int
    vertices: int
    extents_m: list[float]
    bodies: int
    checks: list[dict] = field(default_factory=list)
    maps: dict[str, dict] = field(default_factory=dict)
    metallic: float | None = None
    roughness: float | None = None

    @property
    def passed(self) -> bool:
        """All GATING checks passed (records never gate — §4.3's table is
        checks, but only these decide whether conform may proceed)."""
        return all(c["passed"] for c in self.checks if c["gating"])

    @property
    def aspect_ok(self) -> bool:
        aspect = next((c for c in self.checks if c["name"] == "aspect_ratio"), None)
        return True if aspect is None else bool(aspect["passed"])

    def failed_checks(self) -> list[dict]:
        return [c for c in self.checks if c["gating"] and not c["passed"]]

    def to_dict(self) -> dict:
        return {
            "glb_path": self.glb_path,
            "triangles": self.triangles,
            "vertices": self.vertices,
            "extents_m": list(self.extents_m),
            "bodies": self.bodies,
            "metallic": self.metallic,
            "roughness": self.roughness,
            "checks": self.checks,
            "maps": self.maps,
            "passed": self.passed,
        }


def _world_mesh(path: Path) -> trimesh.Trimesh | None:
    """Flattened world-space mesh (§3.5 trap 2), NOT yet merged — the
    caller owns the merge policy per check."""
    loaded = trimesh.load(str(path))
    if isinstance(loaded, trimesh.Scene):
        if not loaded.geometry:
            return None
        mesh = loaded.to_mesh()
    else:
        mesh = loaded
    return trimesh.Trimesh(
        vertices=np.asarray(mesh.vertices), faces=np.asarray(mesh.faces), process=False
    )


def _position_merged(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Merge by POSITION ONLY (§3.5 trap 1): UV seams and per-corner
    normals keep duplicate vertices that are not geometry boundaries."""
    merged = mesh.copy()
    merged.merge_vertices(merge_tex=False, merge_norm=False)
    return merged


def _open_edge_count(mesh: trimesh.Trimesh) -> int:
    groups = trimesh.grouping.group_rows(mesh.edges_sorted, require_count=1)
    return int(len(groups))


def _scan_materials(path: Path) -> tuple[dict[str, dict], float | None, float | None]:
    """Maps present (with resolution) and mean metallic/roughness across
    the GLB's PBR materials. Packed glTF convention: G = roughness,
    B = metallic; scalar factors are the fallback when no texture."""
    loaded = trimesh.load(str(path))
    geometries = (
        loaded.geometry.values() if isinstance(loaded, trimesh.Scene) else [loaded]
    )
    maps: dict[str, dict] = {name: {"present": False, "resolution": None} for name in CANONICAL_MAPS}
    metallic_samples: list[float] = []
    roughness_samples: list[float] = []

    for geom in geometries:
        visual = getattr(geom, "visual", None)
        mat = getattr(visual, "material", None)
        if mat is None:
            continue
        base = getattr(mat, "baseColorTexture", None)
        if base is not None:
            maps["albedo"] = {"present": True, "resolution": list(base.size)}
        packed = getattr(mat, "metallicRoughnessTexture", None)
        if packed is not None:
            maps["roughness"] = {"present": True, "resolution": list(packed.size)}
            maps["metallic"] = {"present": True, "resolution": list(packed.size)}
            arr = np.asarray(packed.convert("RGBA"), dtype=np.float64) / 255.0
            roughness_samples.append(float(arr[..., 1].mean()))
            metallic_samples.append(float(arr[..., 2].mean()))
        else:
            if getattr(mat, "roughnessFactor", None) is not None:
                roughness_samples.append(float(mat.roughnessFactor))
            if getattr(mat, "metallicFactor", None) is not None:
                metallic_samples.append(float(mat.metallicFactor))
        normal = getattr(mat, "normalTexture", None)
        if normal is not None:
            maps["normal"] = {"present": True, "resolution": list(normal.size)}
        occlusion = getattr(mat, "occlusionTexture", None)
        if occlusion is not None:
            maps["ao"] = {"present": True, "resolution": list(occlusion.size)}

    metallic = float(np.mean(metallic_samples)) if metallic_samples else None
    roughness = float(np.mean(roughness_samples)) if roughness_samples else None
    return maps, metallic, roughness


def aspect_deviation(
    measured_extents: list[float] | tuple[float, ...] | np.ndarray,
    card_extents: list[float] | tuple[float, ...] | np.ndarray,
) -> np.ndarray | None:
    """Per-axis deviation of the NORMALIZED extent triples (§4.3/§4.4 S1).
    The mesh is not yet scaled, so ratio structure — not absolute size —
    is the only honest comparison. None when either triple is degenerate.
    Shared by analyse (gate) and conform (refusal) so the two can't drift."""
    measured = np.asarray(measured_extents, dtype=np.float64)
    card = np.asarray(card_extents, dtype=np.float64)
    if measured.size != 3 or card.size != 3:
        return None
    if measured.max() <= 0 or card.max() <= 0:
        return None
    m_norm = measured / measured.max()
    c_norm = card / card.max()
    return np.abs(m_norm - c_norm) / c_norm


def analyse_neural_mesh(
    glb_path: str | Path,
    job_card: JobCard,
    declared_fabric: bool = False,
    aspect_tolerance: float = ASPECT_TOLERANCE,
) -> NeuralAnalyseReport:
    """Run the §4.3 measured-fact table on one generated neural GLB
    against its job card. Pure function of (file, card, flags)."""
    path = Path(glb_path)
    mesh = _world_mesh(path)
    if mesh is None or len(mesh.faces) == 0:
        raise ValueError(f"no mesh geometry in {path}")

    merged = _position_merged(mesh)
    triangles = int(len(mesh.faces))
    bodies = int(getattr(mesh, "body_count", 1) or 1)
    extents = [float(v) for v in mesh.extents]
    maps, metallic, roughness = _scan_materials(path)

    report = NeuralAnalyseReport(
        glb_path=str(path),
        triangles=triangles,
        vertices=int(len(mesh.vertices)),
        extents_m=extents,
        bodies=bodies,
        maps=maps,
        metallic=metallic,
        roughness=roughness,
    )

    # 1. Triangles vs the card's ceiling (None = no ceiling known → record).
    ceiling = job_card.effective_polycount_ceiling()
    if ceiling is not None:
        report.checks.append({
            "name": "triangles",
            "gating": True,
            "passed": triangles <= ceiling,
            "value": triangles,
            "threshold": ceiling,
            "note": "JobCard effective polycount ceiling "
                    f"({job_card.effective_polycount_semantics()})",
        })
    else:
        report.checks.append({
            "name": "triangles",
            "gating": False,
            "passed": True,
            "value": triangles,
            "threshold": None,
            "note": "no ceiling on the card — recorded, not gated",
        })

    # 2. Open edges after position merge.
    open_edges = _open_edge_count(merged)
    report.checks.append({
        "name": "open_edges_after_position_merge",
        "gating": True,
        "passed": open_edges == 0,
        "value": open_edges,
        "threshold": 0,
        "note": "§3.5: UV-seam duplicates merged by position before counting",
    })

    # 3. Bodies — record, preferred 1 (conform's voxel remesh consolidates).
    report.checks.append({
        "name": "bodies",
        "gating": False,
        "passed": True,
        "value": bodies,
        "threshold": "1 preferred",
        "note": "nested shells consolidate at conform (voxel remesh); recorded",
    })

    # 4. Aspect ratio vs the card — the S1 check. Absolute, not a proxy:
    # normalized extent triples compared per axis (the mesh is not yet
    # scaled, so ratio structure is the only honest comparison).
    expected = job_card.expected_bounds_m()
    card = np.array([expected["x"], expected["y"], expected["z"]], dtype=np.float64)
    measured = np.array(extents, dtype=np.float64)
    deviation = aspect_deviation(measured, card)
    if deviation is not None:
        worst = float(deviation.max())
        m_norm = measured / measured.max()
        c_norm = card / card.max()
        report.checks.append({
            "name": "aspect_ratio",
            "gating": True,
            "passed": worst <= aspect_tolerance,
            "value": {
                "measured_ratio": [round(float(v), 4) for v in m_norm],
                "card_ratio": [round(float(v), 4) for v in c_norm],
                "per_axis_deviation": [round(float(v), 4) for v in deviation],
            },
            "threshold": aspect_tolerance,
            "note": "worst per-axis deviation of normalized extents "
                    f"({worst:.1%}); >{aspect_tolerance:.0%} → refuse (S1)",
        })
    else:
        report.checks.append({
            "name": "aspect_ratio",
            "gating": True,
            "passed": False,
            "value": None,
            "threshold": aspect_tolerance,
            "note": "degenerate extents — cannot compare",
        })

    # 5. n-gons — a GLB is triangles by construction (§3.3); the real
    # n-gon gate applies to the conformed quad-clean FBX.
    n_gons = 0  # trimesh faces are triangle lists
    report.checks.append({
        "name": "n_gons",
        "gating": False,
        "passed": True,
        "value": n_gons,
        "threshold": 0,
        "note": "GLB is triangulated at export (§3.3); the gate that matters "
                "runs on the conformed FBX from the quad-clean scene",
    })

    # 6. Metallic on declared-fabric surfaces (§3.6: measured 34% defect).
    if declared_fabric:
        report.checks.append({
            "name": "metallic_fabric",
            "gating": metallic is not None,
            "passed": metallic is not None and metallic <= METALLIC_FABRIC_MAX,
            "value": metallic,
            "threshold": METALLIC_FABRIC_MAX,
            "note": "declared fabric must read ~0 metallic "
                    "(measured TRELLIS defect: 34%)",
        })
    else:
        report.checks.append({
            "name": "metallic_fabric",
            "gating": False,
            "passed": True,
            "value": metallic,
            "threshold": METALLIC_FABRIC_MAX,
            "note": "surfaces not declared fabric — metallic recorded, not gated",
        })

    # 7. The 5 maps — recorded with resolution (§6 reporting discipline).
    #    Normal + AO absent is EXPECTED from TRELLIS (§3.2); the conform
    #    HP→LP bake supplies them. Nothing here gates.
    report.checks.append({
        "name": "maps_present",
        "gating": False,
        "passed": True,
        "value": {k: v["present"] for k, v in maps.items()},
        "threshold": "record",
        "note": "normal + AO come from the conform bake (§3.2), so their "
                "absence here is expected, not a defect",
    })

    return report
