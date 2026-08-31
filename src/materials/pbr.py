"""PBR material preset library.

Self-contained on purpose: plain values only, no imports from the spec package,
so `spec.resolver` can consume presets without a circular dependency. The
harness receives these as a flat dict and builds Principled BSDF materials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MaterialPreset:
    name: str
    category: str
    description: str
    color: list[float] = field(default_factory=lambda: [0.75, 0.75, 0.75])
    roughness: float = 0.5
    metallic: float = 0.0
    transmission: float = 0.0
    procedural_type: str | None = None


PRESET_LIBRARY: dict[str, MaterialPreset] = {
    "oak_wood": MaterialPreset(
        name="oak_wood",
        category="wood",
        description="Warm natural oak wood",
        procedural_type="wood",
        color=[0.65, 0.45, 0.28],
        roughness=0.6,
    ),
    "walnut_wood": MaterialPreset(
        name="walnut_wood",
        category="wood",
        description="Deep dark walnut wood",
        procedural_type="wood",
        color=[0.28, 0.18, 0.12],
        roughness=0.55,
    ),
    "brushed_steel": MaterialPreset(
        name="brushed_steel",
        category="metal",
        description="Industrial brushed stainless steel",
        procedural_type="metal",
        color=[0.82, 0.82, 0.84],
        roughness=0.35,
        metallic=0.95,
    ),
    "chrome": MaterialPreset(
        name="chrome",
        category="metal",
        description="High polish mirror chrome",
        procedural_type="metal",
        color=[0.95, 0.95, 0.96],
        roughness=0.05,
        metallic=1.0,
    ),
    "gold": MaterialPreset(
        name="gold",
        category="metal",
        description="Polished 24k yellow gold",
        procedural_type="metal",
        color=[1.0, 0.78, 0.28],
        roughness=0.15,
        metallic=1.0,
    ),
    "matte_black_plastic": MaterialPreset(
        name="matte_black_plastic",
        category="plastic",
        description="Sleek matte black engineering polymer",
        procedural_type="plastic",
        color=[0.05, 0.05, 0.05],
        roughness=0.7,
    ),
    "white_ceramic": MaterialPreset(
        name="white_ceramic",
        category="ceramic",
        description="Glossy porcelain / glazed ceramic",
        procedural_type="ceramic",
        color=[0.94, 0.94, 0.94],
        roughness=0.12,
    ),
    "leather_black": MaterialPreset(
        name="leather_black",
        category="fabric",
        description="Premium black upholstery leather",
        procedural_type="leather",
        color=[0.08, 0.08, 0.08],
        roughness=0.6,
    ),
    "leather_brown": MaterialPreset(
        name="leather_brown",
        category="fabric",
        description="Vintage saddle brown leather",
        procedural_type="leather",
        color=[0.38, 0.22, 0.12],
        roughness=0.65,
    ),
    "velvet_fabric": MaterialPreset(
        name="velvet_fabric",
        category="fabric",
        description="Plush velvet upholstery",
        procedural_type="fabric",
        color=[0.2, 0.35, 0.55],
        roughness=0.85,
    ),
    "frosted_glass": MaterialPreset(
        name="frosted_glass",
        category="glass",
        description="Semi-translucent frosted glass",
        procedural_type="glass",
        color=[0.92, 0.96, 0.96],
        roughness=0.35,
        transmission=0.88,
    ),
    "white_marble": MaterialPreset(
        name="white_marble",
        category="stone",
        description="Carrara white marble",
        procedural_type="marble",
        color=[0.92, 0.92, 0.93],
        roughness=0.2,
    ),
}


def get_preset_values(name: str) -> dict[str, Any]:
    """Flat PBR values for a preset name (or a neutral default)."""
    key = name.lower().replace(" ", "_").replace("-", "_")
    preset = PRESET_LIBRARY.get(key)
    if preset is None:
        return {
            "color": [0.75, 0.75, 0.75],
            "roughness": 0.5,
            "metallic": 0.0,
            "transmission": 0.0,
        }
    return {
        "color": list(preset.color),
        "roughness": preset.roughness,
        "metallic": preset.metallic,
        "transmission": preset.transmission,
    }


def list_material_presets() -> list[dict[str, Any]]:
    """List all available material presets with categories and parameters."""
    return [
        {
            "name": p.name,
            "category": p.category,
            "description": p.description,
            "color": p.color,
            "roughness": p.roughness,
            "metallic": p.metallic,
            "transmission": p.transmission,
        }
        for p in PRESET_LIBRARY.values()
    ]
