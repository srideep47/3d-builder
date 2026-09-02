"""Phase 8 item 2: the absolute-contrast analyzer (the §H fix).

Round 4's defect: the FFT axis RATIO read a healthy 0.87 while fill light
had flattened the quilt to 0.81/0.96 grey levels — a ratio reaches 1.0
when both terms go to zero, so symmetric invisibility scored as success.
The probe gates on ABSOLUTE grey-level amplitude at the authored relief
pitch instead, and the floor must hold on the WEAKEST gated axis (a
strong y must never carry a dead x past the floor — the same defect
rotated 90°).

Pinned here on synthetic gratings (pure numpy + PIL, no Blender):
- known-amplitude recovery: a cosine grating of amplitude A grey levels
  measures back A within 5%, on each axis independently and additively
  (the quilt shading model: normal tilt superposes per-axis cosines);
- detected cycles land on the authored pitch;
- flat fields measure ~0 and FAIL the floor;
- RATIO-NEVER-GATES: same axis ratio 1.0, 2/2 grey levels fails while
  12/12 passes — only the absolute floor decides;
- per-axis gating: x dead + y strong FAILS on axes="both", passes on
  axes="y" (deliberately one-directional relief opts out explicitly);
- balance checks: clipped/crushed fractions over opaque pixels only;
- fail-closed: a mostly-transparent probe region refuses (valid=False,
  passed=False), never silently passes.
"""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.render.metrics import (load_luminance, measure_contrast_probe,
                                view_stats)

SIZE = 512
# the full-image probe region — y from the top, normalized
REGION = (0.0, 0.0, 1.0, 1.0)


def _grating(path: Path, amp_x: float = 0.0, amp_y: float = 0.0,
             cycles: float = 10.0, mean: float = 128.0,
             size: int = SIZE, opaque: bool = True) -> Path:
    """Additive per-axis cosine grating (the quilt shading model: normal
    tilt superposes the two axes' modulations), fully opaque or fully
    transparent. amp_x modulates along image columns (the image x axis),
    amp_y along rows."""
    n = np.arange(size)
    cos = np.cos(2 * np.pi * cycles * n / size)
    lum = (mean + amp_x * cos)[None, :] + (amp_y * cos)[:, None]
    a = np.full((size, size), 255.0 if opaque else 0.0)
    Image.fromarray(
        np.dstack([np.clip(lum, 0, 255), lum, lum, a]).astype(np.uint8)
    ).save(path)
    return path


# ── amplitude recovery ───────────────────────────────────────────────────────
# Recovery runs at 20 cycles across (the detrend kernel's sinc response is
# ~0.1% there; at 10 cycles it attenuates ~13% — the floor semantics tests
# below run at 10 cycles where only pass/fail at the floor matters).


def test_known_amplitude_recovered_per_axis(tmp_path):
    for axis in ("x", "y"):
        img = _grating(tmp_path / f"g_{axis}.png", cycles=20.0,
                       **{f"amp_{axis}": 20.0})
        r = measure_contrast_probe(img, REGION, (20, 20))
        assert r["valid"], r.get("reason")
        assert r[f"amplitude_{axis}"] == pytest.approx(20.0, abs=1.0), r
        # the orthogonal axis stays dead
        assert r["amplitude_y" if axis == "x" else "amplitude_x"] < 1.0, r


def test_known_amplitude_recovered_both_axes(tmp_path):
    img = _grating(tmp_path / "g.png", cycles=20.0, amp_x=16.0, amp_y=12.0)
    r = measure_contrast_probe(img, REGION, (20, 20))
    assert r["amplitude_x"] == pytest.approx(16.0, abs=1.0), r
    assert r["amplitude_y"] == pytest.approx(12.0, abs=1.0), r


def test_detected_cycles_land_on_authored_pitch(tmp_path):
    img = _grating(tmp_path / "g.png", cycles=9.0, amp_x=16.0)
    r = measure_contrast_probe(img, REGION, (10, 10), band=(0.6, 1.4))
    assert r["detected_cycles_x"] == pytest.approx(9.0, abs=0.3), r


def test_band_excludes_unrelated_pitch(tmp_path):
    """A grating OUTSIDE the search band must not be credited as the
    relief: the probe reads ~0, not the grating's amplitude."""
    img = _grating(tmp_path / "g.png", amp_x=30.0, cycles=40.0)
    r = measure_contrast_probe(img, REGION, (10, 10), band=(0.6, 1.4))
    assert r["amplitude_x"] < 3.0, r


