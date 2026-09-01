"""Compose delivery texture surfaces from CC0 scans + procedural patterns.

Reads the `textures:` recipes of a product template (SurfaceSpec) and writes
canonical map sets (albedo/roughness/height PNGs) into
assets/textures/<product_class>/<surface>/ — the exact layout the harness's
textured materials and the spec compiler expect.

Layer geometry params are declared in METRES (`cell_m`, `column_width_m`,
`pitch_m`, ...) and converted to per-tile integers here, so patterns scale
physically and stay tileable (integer cell counts, even counts where parity
must wrap). All output is deterministic: same inputs -> same pixels.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from ..spec.template import SurfaceSpec, TextureLayerSpec, TemplateSpec
from .patterns import (chevron, height_field, herringbone, load_gray,
                       load_rgb, modulate, oval_holes, overlay, save_png, tint)

SCAN_ROOT = Path(__file__).resolve().parents[2] / "input" / "textures" / "cc0"


def _even(n: int) -> int:
    return n if n % 2 == 0 else n + 1


def _layer_mask(layer: TextureLayerSpec, res: int, tile_m: float) -> dict[str, np.ndarray]:
    """Pattern masks for one layer, with metre params converted to per-tile
    integers. Returns {"mask": main mask, "groove": optional extra mask}."""
    p = dict(layer.params)
    kind = layer.kind
    if kind == "oval_holes":
        cell_m = float(p.get("cell_m", 0.006))
        cells_x = max(1, round(tile_m / cell_m))
        cells_y = _even(max(1, round(tile_m / cell_m)))
        return {"mask": oval_holes(res, cells_x, cells_y,
                                   float(p.get("hole_rx", 0.32)),
                                   float(p.get("hole_ry", 0.22)))}
    if kind == "herringbone":
        column_width_m = float(p.get("column_width_m", 0.003))
        stripe_pitch_m = float(p.get("stripe_pitch_m", 0.0025))
        columns = _even(max(2, round(tile_m / column_width_m)))
        stripes = max(1, round(tile_m / stripe_pitch_m))
        hb = herringbone(res, columns=columns, stripes_per_column=stripes,
                         stripe_fraction=float(p.get("stripe_fraction", 0.5)),
                         groove_width=float(p.get("groove_width", 0.12)))
        return {"mask": hb["stripe"], "groove": hb["groove"]}
    if kind == "chevron":
        pitch_m = float(p.get("pitch_m", 0.08))
        thickness_m = float(p.get("thickness_m", 0.004))
        amplitude_m = float(p.get("amplitude_m", 0.08))
        return {"mask": chevron(res, pitch=max(1, round(tile_m / pitch_m)),
                                thickness=thickness_m / tile_m,
                                amplitude=amplitude_m / tile_m)}
    raise ValueError(f"unknown pattern kind {kind!r}")


def _scan_surface_dirs(scan: str) -> tuple[Path, dict[str, Any]]:
    d = SCAN_ROOT / scan
    source_path = d / "SOURCE.json"
    if not source_path.exists():
        raise FileNotFoundError(
            f"CC0 scan '{scan}' not fetched: {d} missing. "
            f"Run: python scripts/fetch_cc0_textures.py --asset {scan}"
        )
    return d, json.loads(source_path.read_text(encoding="utf-8"))


def compose_surface(product_class: str, name: str, surface: SurfaceSpec,
                    out_root: Path | None = None) -> Path:
    """Compose one surface; returns the output dir. Writes albedo.png,
    roughness.png, height.png + a provenance manifest.json."""
    res = int(surface.resolution)
    out_dir = (out_root or (Path(__file__).resolve().parents[2] / "assets"
                            / "textures" / product_class)) / name
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"surface": name, "resolution": res,
                                "tile_m": surface.tile_m, "layers": []}

    if surface.base == "scan":
        scan_dir, source = _scan_surface_dirs(surface.scan)
        albedo = load_rgb(scan_dir / "albedo.png", size=res)
        roughness = load_gray(scan_dir / "roughness.png", size=res)
        height = load_gray(scan_dir / "height.png", size=res)
        manifest["scan"] = {
            "asset": surface.scan, "source": source.get("site"),
            "licence": source.get("licence"),
        }
        # EMPIRICAL: Poly Haven fabric disp maps can be flat placeholders
        # (knitted_fleece / velour_velvet disp = constant 1.0). AO carries
        # the micro-relief instead (cavity dark = low) — fall back to it and
        # record the substitution; never ship a silently-flat bump.
        if float(height.std()) < 1e-3:
            ao = load_gray(scan_dir / "ao.png", size=res)
            if float(ao.std()) > 1e-3:
                height = ao
                manifest["height_source"] = "ao (scan displacement map was flat)"
            else:
                manifest["height_source"] = "flat (no relief available — check scan)"
    else:
        base_col = surface.tint or [0.8, 0.8, 0.8]
        albedo = np.full((res, res, 3), base_col, dtype=np.float32)
        roughness = np.full((res, res), surface.roughness, dtype=np.float32)
        height = height_field(res)
        manifest["height_source"] = "flat base"

    if surface.base == "scan" and surface.tint:
        albedo = tint(albedo, tuple(surface.tint))

    for layer in surface.layers:
        masks = _layer_mask(layer, res, surface.tile_m)
        m = masks["mask"]
        if layer.color is not None and layer.opacity > 0:
            albedo = overlay(albedo, m, tuple(layer.color), layer.opacity)
        height = modulate(height, m, layer.height_delta)
        roughness = modulate(roughness, m, layer.roughness_delta)
        groove = masks.get("groove")
        if groove is not None:
            gd = float(layer.params.get("groove_height_delta", -0.08))
            gdr = float(layer.params.get("groove_roughness_delta", 0.02))
            height = modulate(height, groove, gd)
            roughness = modulate(roughness, groove, gdr)
        manifest["layers"].append({"kind": layer.kind, "params": layer.params})

    if surface.rotate_deg:
        # quarter-turn the composed maps (square tiles stay tileable): turns
        # a scan's directional nap so it renders the right way on walls
        k = surface.rotate_deg // 90 % 4
        albedo = np.rot90(albedo, k)
        roughness = np.rot90(roughness, k)
        height = np.rot90(height, k)
        manifest["rotate_deg"] = surface.rotate_deg

    save_png(albedo, out_dir / "albedo.png")
    save_png(roughness, out_dir / "roughness.png")
    save_png(height, out_dir / "height.png")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                           encoding="utf-8")
    return out_dir


def compose_template(template: TemplateSpec, out_root: Path | None = None) -> list[Path]:
    """Compose every surface in a template. Returns the output dirs."""
    return [compose_surface(template.product_class, name, surface, out_root)
            for name, surface in template.textures.items()]
