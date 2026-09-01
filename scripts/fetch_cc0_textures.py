"""Fetch CC0 texture scans from Poly Haven into input/textures/cc0/.

GLM_BRIEF §7 T4 "surface sources": knit micro-weave and velvet bands must be
CC0 fabric SCANS (ambientCG / Poly Haven) — no marketplace licences, no
AI-generated fabric. Poly Haven publishes everything under CC0 and its API
exposes descriptive names, physical dimensions, and per-map download URLs.

Downloads are normalized to canonical file names so the material pipeline can
consume them without knowing the source:

    input/textures/cc0/<asset>/albedo.png    (from Diffuse jpg)
    input/textures/cc0/<asset>/height.png    (from Displacement png)
    input/textures/cc0/<asset>/roughness.png (from Rough jpg)
    input/textures/cc0/<asset>/ao.png        (from AO jpg)
    input/textures/cc0/<asset>/SOURCE.json   (url, licence, physical size)

The asset's real-world scan size (cm) is recorded in SOURCE.json — the
template uses it as the triplanar tile size so weave scale is physically
correct.

Usage:
    python scripts/fetch_cc0_textures.py            # fetch the default set
    python scripts/fetch_cc0_textures.py --asset velour_velvet
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = PROJECT_ROOT / "input" / "textures" / "cc0"

API_FILES = "https://api.polyhaven.com/files/{asset}"
API_ASSETS = "https://api.polyhaven.com/assets?type=textures"

# The default T4 set (mattress template, GLM_BRIEF §5.2):
#   knit_white  <- knitted_fleece   (knitted / fleece / wool weave structure)
#   velvet bands<- velour_velvet    (plush napped velvet structure)
# Both are tinted to the §5.2 colours at composition time — the scan
# contributes STRUCTURE only, so the red velvet scan is fine for charcoal
# bands after tinting. Asset choice is owner-reviewable via the renders.
DEFAULT_ASSETS = ("knitted_fleece", "velour_velvet")

# canonical <- Poly Haven map key (first available wins)
MAP_SOURCES = (
    ("albedo.png", "Diffuse", "jpg"),
    ("height.png", "Displacement", "png"),
    ("roughness.png", "Rough", "jpg"),
    ("ao.png", "AO", "jpg"),
)


# The CDN 403s the default Python User-Agent (empirical) — identify ourselves.
_HEADERS = {"User-Agent": "3d-builder-cc0-fetch/1.0 (pipeline asset fetcher)"}


def _http_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [cached] {dest.name}")
        return
    print(f"  [get]    {url}")
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = resp.read()
    dest.write_bytes(data)
    print(f"  [saved]  {dest} ({len(data) / 1e6:.1f} MB)")


def fetch_asset(asset_id: str, resolution: str = "2k") -> dict:
    files = _http_json(API_FILES.format(asset=asset_id))
    assets = _http_json(API_ASSETS)
    info = assets.get(asset_id, {})

    out_dir = OUT_ROOT / asset_id
    out_dir.mkdir(parents=True, exist_ok=True)

    dims_cm = info.get("dimensions") or [100.0, 100.0]
    source = {
        "asset_id": asset_id,
        "name": info.get("name", asset_id),
        "site": "https://polyhaven.com/a/" + asset_id,
        "licence": "CC0 (Poly Haven publishes all assets under CC0)",
        "physical_size_cm": [float(dims_cm[0]), float(dims_cm[1])],
        "resolution": resolution,
        "maps": {},
    }

    for canonical, map_key, ext in MAP_SOURCES:
        entry = files.get(map_key, {}).get(resolution, {}).get(ext)
        if not entry:
            print(f"  [skip]   {map_key} not available at {resolution}")
            continue
        dest = out_dir / canonical
        _download(entry["url"], dest)
        source["maps"][canonical] = {
            "url": entry["url"],
            "md5": entry.get("md5"),
            "size_bytes": entry.get("size"),
        }

    (out_dir / "SOURCE.json").write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    print(f"  [record] {out_dir / 'SOURCE.json'} (physical size {dims_cm[0]:.0f}x{dims_cm[1]:.0f} cm)")
    return source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", action="append", help="asset id (repeatable); default = T4 mattress set")
    parser.add_argument("--resolution", default="2k", help="2k (default) / 1k / 4k")
    args = parser.parse_args()

    assets = args.asset or list(DEFAULT_ASSETS)
    for asset in assets:
        print(f"== {asset}")
        try:
            fetch_asset(asset, args.resolution)
        except Exception as e:  # noqa: BLE001 — report and continue with the rest
            print(f"  [FAILED] {asset}: {e}", file=sys.stderr)
            return 1
    print("\nAll fetches complete. Sources recorded in input/textures/cc0/*/SOURCE.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
