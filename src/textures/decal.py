"""Placeholder brand-label decal (GLM_BRIEF §5.3).

The REAL label must be cropped from the corner close-up reference photo by
someone who can see it (the implementing agent is text-only — §5.2 note).
Until the owner supplies `albedo.png` in the decal dir, this generates an
obviously-fake stand-in with the §5.3 layout (NISIEN / PURE COMFORT /
"with body support" / blue icon with mattress glyph + three arrows /
"Perfect Night") and a magenta hairline border so the placeholder is
VISIBLE in every render and can never be mistaken for a deliverable
texture (owner rule: placeholders are never silent).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_FONT_CANDIDATES = {
    "serif": [r"C:\Windows\Fonts\timesbd.ttf", r"C:\Windows\Fonts\times.ttf"],
    "serif_small": [r"C:\Windows\Fonts\times.ttf"],
    "sans": [r"C:\Windows\Fonts\arial.ttf"],
    "italic": [r"C:\Windows\Fonts\ariali.ttf", r"C:\Windows\Fonts\timesi.ttf"],
}


def _font(kind: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES[kind]:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def generate_placeholder_decal(out_dir: str | Path, width: int = 512,
                               height: int = 1024) -> Path:
    """Write {out_dir}/albedo.png — the §5.3 layout on a black patch with a
    magenta placeholder border. Deterministic."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), (12, 12, 13))
    d = ImageDraw.Draw(img)

    # placeholder marker — magenta hairline, impossible to read as final art
    d.rectangle([2, 2, width - 3, height - 3], outline=(255, 0, 255), width=3)

    white = (235, 235, 232)
    grey = (200, 200, 198)

    # NISIEN — wide-tracked serif caps (§5.3)
    f_brand = _font("serif", int(height * 0.075))
    brand = "N I S I E N"
    w = d.textlength(brand, font=f_brand)
    d.text(((width - w) / 2, height * 0.10), brand, font=f_brand, fill=white)

    # PURE COMFORT — smaller caps
    f_sub = _font("serif_small", int(height * 0.030))
    sub = "P U R E   C O M F O R T"
    w = d.textlength(sub, font=f_sub)
    d.text(((width - w) / 2, height * 0.22), sub, font=f_sub, fill=grey)

    # "with body support" — small lowercase
    f_low = _font("serif_small", int(height * 0.026))
    low = "with body support"
    w = d.textlength(low, font=f_low)
    d.text(((width - w) / 2, height * 0.27), low, font=f_low, fill=grey)

    # blue rounded-square icon: white mattress glyph + three downward arrows
    icon_w, icon_h = int(width * 0.52), int(height * 0.20)
    ix = (width - icon_w) // 2
    iy = int(height * 0.36)
    d.rounded_rectangle([ix, iy, ix + icon_w, iy + icon_h], radius=icon_w // 8,
                        fill=(28, 78, 168))
    # mattress glyph: rounded slab inside the icon
    mx, my = ix + icon_w * 0.18, iy + icon_h * 0.30
    mw, mh = icon_w * 0.64, icon_h * 0.22
    d.rounded_rectangle([mx, my, mx + mw, my + mh], radius=int(mh * 0.4),
                        fill=white)
    # three downward arrows beneath the slab
    for k in range(3):
        ax = mx + mw * (0.2 + 0.3 * k)
        ay = my + mh + icon_h * 0.08
        al = icon_h * 0.16
        d.line([ax, ay, ax, ay + al], fill=white, width=max(3, int(icon_w * 0.02)))
        d.polygon([
            (ax - al * 0.22, ay + al * 0.75),
            (ax + al * 0.22, ay + al * 0.75),
            (ax, ay + al),
        ], fill=white)

    # "Perfect Night" — italic script (§5.3)
    f_ital = _font("italic", int(height * 0.034))
    ital = "Perfect Night"
    w = d.textlength(ital, font=f_ital)
    d.text(((width - w) / 2, height * 0.66), ital, font=f_ital, fill=grey)

    # placeholder note at the bottom — small, out of the readable hierarchy
    f_note = _font("sans", int(height * 0.020))
    note = "PLACEHOLDER - replace with photo crop (GLM_BRIEF 5.3)"
    w = d.textlength(note, font=f_note)
    d.text(((width - w) / 2, height * 0.93), note, font=f_note,
           fill=(255, 0, 255))

    out = out_dir / "albedo.png"
    img.save(out)
    return out
