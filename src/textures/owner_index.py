"""Owner-supplied texture library — the drop-directory index (Phase 4).

The owner drops texture sets into a directory (convention:
``input/textures/owner/<surface>/``); this module scans it and writes a
deterministic ``index.json`` the brain selects from. One sub-directory per
surface, canonical map files inside — the SAME names the delivery harness
consumes (``albedo.png`` / ``roughness.png`` / ``height.png``, with the
.jpg / disp.png aliases the harness's ``_find`` accepts), because a
selected surface's directory path goes STRAIGHT into the spec material's
``texture_dir`` (PBRMaterial → harness ``_textured_material``'s triplanar
BOX projection). No copying, no renaming: the index records paths, the
existing build path does the consuming.

Selection contract (owner order, Phase 4): if a required surface has no
supplied set, compose from CC0 scans as the template layer already does
(``compose.py``). Textures are NEVER diffusion-generated — diffusion output
does not tile seamlessly and cannot produce a true normal map.

Measured facts per map (the brain is text-only — numbers, not eyeballs):
pixel resolution, sha256, and ``edge_wrap_delta_mean`` — the mean absolute
per-channel difference (0–255 scale) between opposite image edges. 0 means
the edges' VALUES continue across the tile boundary (what a scan of a real
surface should show); a large value on the albedo means visible seams at
every tile boundary. CAVEAT: high-frequency patterns (e.g. a 1px checker)
read high even when geometrically tileable — the number measures value
continuity, not tiling correctness. The number is recorded for every map;
judgment stays with the brain.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

# Canonical map slots. The first three are what the harness's textured
# material consumes today; normal/metallic/ao are recorded for completeness
# (and future consumption) but do NOT make a directory buildable on their
# own — a texture_dir the harness cannot feed produces a flat material.
CANONICAL_MAP_SLOTS: dict[str, tuple[str, ...]] = {
    "albedo": ("albedo.png", "albedo.jpg"),
    "roughness": ("roughness.png", "roughness.jpg"),
    "height": ("height.png", "height.jpg", "disp.png"),
    "normal": ("normal.png", "normal.jpg"),
    "metallic": ("metallic.png", "metallic.jpg"),
    "ao": ("ao.png", "ao.jpg"),
}
# Slots whose presence makes a directory usable as a PBRMaterial.texture_dir.
HARNESS_CONSUMED_SLOTS = ("albedo", "roughness", "height")

INDEX_NAME = "index.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _edge_wrap_delta_mean(img: Image.Image) -> float | None:
    """Mean absolute per-channel difference between opposite edges (0–255):
    0 = edge values continue across the tile boundary (value continuity —
    see the module docstring caveat about high-frequency patterns). None
    when the image is too small to wrap (1px side)."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    if w < 2 or h < 2:
        return None
    data = rgb.tobytes()  # row-major, 3 bytes per pixel
    stride = w * 3
    diffs = 0
    for row in range(h):  # left column vs right column
        base = row * stride
        for c in range(3):
            diffs += abs(data[base + c] - data[base + stride - 3 + c])
    top, bottom = data[:stride], data[(h - 1) * stride:]
    diffs += sum(abs(a - b) for a, b in zip(top, bottom))
    count = 3 * (h + w)
    return round(diffs / count, 3) if count else None


def _map_facts(path: Path) -> dict[str, Any]:
    with Image.open(path) as img:
        size = img.size
        wrap = _edge_wrap_delta_mean(img)
    return {
        "file": path.name,
        "resolution_px": [size[0], size[1]],
        "sha256": _sha256(path),
        "edge_wrap_delta_mean": wrap,
    }


def index_owner_textures(root: Path | str, *, write: bool = True) -> dict[str, Any]:
    """Scan an owner drop-directory and return (and by default write) its
    index. See the module docstring for the layout and the selection
    contract. Deterministic apart from ``generated_utc``: the same files
    always produce the same surfaces/maps/sha256 values, in sorted order.

    Directories with no canonical map file are recorded under ``skipped``
    (name + files + reason), never silently dropped — the brain must be
    able to see that a drop was noticed but unusable."""
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"owner texture root not found: {root}")

    surfaces: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    root_files: list[str] = []

    for entry in sorted(root.iterdir()):
        if entry.name == INDEX_NAME:
            continue  # our own output from a previous run
        if not entry.is_dir():
            root_files.append(entry.name)
            continue
        if entry.name.startswith("."):
            skipped.append({"name": entry.name, "reason": "hidden directory"})
            continue

        maps: dict[str, Any] = {}
        mapped_files: set[str] = set()
        for slot, names in CANONICAL_MAP_SLOTS.items():
            for n in names:
                candidate = entry / n
                if candidate.is_file():
                    maps[slot] = _map_facts(candidate)
                    mapped_files.add(n)
                    break

        other_files = sorted(
            p.name for p in entry.iterdir()
            if p.is_file() and p.name not in mapped_files
        )
        if not any(slot in maps for slot in HARNESS_CONSUMED_SLOTS):
            skipped.append({
                "name": entry.name,
                "reason": "no canonical map the harness consumes "
                          "(albedo/roughness/height with .png/.jpg/disp names)",
                "files": sorted(p.name for p in entry.iterdir() if p.is_file()),
            })
            continue

        surfaces.append({
            "name": entry.name,
            "path": str(entry.resolve()),
            "maps": maps,
            "other_files": other_files,
            "min_resolution_px": min(
                m["resolution_px"][0] for m in maps.values()),
        })

    index: dict[str, Any] = {
        "schema": "threed-owner-textures/1",
        "root": str(root.resolve()),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "surface_count": len(surfaces),
        "surfaces": surfaces,
        "skipped": skipped,
        "root_files": root_files,
        "selection_contract": (
            "Put a surface's path into the spec material's texture_dir "
            "(PBRMaterial; the harness applies triplanar BOX projection at "
            "texture_size metres/tile). If no supplied surface fits, compose "
            "from CC0 scans (src/textures/compose.py). NEVER generate a "
            "texture with a diffusion model — it does not tile seamlessly "
            "and cannot produce a true normal map (owner order, Phase 4)."
        ),
    }
    if write:
        (root / INDEX_NAME).write_text(
            json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index
