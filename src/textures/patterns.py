"""Procedural fabric-pattern generators (GLM_BRIEF §7 T4 surface sources).

Every generator is PURE MATH over normalized tile coordinates (u, v) ∈ [0, 1]²
and is TILEABLE by construction (integer cell counts, half-cell offsets that
wrap exactly) — triplanar materials repeat these tiles seamlessly in metres.

Why generated PNGs instead of Blender node trees: the pattern math is
deterministic and unit-testable WITHOUT a Blender process, it bakes stably
through the emission/bump channels, and the same functions can be re-run at
any resolution. The material NODE setup that consumes them (image textures
with box mapping + height→bump) lives in the harness.

Mask conventions: every generator returns float32 arrays shaped (res, res)
with values in [0, 1], row 0 = v=0. Composition (tint / overlay / modulate)
happens in numpy; PNG IO at the edges only.
"""

from __future__ import annotations

import numpy as np


def _grid(res: int) -> tuple[np.ndarray, np.ndarray]:
    """Normalized tile coords. Row 0 = v = 0 (bottom), matching how the
    pipeline samples baked maps (v up, PNG rows top-first handled at IO)."""
    u = (np.arange(res) + 0.5) / res
    v = (np.arange(res) + 0.5) / res
    return np.meshgrid(u, v, indexing="ij")  # uu[i, j] = u_i, vv[i, j] = v_j


def oval_holes(res: int, cells_x: int, cells_y: int, hole_rx: float = 0.32,
               hole_ry: float = 0.22) -> np.ndarray:
    """Running-bond grid of elliptical holes — spacer/air-mesh look
    (GLM_BRIEF §5.2 band 3: "perforated, honeycomb/oval-hole pattern").

    Odd rows are offset by half a cell (running bond), which reads as a
    honeycomb at small scale. `hole_r*` are ellipse radii as a fraction of
    the cell size. Returns 1 inside holes, 0 on the land between them.

    `cells_y` must be EVEN: row parity (which rows carry the half-cell
    offset) only wraps when the row count is even, exactly like herringbone
    columns. The v-neighbour search is by ROW INDEX — a raw mod-1 wrap in
    row units erases row parity and stamps unoffset centres into offset
    rows, doubling the hole coverage (found numerically: 0.395 vs the
    analytic π·rx·ry = 0.221).
    """
    if cells_y % 2:
        raise ValueError(f"oval_holes cells_y must be even (got {cells_y}) — "
                         "odd row counts break running-bond tiling")
    uu, vv = _grid(res)
    mask = np.zeros((res, res), dtype=np.float32)
    rx = max(hole_rx, 1e-3)
    ry = max(hole_ry, 1e-3)
    row_f = vv * cells_y - 0.5  # fractional row index (row r's centre at r + 0.5)
    r0 = np.floor(row_f + 0.5).astype(np.int64)
    for dr in (-1, 0, 1):
        r = r0 + dr  # may hit -1 / cells_y at the seams; parity is still correct
        cy = (r + 0.5) / cells_y
        row_off = 0.5 if (r % 2 != 0).any() else 0.0
        dv = (vv - cy) * cells_y  # within ±1.5 rows of the nearest — no wrap
        for i in (-1, 0, 1):
            cx = (i + row_off + 0.5) / cells_x
            du = (uu - cx) * cells_x
            du = du - np.round(du)  # u-wrap is parity-free within a row
            rr = np.sqrt((du / rx) ** 2 + (dv / ry) ** 2)
            mask = np.maximum(mask, (rr < 1.0).astype(np.float32))
    return mask


def herringbone(res: int, columns: int = 12, stripes_per_column: int = 6,
                stripe_fraction: float = 0.5, groove_width: float = 0.12
                ) -> dict[str, np.ndarray]:
    """Herringbone / diagonal-twill weave for binding tape (GLM_BRIEF §5.2
    bands 2/4/6: "visible herringbone / diagonal twill weave").

    Columns of 45° stripes whose direction alternates per column, with a
    recessed groove at each column boundary (the weave's channel). Returns
    {"stripe": 1 on the raised yarn, "groove": 1 inside the boundary grooves}.
    `columns` must be even so column parity wraps; `stripes_per_column` is an
    integer so the diagonals wrap in v.
    """
    if columns % 2:
        raise ValueError(f"herringbone columns must be even (got {columns}) — "
                         "odd counts break tiling (column parity must wrap)")
    uu, vv = _grid(res)
    x = uu * columns
    col = np.floor(x).astype(np.int64)
    lx = x - col
    direction = np.where(col % 2 == 0, 1.0, -1.0)
    phase = (lx + direction * vv * stripes_per_column) % 1.0
    stripe = (phase < stripe_fraction).astype(np.float32)
    groove = (np.minimum(lx, 1.0 - lx) < groove_width).astype(np.float32)
    return {"stripe": stripe, "groove": groove}


