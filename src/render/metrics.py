"""Review-render quality metrics (Phase 8 item 2 — the absolute-contrast fix).

The §H lesson this module exists for: a ratio reaches 1.0 when both terms
go to zero. The round-4 FFT axis ratio read a healthy 0.87 while fill light
had flattened the crown quilt to 0.81 / 0.96 grey levels of absolute
contrast — the metric scored symmetric invisibility as success. Every
number produced here is therefore ABSOLUTE and carries its unit (grey
levels of 255), and the contrast floor gates on amplitude, never on a
ratio.

Two measurements:

- ``view_stats(image)`` — the balance and clipping checks, whole image:
  clipped fraction (>= 0.995), crushed fraction (<= 0.005), mean and
  percentile luminance, over OPAQUE pixels only (review renders use
  transparent film; the background is not fabric).
- ``measure_contrast_probe(image, region, cycles, band)`` — absolute
  grey-level amplitude of the periodic relief at an authored pitch, in a
  region of one rendered view. The caller (product template, rule 11)
  supplies the region as normalized image coordinates and the expected
  relief cycles across it; the analyzer detrends (removes the dome/vignette
  shading), Hann-windows, and reads the FFT fundamental in a search band
  around the expected pitch — plus a scanline peak-to-trough cross-check.
  ``amplitude`` is the cosine amplitude (HALF the peak-to-trough swing);
  the floor of 6 grey levels therefore means a 12-level visible swing.

Pure numpy + PIL, no Blender. The harness renders; this measures.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

# renders are made with transparent film — a pixel is "fabric" at half alpha
_OPAQUE_ALPHA = 0.5
# the round-4 clipping definition: >= 0.995 luminance is blown fabric
_CLIP_LEVEL = 0.995
_CRUSH_LEVEL = 0.005
# a probe region must be almost fully on-model to mean anything
_MIN_REGION_OPAQUE = 0.9


def load_luminance(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """Return (luminance 0..255, alpha 0..1) arrays for an image."""
    with Image.open(path) as img:
        rgba = np.asarray(img.convert("RGBA"), dtype=float)
    alpha = rgba[:, :, 3] / 255.0
    lum = (0.2126 * rgba[:, :, 0] + 0.7152 * rgba[:, :, 1]
           + 0.0722 * rgba[:, :, 2])
    return lum, alpha


def view_stats(image_path: str | Path) -> dict:
    """Whole-image balance + clipping evidence over opaque pixels."""
    lum, alpha = load_luminance(image_path)
    opaque = alpha > _OPAQUE_ALPHA
    if not opaque.any():
        return {"valid": False, "reason": "no opaque pixels"}
    vals = lum[opaque]
    return {
        "valid": True,
        "image": str(image_path),
        "opaque_fraction": round(float(opaque.mean()), 6),
        "clipped_fraction": round(float((vals >= _CLIP_LEVEL * 255).mean()), 6),
        "crushed_fraction": round(float((vals <= _CRUSH_LEVEL * 255).mean()), 6),
        "mean_luminance": round(float(vals.mean()), 2),
        "p5_luminance": round(float(np.percentile(vals, 5)), 2),
        "p95_luminance": round(float(np.percentile(vals, 95)), 2),
    }


def _box_blur(a: np.ndarray, kh: int, kw: int) -> np.ndarray:
    """Edge-clamped uniform blur via integral image (no scipy)."""
    pad_h, pad_w = kh // 2, kw // 2
    padded = np.pad(a, ((pad_h, pad_h), (pad_w, pad_w)), mode="edge")
    ii = np.pad(padded, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    h, w = a.shape
    s = (ii[kh:kh + h, kw:kw + w] - ii[0:h, kw:kw + w]
         - ii[kh:kh + h, 0:w] + ii[0:h, 0:w])
    return s / float(kh * kw)


def _peak_in_band(mag: np.ndarray, k_lo: int, k_hi: int,
                  cross_bins: int) -> tuple[float, float]:
    """Strongest spectral peak in bins [k_lo, k_hi] of one frequency axis,
    within ±cross_bins of the other axis' DC. mag is |rfft2| (rows = +fy
    only). Returns (interpolated magnitude, interpolated bin position)."""
    h, w = mag.shape
    best_mag, best_k = -1.0, k_lo
    for k in range(max(1, k_lo), min(k_hi, w - 2) + 1):
        m = mag[0:cross_bins + 1, k].max()
        if m > best_mag:
            best_mag, best_k = m, k
    if best_mag < 0:
        return 0.0, float(k_lo)
    # 3-point parabolic interpolation across neighbouring bins of the
    # peak row's strongest entry
    row = int(np.argmax(mag[0:cross_bins + 1, best_k]))
    y0 = mag[row, best_k - 1] if best_k >= 1 else mag[row, best_k]
    y1 = mag[row, best_k]
    y2 = mag[row, best_k + 1] if best_k + 1 < w else mag[row, best_k]
    denom = y0 - 2.0 * y1 + y2
    offset = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-12 else 0.0
    offset = float(np.clip(offset, -1.0, 1.0))
    interp = y1 - 0.25 * (y0 - y2) * offset
    return float(interp), float(best_k) + offset


def measure_contrast_probe(
    image_path: str | Path,
    region: tuple[float, float, float, float],
    cycles: tuple[float, float],
    band: tuple[float, float] = (0.6, 1.4),
    min_amplitude: float = 6.0,
    axes: str = "both",
) -> dict:
    """Absolute grey-level amplitude of periodic relief at an authored pitch.

    region: normalized image coordinates (x0, y0, x1, y1), y from the TOP
    (image convention), each in 0..1.
    cycles: expected relief cycles ACROSS THE REGION along x and y —
    authored by the product template (rule 11); the analyzer searches a
    band of band[0]..band[1] times the expectation, so framing slop is
    tolerated while unrelated periodicity (micro-prints at other pitches)
    is excluded.
    min_amplitude: the floor (grey levels; amplitude = half the
    peak-to-trough swing). The owner's Phase 8 suggestion: 6+.
    axes: which axes the floor gates on. "both" (default — a square quilt
    grid must read in BOTH directions; the round-4 defect was an axis
    flattened while the other carried the image), or "x"/"y" for
    deliberately one-directional relief (ribbing).

    Returns amplitude_x/y (cosine amplitude of the relief fundamental,
    grey levels), detected cycles, the scanline peak-to-trough cross-check,
    and pass/fail at the floor over the gated axes. A region that is not
    almost fully on-model refuses honestly (valid: False) rather than
    measuring background.

    The measurement is deliberately CONSERVATIVE: the detrend kernel
    (region/4 box blur) attenuates the relief fundamental by its sinc
    response — ~13% at 10 cycles across the region, ~0% at 20+. The
    amplitude floor is calibrated against THIS analyzer's output, so a
    pass means the true swing was at or above what the floor claims.
    """
    lum, alpha = load_luminance(image_path)
    h_img, w_img = lum.shape
    x0, y0, x1, y1 = region
    px0 = int(np.clip(round(x0 * w_img), 0, w_img - 2))
    py0 = int(np.clip(round(y0 * h_img), 0, h_img - 2))
    px1 = int(np.clip(round(x1 * w_img), px0 + 2, w_img))
    py1 = int(np.clip(round(y1 * h_img), py0 + 2, h_img))
    crop = lum[py0:py1, px0:px1]
    crop_a = alpha[py0:py1, px0:px1]
    opaque_fraction = float((crop_a > _OPAQUE_ALPHA).mean())
    out = {
        "image": str(image_path),
        "region_px": [px0, py0, px1, py1],
        "region_opaque_fraction": round(opaque_fraction, 4),
        "min_amplitude": float(min_amplitude),
        "axes": axes,
    }
    if opaque_fraction < _MIN_REGION_OPAQUE:
        out.update({"valid": False, "passed": False,
                    "reason": (f"region only {opaque_fraction:.0%} opaque — "
                               f"the probe is not on the model")})
        return out
    ch, cw = crop.shape
    if ch < 32 or cw < 32:
        out.update({"valid": False, "passed": False,
                    "reason": f"region too small ({cw}x{ch} px)"})
        return out

    # Detrend: remove the low-frequency dome/vignette shading (kernel ~ 1/4
    # of the region keeps everything >= ~5 cycles across it).
    kh = max(3, ch // 4) | 1
    kw = max(3, cw // 4) | 1
    detrended = crop - _box_blur(crop, kh, kw)

    # Scanline cross-check (no FFT): median over rows/columns of the
    # p95-p5 swing of the detrended field — absolute peak-to-trough.
    row_swings = np.percentile(detrended, 95, axis=1) - np.percentile(detrended, 5, axis=1)
    col_swings = np.percentile(detrended, 95, axis=0) - np.percentile(detrended, 5, axis=0)
    peak_to_trough = float(max(np.median(row_swings), np.median(col_swings)))

    # Hann-windowed rfft2; amplitude = 2*|F|/sum(window) at the peak.
    win = np.outer(np.hanning(ch), np.hanning(cw))
    wsum = float(win.sum())
    mag = np.abs(np.fft.rfft2(detrended * win))
    cx, cy = cycles
    lo, hi = band
    x_lo, x_hi = int(np.ceil(cx * lo)), int(np.floor(cx * hi))
    y_lo, y_hi = int(np.ceil(cy * lo)), int(np.floor(cy * hi))
    mag_x, kx = _peak_in_band(mag, x_lo, x_hi, cross_bins=2)
    # y-axis relief lives in rows fy (rfft2 keeps only +fy) at fx near DC
    mag_y_t = mag.T  # transpose: now "bins" are fy
    mag_y, ky = _peak_in_band(mag_y_t, y_lo, y_hi, cross_bins=2)
    amp_x = 2.0 * mag_x / wsum
    amp_y = 2.0 * mag_y / wsum
    if axes not in ("both", "x", "y"):
        raise ValueError(f"axes must be 'both', 'x' or 'y' (got {axes!r})")
    # The floor gates on the WEAKEST gated axis. Gating on the max let a
    # strong y carry a dead x past the floor (the §H defect, rotated 90°);
    # a ratio alone must never gate this, and neither may one axis stand
    # in for the other when the relief is supposed to read in both.
    if axes == "x":
        amplitude = amp_x
    elif axes == "y":
        amplitude = amp_y
    else:
        amplitude = min(amp_x, amp_y)

    out.update({
        "valid": True,
        "axes": axes,
        "amplitude_x": round(float(amp_x), 3),
        "amplitude_y": round(float(amp_y), 3),
        "amplitude": round(float(amplitude), 3),
        "detected_cycles_x": round(kx, 2),
        "detected_cycles_y": round(ky, 2),
        "peak_to_trough": round(peak_to_trough, 3),
        "passed": bool(amplitude >= min_amplitude),
    })
    return out
