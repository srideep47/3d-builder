"""Generate a product template's texture assets (GLM_BRIEF §7 T4).

Composes every surface recipe in templates/<product_class>.yaml into
assets/textures/<product_class>/<surface>/{albedo,roughness,height}.png
(CC0 scans + deterministic procedural patterns), and can generate the
placeholder brand-label decal for templates that declare one.

Deterministic: same template + same inputs -> same pixels (asserted by
tests/test_textures.py).

Usage:
    python scripts/gen_template_textures.py --template templates/mattress.yaml
    python scripts/gen_template_textures.py --template ... --placeholder-decal
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.spec.template import load_template  # noqa: E402
from src.textures.compose import compose_template  # noqa: E402
from src.textures.decal import generate_placeholder_decal  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", required=True)
    parser.add_argument("--placeholder-decal", action="store_true",
                        help="generate the obviously-fake stand-in label "
                             "(real one is an owner photo crop, §5.3)")
    args = parser.parse_args()

    template = load_template(args.template)
    dirs = compose_template(template)
    for d in dirs:
        print(f"[composed] {d}")

    if args.placeholder_decal and template.decal is not None:
        decal_dir = PROJECT_ROOT / template.decal.texture
        out = generate_placeholder_decal(decal_dir)
        print(f"[decal]    {out}  (PLACEHOLDER — magenta border; replace with "
              f"the owner's photo crop of the corner close-up, GLM_BRIEF §5.3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