def chevron(res: int, pitch: int = 8, thickness: float = 0.035,
            amplitude: float = 0.16) -> np.ndarray:
    """Zigzag print lines — the faint grey chevron micro-print woven into
    the white knit of the pillowtop (GLM_BRIEF §5.2 band 1). Returns 1 on
    the print line. `pitch` zigzags per tile (integer ⇒ tileable)."""
    uu, vv = _grid(res)
    tri = 2.0 * np.abs(np.mod(uu * pitch, 1.0) - 0.5) - 0.5  # ±0.5 triangle
    path = 0.5 + amplitude * tri * 2.0
    dv = np.abs(vv - path)
    dv = np.minimum(dv, 1.0 - dv)  # wrap-aware: lines crossing the v seam
    return (dv < thickness / 2.0).astype(np.float32)


# ── composition ops (numpy; colour in linear-ish 0..1 space) ────────────────


def tint(albedo: np.ndarray, color: tuple[float, float, float]) -> np.ndarray:
    """Force an albedo's colour family while keeping the scan's structure:
    per-channel rescale so the mean matches `color` (structure = deviations
    from the mean, preserved exactly)."""
    out = np.asarray(albedo, dtype=np.float32).copy()
    if out.ndim == 2:
        out = np.stack([out] * 3, axis=-1)
    mean = out.reshape(-1, 3).mean(axis=0)
    scale = np.array(color, dtype=np.float32) / np.maximum(mean, 1e-4)
    out = np.clip(out * scale, 0.0, 1.0)
    return out


def overlay(albedo: np.ndarray, mask: np.ndarray,
            color: tuple[float, float, float], opacity: float) -> np.ndarray:
    """Print `color` onto `albedo` where `mask` is 1, at `opacity`."""
    out = np.asarray(albedo, dtype=np.float32).copy()
    if out.ndim == 2:
        out = np.stack([out] * 3, axis=-1)
    m = (np.asarray(mask, dtype=np.float32) * float(opacity))[..., None]
    col = np.array(color, dtype=np.float32)
    return np.clip(out * (1.0 - m) + col * m, 0.0, 1.0)


def modulate(channel: np.ndarray, mask: np.ndarray, delta: float) -> np.ndarray:
    """Shift a scalar channel (roughness/height) by `delta` where mask is 1."""
    out = np.asarray(channel, dtype=np.float32).copy()
    m = np.asarray(mask, dtype=np.float32)
    return np.clip(out + m * float(delta), 0.0, 1.0)


def height_field(res: int, base: float = 0.5) -> np.ndarray:
    """Flat neutral height field (bump mid-level)."""
    return np.full((res, res), float(base), dtype=np.float32)


# ── PNG IO ──────────────────────────────────────────────────────────────────


def load_rgb(path, size: int | None = None) -> np.ndarray:
    """Load an RGB PNG in generator convention: row 0 = v = 0 (bottom).
    PNG files are top-first, so the array is flipped on load — the exact
    inverse of save_png (roundtrip is identity; compose preserves scan
    orientation)."""
    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("RGB")
        if size is not None and img.size != (size, size):
            img = img.resize((size, size), Image.BILINEAR)
        return np.flipud(np.asarray(img, dtype=np.float32)) / 255.0


def load_gray(path, size: int | None = None) -> np.ndarray:
    """Load a grayscale PNG in generator convention (row 0 = v = 0 bottom)."""
    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("L")
        if size is not None and img.size != (size, size):
            img = img.resize((size, size), Image.BILINEAR)
        return np.flipud(np.asarray(img, dtype=np.float32)) / 255.0


def save_png(array: np.ndarray, path) -> None:
    """Save an HxW or HxWx3 float array as PNG. Row 0 of the array is v=0
    (bottom); PNG rows are top-first, so the array is flipped on save —
    matching how the bake tests sample baked maps."""
    from PIL import Image

    arr = np.flipud(np.clip(np.asarray(array), 0.0, 1.0))
    if arr.ndim == 2:
        img = Image.fromarray((arr * 255.0 + 0.5).astype(np.uint8), mode="L")
    else:
        img = Image.fromarray((arr * 255.0 + 0.5).astype(np.uint8), mode="RGB")
    img.save(path)
