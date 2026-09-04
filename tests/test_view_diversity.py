"""View-diversity metric tests (§3.1 — GLM_PROMPT_NEURAL_INTAKE.md).

The real calibration sets (`Test Images/`) are untracked owner photos,
so their measured numbers are pinned in the module docstring; these
tests pin the METRIC's behaviour on synthetic images:

- exact duplicates and exposure-shifted copies of one view must score
  ~0 and WARN (the mattress failure mode: near-duplicate viewpoints);
- structurally different views must score well above the threshold and
  pass (the cup case);
- dHash must be invariant to the exposure shift that fooled greyscale
  correlation (mattress mean 1−cos 0.389 ≥ cup 0.377 — rejected metric).
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from src.neural.view_diversity import WARN_THRESHOLD, measure_view_diversity


def _gradient(path, direction, base=128, size=256):
    """A synthetic 'view': a smooth gradient — its dHash bits are the
    sign of the horizontal derivative, so direction flips them."""
    if direction == "right":
        a = np.tile(np.linspace(0, 255, size, dtype=np.uint8), (size, 1))
    elif direction == "left":
        a = np.tile(np.linspace(255, 0, size, dtype=np.uint8), (size, 1))
    elif direction == "flat":
        a = np.full((size, size), base, dtype=np.uint8)
    elif direction == "checker":
        g = np.linspace(0, 255, size, dtype=np.uint8)
        a = np.where((np.add.outer(g, g) // 32) % 2, 240, 15).astype(np.uint8)
    else:
        raise ValueError(direction)
    Image.fromarray(a).save(path)
    return path


def _shifted_copy(src, dst, factor):
    """Same structure, different exposure — greyscale correlation moves,
    dHash must not. A pure scale (no clipping) preserves every gradient
    sign; an additive shift would clip at 255 and flatten structure."""
    a = np.asarray(Image.open(src).convert("L"), dtype=np.float64)
    Image.fromarray(np.clip(a * factor, 0, 255).astype(np.uint8)).save(dst)
    return dst


def test_threshold_is_the_calibrated_value():
    # 0.20 — round number inside the measured gap [mattress 0.135, cup
    # 0.299]; changing it requires re-measuring on the four sets.
    assert WARN_THRESHOLD == 0.20


def test_identical_views_warn(tmp_path):
    a = _gradient(tmp_path / "a.png", "right")
    b = _gradient(tmp_path / "b.png", "right")
    r = measure_view_diversity([a, b])
    assert r.score == 0.0
    assert r.warned
    assert "near-duplicates" in r.reason
    assert r.pairwise == [{"a": "a.png", "b": "b.png", "distance": 0.0}]


def test_exposure_shifted_views_still_warn(tmp_path):
    a = _gradient(tmp_path / "a.png", "right")
    b = _shifted_copy(a, tmp_path / "b.png", factor=0.45)
    r = measure_view_diversity([a, b])
    # dHash is a gradient-sign metric: a pure exposure shift cannot move it.
    assert r.score == 0.0
    assert r.warned


def test_structurally_different_views_pass(tmp_path):
    paths = [
        _gradient(tmp_path / "v1.png", "right"),
        _gradient(tmp_path / "v2.png", "checker"),
        _gradient(tmp_path / "v3.png", "left"),
        _gradient(tmp_path / "v4.png", "flat"),
    ]
    r = measure_view_diversity(paths)
    assert r.score is not None and r.score > WARN_THRESHOLD
    assert not r.warned
    assert r.image_count == 4
    assert len(r.pairwise) == 6  # C(4,2)
    assert r.max_pairwise >= r.score >= r.min_pairwise


def test_alpha_images_do_not_read_as_black_structure(tmp_path):
    # Same structure with and without an alpha channel must land close:
    # alpha composited on mid-grey, not black.
    rgb = np.tile(np.linspace(0, 255, 128, dtype=np.uint8), (128, 1))
    Image.fromarray(rgb).save(tmp_path / "plain.png")
    rgba = np.dstack([rgb, rgb, rgb,
                      np.full((128, 128), 255, dtype=np.uint8)])
    Image.fromarray(rgba.astype(np.uint8)).save(tmp_path / "alpha.png")
    r = measure_view_diversity([tmp_path / "plain.png", tmp_path / "alpha.png"])
    assert r.score == 0.0


def test_single_image_cannot_measure(tmp_path):
    a = _gradient(tmp_path / "a.png", "right")
    r = measure_view_diversity([a])
    assert r.score is None
    assert r.warned
    assert "fewer than 2" in r.reason


def test_missing_files_are_skipped_and_named(tmp_path):
    a = _gradient(tmp_path / "a.png", "right")
    r = measure_view_diversity([a, tmp_path / "missing.png"])
    assert r.score is None
    assert "missing.png" in r.reason


def test_mattress_shape_case_three_dupes_one_distinct(tmp_path):
    # Three near-duplicates of one view plus one genuinely different
    # view: the mean must stay above the floor (the different view is
    # what carries proportions), while max records it.
    a = _gradient(tmp_path / "a.png", "right")
    b = _shifted_copy(a, tmp_path / "b.png", factor=0.7)
    c = _shifted_copy(a, tmp_path / "c.png", factor=1.0)
    d = _gradient(tmp_path / "d.png", "checker")
    r = measure_view_diversity([a, b, c, d])
    assert r.score is not None and r.score > WARN_THRESHOLD
    assert not r.warned
    assert r.max_pairwise == pytest.approx(max(p["distance"] for p in r.pairwise))


def test_describe_carries_the_number(tmp_path):
    # describe() formats the score with 3 decimals for the UI/manifest
    p1 = _gradient(tmp_path / "a.png", "right")
    p2 = _gradient(tmp_path / "b.png", "checker")
    r = measure_view_diversity([p1, p2])
    assert "view diversity 0." in r.describe()
    assert "max pair" in r.describe()
