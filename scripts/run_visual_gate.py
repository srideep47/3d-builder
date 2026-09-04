"""Standalone advisory visual gate (Gemini) — evidence collector.

Runs the Gemini vision provider over a set of render PNGs with a targeted
inspection prompt and prints the verdict VERBATIM (raw response included),
for before/after defect verification per HANDOFF_GLM §3. This is NOT part
of the delivery pipeline: the in-loop advisory gate lives in
AgentLoop._run_visual_gate and must never gate a release.

Usage:
  python scripts/run_visual_gate.py <dir_or_png> [<dir_or_png> ...]
      [--refs <dir_or_png> ...]   also run the full render-vs-reference
                                  visual_verdict (reference set 1)
  python scripts/run_visual_gate.py --describe <dir_or_png> ...
      reference-decomposition mode instead of defect inspection

The API key comes from the environment (THREED_VLM_API_KEY, falling back
to GEMINI_API_KEY) — never from config or code.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai.vlm import get_vision_provider  # noqa: E402

# Render-only defect inspection: catches the two gross defects found by the
# reviewer (HANDOFF_GLM §2) without needing the reference photos.
INSPECT_PROMPT = (
    "You are a visual QA inspector for a 3D asset pipeline. These are studio "
    "renders (front/side/top/iso views) of a 3D mattress model built for a "
    "client delivery. The product is a pillowtop hybrid mattress: a domed "
    "quilted white top, a white perforated air-mesh band, a side border of "
    "alternating white knit and dark charcoal velvet bands, and THIN black "
    "binding tape wrapping the perimeter at the band boundaries.\n\n"
    "Inspect the renders carefully and answer with ONLY a JSON object with "
    "exactly these keys:\n"
    "{\n"
    '  "tape_edges": "describe the black tape strips at the band boundaries '
    '— do they hug the side surface like thin binding tape, or protrude '
    'like thick collars/flanges? estimate thickness as a fraction of '
    'mattress height",\n'
    '  "band_textures": "describe the side-face bands — do they read as '
    'coherent fabric (knit weave, perforated mesh, velvet), or as chaotic '
    'black-and-white blotches/static/garbage?",\n'
    '  "proportions": "one sentence on the overall silhouette (a tall tower '
    'is EXPECTED here if the render is deliberately built at 12x12x65 '
    'inches — note it neutrally)",\n'
    '  "other_issues": ["any other visible problems: missing parts, wrong '
    'placement, z-fighting, holes"],\n'
    '  "verdict": "PASS or FAIL"\n'
    "}\n"
    "verdict rule: FAIL only for gross visual defects (collar-like tapes, "
    "blotch/garbage textures, missing bands), not for fine pattern "
    "fidelity. Be concrete and honest; this is defect triage, not "
    "encouragement."
)


def _collect(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
                out.extend(sorted(path.glob(ext)))
        else:
            out.append(path)
    return [p for p in out if p.exists()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("images", nargs="+", help="render dir(s) or PNG path(s)")
    ap.add_argument("--refs", nargs="*", default=None,
                    help="reference dir(s)/image(s): also run the full "
                         "render-vs-reference visual_verdict")
    ap.add_argument("--describe", action="store_true",
                    help="reference-decomposition mode instead of defect "
                         "inspection")
    args = ap.parse_args()

    provider = get_vision_provider()
    if provider is None:
        print("[gate] no vision provider configured (config/ai.yaml "
              "vision.vlm) — nothing to do")
        return 2
    if not provider.is_available(recheck=True):
        print("[gate] vision provider configured but NOT reachable")
        return 2

    images = _collect(args.images)
    if not images:
        print("[gate] no existing images found in:", args.images)
        return 2
    print(f"[gate] provider: {type(provider).__name__} "
          f"(model: {getattr(provider, 'model', '?')})")
    print("[gate] images:")
    for p in images:
        print(f"       {p}  ({p.stat().st_size / 1024:.0f} KiB)")

    if args.describe:
        raw = provider.describe_reference_images(images)
        print("\n===== REFERENCE DESCRIPTION (verbatim) =====")
        print(raw)
        return 0

    print("\n===== DEFECT INSPECTION — RAW RESPONSE (verbatim) =====")
    raw = provider.chat_vision(INSPECT_PROMPT, images, max_tokens=2000)
    print(raw)

    if args.refs is not None:
        refs = _collect(args.refs)
        if not refs:
            print("\n[gate] --refs given but no reference images found — "
                  "skipping visual_verdict")
        else:
            renders = {k: str(p) for k, p in zip(
                ("front", "side", "top", "iso"), images[:4])}
            verdict = provider.visual_verdict(renders, refs)
            print("\n===== RENDER-VS-REFERENCE VERDICT (advisory) =====")
            import json
            print(json.dumps(verdict, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