# ── ratio-never-gates (the §H pin) ───────────────────────────────────────────


def test_flat_field_fails_the_floor(tmp_path):
    img = _grating(tmp_path / "flat.png")
    r = measure_contrast_probe(img, REGION, (10, 10), min_amplitude=6.0)
    assert r["valid"]
    assert r["amplitude"] < 1.0, r
    assert r["passed"] is False


def test_same_ratio_small_amplitude_fails_while_large_passes(tmp_path):
    """THE §H regression: both gratings have axis ratio 1.0 (perfectly
    balanced) — only the absolute floor separates invisible from legible.
    A ratio alone must never gate this."""
    small = measure_contrast_probe(
        _grating(tmp_path / "small.png", amp_x=2.0, amp_y=2.0),
        REGION, (10, 10), min_amplitude=6.0)
    large = measure_contrast_probe(
        _grating(tmp_path / "large.png", amp_x=12.0, amp_y=12.0),
        REGION, (10, 10), min_amplitude=6.0)
    assert small["passed"] is False and large["passed"] is True
    # the two cases are indistinguishable by ratio
    assert (small["amplitude_x"] / small["amplitude_y"]) == pytest.approx(
        large["amplitude_x"] / large["amplitude_y"], rel=0.2)


def test_strong_axis_cannot_carry_a_dead_axis(tmp_path):
    """The max-based gate defect: y at 12 grey levels with x dead must
    FAIL on axes="both" (the floor gates on the weakest gated axis); a
    deliberately one-directional relief opts out explicitly with axes="y"."""
    img = _grating(tmp_path / "asym.png", amp_x=0.0, amp_y=12.0)
    both = measure_contrast_probe(img, REGION, (10, 10), axes="both")
    only_y = measure_contrast_probe(img, REGION, (10, 10), axes="y")
    only_x = measure_contrast_probe(img, REGION, (10, 10), axes="x")
    assert both["passed"] is False, both
    assert only_y["passed"] is True, only_y
    assert only_x["passed"] is False, only_x


def test_bad_axes_rejected(tmp_path):
    img = _grating(tmp_path / "g.png", amp_x=12.0, amp_y=12.0)
    with pytest.raises(ValueError, match="axes"):
        measure_contrast_probe(img, REGION, (10, 10), axes="diagonal")


# ── fail-closed refusals ─────────────────────────────────────────────────────


def test_mostly_transparent_region_refuses_fail_closed(tmp_path):
    img = _grating(tmp_path / "bg.png", amp_x=12.0, amp_y=12.0, opaque=False)
    r = measure_contrast_probe(img, REGION, (10, 10))
    assert r["valid"] is False
    assert r["passed"] is False
    assert "opaque" in r["reason"]


def test_region_too_small_refuses(tmp_path):
    img = _grating(tmp_path / "g.png", amp_x=12.0, amp_y=12.0, size=64)
    r = measure_contrast_probe(img, (0.0, 0.0, 0.2, 0.2), (10, 10))
    assert r["valid"] is False and r["passed"] is False


# ── view_stats balance checks ────────────────────────────────────────────────


def test_view_stats_clipped_fraction_over_opaque_only(tmp_path):
    """The top-right quadrant (half the OPAQUE half) at full white; the
    transparent background must not dilute the fraction."""
    lum = np.full((SIZE, SIZE), 128.0)
    lum[: SIZE // 2, SIZE // 2:] = 255.0
    alpha = np.full((SIZE, SIZE), 255)
    alpha[:, : SIZE // 2] = 0  # left half transparent
    Image.fromarray(np.dstack(
        [lum, lum, lum, alpha]).astype(np.uint8)).save(tmp_path / "v.png")
    s = view_stats(tmp_path / "v.png")
    assert s["valid"]
    assert s["opaque_fraction"] == pytest.approx(0.5, abs=0.01)
    assert s["clipped_fraction"] == pytest.approx(0.5, abs=0.01), s
    assert s["mean_luminance"] == pytest.approx((255 + 128) / 2, abs=1.0)


def test_view_stats_refuses_fully_transparent(tmp_path):
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(tmp_path / "t.png")
    s = view_stats(tmp_path / "t.png")
    assert s["valid"] is False


def test_load_luminance_rec709_weights(tmp_path):
    Image.new("RGB", (4, 4), (255, 0, 0)).save(tmp_path / "r.png")
    lum, alpha = load_luminance(tmp_path / "r.png")
    assert lum.mean() == pytest.approx(0.2126 * 255, abs=0.5)
    assert alpha.mean() == pytest.approx(1.0)
