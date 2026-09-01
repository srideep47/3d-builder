"""ObjectSpec v2 resolver — converts an ObjectSpec into Blender harness payloads.

Responsibilities:
- Convert every length from the spec's unit to meters (the harness is meters-only).
- Resolve material presets to concrete flat PBR values (explicit fields win).
- Pass through modifiers, constraints, and per-method fields.
"""

from __future__ import annotations

from typing import Any

from ..materials.pbr import get_preset_values
from .schema import ObjectSpec, PBRMaterial, PartSpec, Unit


def _to_meters(values: list[float], unit: Unit) -> list[float]:
    return [unit.to_meters(float(v)) for v in values]


def _resolve_material(mat: PBRMaterial) -> dict[str, Any]:
    """Merge a material spec with its preset. Only explicitly-set fields override
    the preset's values (via pydantic's model_fields_set)."""
    if not mat.preset:
        return mat.model_dump()

    merged = get_preset_values(mat.preset)
    fields_set = mat.model_fields_set
    if "color" in fields_set:
        merged["color"] = [float(c) for c in mat.color]
    if "roughness" in fields_set:
        merged["roughness"] = float(mat.roughness)
    if "metallic" in fields_set:
        merged["metallic"] = float(mat.metallic)
    if "transmission" in fields_set:
        merged["transmission"] = float(mat.transmission)
    if "procedural" in fields_set:
        merged["procedural"] = bool(mat.procedural)
    # Texture-driven fields never come from a preset — pass explicitly-set
    # values through so preset + texture_dir combinations keep working.
    if "texture_dir" in fields_set:
        merged["texture_dir"] = mat.texture_dir
    if "texture_size" in fields_set:
        merged["texture_size"] = [float(v) for v in mat.texture_size]
    if "bump_strength" in fields_set:
        merged["bump_strength"] = float(mat.bump_strength)
    if "triplanar" in fields_set:
        merged["triplanar"] = bool(mat.triplanar)
    merged["name"] = mat.name if "name" in fields_set else f"mat_{mat.preset}"
    merged["preset"] = mat.preset
    return merged


def _resolve_part(part: PartSpec, unit: Unit) -> dict[str, Any]:
    p_dict: dict[str, Any] = {
        "name": part.name,
        "shape": part.shape.value,
        "dimensions": _to_meters(part.dimensions, unit),
        "position": _to_meters(part.position, unit),
        "rotation": [float(r) for r in part.rotation],
        "smooth_shade": part.smooth_shade,
    }

    if part.position_mode:
        p_dict["position_mode"] = part.position_mode
    if part.top_scale:
        p_dict["top_scale"] = [float(s) for s in part.top_scale]
    if part.profile_points:
        p_dict["profile_points"] = [
            [unit.to_meters(float(pt[0])), unit.to_meters(float(pt[1]))] for pt in part.profile_points
        ]
    if part.path_points:
        p_dict["path_points"] = [
            [unit.to_meters(float(c)) for c in pt[:3]] for pt in part.path_points
        ]
        if part.path_closed:
            p_dict["path_closed"] = True
    if part.caps != "ngon":
        p_dict["caps"] = part.caps
    if part.segments:
        p_dict["segments"] = int(part.segments)
    if part.method.value != "parametric":
        p_dict["method"] = part.method.value
    if part.image_crop:
        p_dict["image_crop"] = part.image_crop
    if part.mesh_path:
        p_dict["mesh_path"] = part.mesh_path
    if part.target_size:
        p_dict["target_size"] = _to_meters(part.target_size, unit)
    if part.code:
        p_dict["code"] = part.code

    if part.detail:
        d = part.detail
        detail: dict[str, Any] = {}
        if d.bevel_width is not None:
            detail["bevel_width"] = unit.to_meters(d.bevel_width)
        if d.subdivision_levels is not None:
            detail["subdivision_levels"] = int(d.subdivision_levels)
        if d.displacement:
            disp = d.displacement
            detail["displacement"] = {
                "pattern": disp.pattern,
                "amplitude_m": unit.to_meters(disp.amplitude),
                "frequency": float(disp.frequency),
                "frequency_y": float(disp.frequency_y) if disp.frequency_y is not None else None,
                "axis": disp.axis,
                "seed": int(disp.seed),
                "exponent": float(disp.exponent),
                "restrict": disp.restrict,
            }
        p_dict["detail"] = detail

    if part.material:
        p_dict["material"] = _resolve_material(part.material)

    if part.modifiers:
        mods: dict[str, Any] = {}
        if part.modifiers.bevel:
            mods["bevel"] = {
                "width": unit.to_meters(part.modifiers.bevel.width),
                "segments": int(part.modifiers.bevel.segments),
            }
        if part.modifiers.subdivision:
            mods["subdivision"] = {"levels": int(part.modifiers.subdivision.levels)}
        if part.modifiers.radial_array:
            mods["radial_array"] = {
                "count": int(part.modifiers.radial_array.count),
                "axis": part.modifiers.radial_array.axis,
                "center": _to_meters(part.modifiers.radial_array.center, unit),
            }
        if part.modifiers.linear_array:
            mods["linear_array"] = {
                "count": int(part.modifiers.linear_array.count),
                "direction": [float(d) for d in part.modifiers.linear_array.direction],
                "spacing": unit.to_meters(part.modifiers.linear_array.spacing),
            }
        if part.modifiers.mirror:
            mods["mirror"] = {"axis": part.modifiers.mirror.axis}
        if part.modifiers.boolean:
            mods["boolean"] = {
                "operation": part.modifiers.boolean.operation,
                "target_part": part.modifiers.boolean.target_part,
            }
        p_dict["modifiers"] = mods

    return p_dict


def resolve_spec_to_build_params(spec: ObjectSpec, output_glb_path: str | None = None) -> dict[str, Any]:
    """Serialize an ObjectSpec into the JSON payload for harness op 'build_from_spec'."""
    return {
        "spec": {
            "name": spec.name,
            "parts": [_resolve_part(part, spec.units) for part in spec.parts],
            "constraints": [c.model_dump() for c in spec.constraints],
        },
        "output_path": output_glb_path,
        "generate_uvs": True,
        "center_origin_bottom": True,
    }
