"""T4 texture machinery: pattern generators (analytic coverage + period
invariance), compose (scan/AO fallback, manifests), and the placeholder decal.

The operator is text-only — every visual claim here is a NUMBER: analytic
coverage formulas, period-shift invariance, mean preservation, manifests.

Tiling is verified as PERIOD INVARIANCE: every generator's parameters are
integer counts per tile, so shifting the sampled tile by exactly one period
(in pixels) must reproduce it exactly. That is the discrete statement of
"tiles seamlessly", and it is exact rather than eyeballed.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from src.textures import patterns
from src.textures.decal import generate_placeholder_decal


def _period_shift_is_identity(mask: np.ndarray, shift: int, axis: int) -> bool:
    assert mask.shape[0] % shift == 0 and mask.shape[1] % shift == 0
    return np.array_equal(np.roll(mask, shift, axis=axis), mask)


# ── oval_holes ───────────────────────────────────────────────────────────────


def test_oval_holes_coverage_matches_analytic():
    """Running-bond ellipse holes cover ~pi*rx*ry of the tile (the stagger
    does not change total area — the historical parity bug doubled it to
    0.395 vs the analytic 0.221)."""
    res, cx, cy = 480, 12, 8
    rx, ry = 0.32, 0.22
    m = patterns.oval_holes(res, cx, cy, hole_rx=rx, hole_ry=ry)
    coverage = float(m.mean())
    analytic = np.pi * rx * ry
    assert abs(coverage - analytic) < 0.02 * analytic


def test_oval_holes_requires_even_rows():
    """cells_y must be EVEN: row parity is what staggers the running bond;
    an odd count wraps an offset row onto an unoffset one."""
    with pytest.raises(ValueError, match="even"):
        patterns.oval_holes(64, 4, 3)


def test_oval_holes_period_invariance():
    """Shifting by two cells in u and two rows in v (even shifts preserve
    parity) must reproduce the tile exactly — the discrete tiling proof.
    Grid convention: axis 0 = u, axis 1 = v (indexing="ij")."""
    res, cx, cy = 480, 12, 8
    m = patterns.oval_holes(res, cx, cy)
    assert _period_shift_is_identity(m, res // cx * 2, axis=0)  # 2 cells in u
    assert _period_shift_is_identity(m, res // cy * 2, axis=1)  # 2 rows in v


# ── herringbone ──────────────────────────────────────────────────────────────


def test_herringbone_requires_even_columns():
    with pytest.raises(ValueError, match="even"):
        patterns.herringbone(64, columns=7)


def test_herringbone_has_stripes_and_grooves():
    out = patterns.herringbone(256, columns=8, stripes_per_column=4)
    assert set(out) >= {"stripe", "groove"}
    assert out["stripe"].any()
    assert out["groove"].any()
    # grooves are thin boundary channels, not wide bands
    assert 0.0 < out["groove"].mean() < 0.35


def test_herringbone_period_invariance():
    """Two columns (parity preserved) in u and stripes_per_column periods in
    v are exact periods of the weave. Axis 0 = u, axis 1 = v."""
    res, cols, stripes = 256, 8, 4
    out = patterns.herringbone(res, columns=cols, stripes_per_column=stripes)
    for key in ("stripe", "groove"):
        assert _period_shift_is_identity(out[key], res // cols * 2, axis=0)
        assert _period_shift_is_identity(out[key], res // stripes, axis=1)


# ── chevron ──────────────────────────────────────────────────────────────────


def test_chevron_coverage_equals_thickness():
    """A vertical-distance zigzag band covers EXACTLY `thickness` of every
    column — analytically checkable at any amplitude/pitch."""
    res, thickness = 480, 0.035
    m = patterns.chevron(res, pitch=8, thickness=thickness, amplitude=0.16)
    assert abs(float(m.mean()) - thickness) < 1.5 / res


def test_chevron_oscillates():
    m = patterns.chevron(480, pitch=8, thickness=0.035, amplitude=0.16)
    assert m.std() > 0.1
    assert 0.0 < float(m.mean()) < 1.0


def test_chevron_period_invariance():
    """One zigzag period in u is an exact period of the print (axis 0 = u)."""
    res, pitch = 480, 8
    m = patterns.chevron(res, pitch=pitch, thickness=0.035, amplitude=0.16)
    assert _period_shift_is_identity(m, res // pitch, axis=0)


# ── tint / overlay / modulate ────────────────────────────────────────────────


def test_tint_preserves_channel_means_when_unclipped():
    """Scaling toward a DARKER target never clips, so the per-channel mean is
    preserved exactly (structure = deviations from the mean)."""
    img = np.random.default_rng(7).random((64, 64, 3)) * 0.5 + 0.25
    out = patterns.tint(img, (0.3, 0.2, 0.1))
    for c in range(3):
        assert abs(out[..., c].mean() - (0.3, 0.2, 0.1)[c]) < 1e-5


def test_tint_matches_target_mean():
    img = np.full((32, 32, 3), 0.5)
    out = patterns.tint(img, (0.9, 0.1, 0.5))
    assert np.allclose(out, (0.9, 0.1, 0.5))


def test_overlay_alpha_zero_is_identity():
    base = np.full((8, 8, 3), 0.4)
    mask = np.ones((8, 8))
    out = patterns.overlay(base, mask, (0.9, 0.9, 0.9), opacity=0.0)
    assert np.allclose(out, base)


def test_overlay_alpha_one_takes_layer_color():
    base = np.full((8, 8, 3), 0.4)
    mask = np.ones((8, 8))
    out = patterns.overlay(base, mask, (0.9, 0.1, 0.2), opacity=1.0)
    assert np.allclose(out, (0.9, 0.1, 0.2))


def test_modulate_shifts_where_mask():
    ch = np.full((8, 8), 0.5)
    mask = np.zeros((8, 8))
    mask[:4] = 1.0
    out = patterns.modulate(ch, mask, -0.2)
    assert np.allclose(out[:4], 0.3) and np.allclose(out[4:], 0.5)


# ── save/load roundtrip (row 0 = v = 0 bottom, flipud on save) ───────────────


def test_save_png_bottom_row_convention(tmp_path: Path):
    from PIL import Image

    arr = np.zeros((4, 6, 3), dtype=np.float64)
    arr[0, :, 0] = 1.0  # bottom row (v=0) is red
    p = tmp_path / "t.png"
    patterns.save_png(arr, p)
    img = np.asarray(Image.open(p)).astype(np.float64) / 255.0
    assert img.shape == (4, 6, 3)
    # PIL loads row 0 at the TOP — the saved file must have flipped it
    assert img[-1, 0, 0] > 0.9 and img[0, 0, 0] < 0.1


def test_load_gray_roundtrip(tmp_path: Path):
    arr = np.random.default_rng(3).random((16, 16))
    p = tmp_path / "g.png"
    patterns.save_png(arr, p)
    back = patterns.load_gray(p)
    assert back.shape == (16, 16)
    assert np.allclose(back, arr, atol=1 / 255.0)


def test_load_rgb_resizes_to_request(tmp_path: Path):
    arr = np.random.default_rng(3).random((32, 32, 3))
    p = tmp_path / "c.png"
    patterns.save_png(arr, p)
    back = patterns.load_rgb(p, size=16)
    assert back.shape[:2] == (16, 16)


# ── compose: scan loading + AO-height fallback + manifest ────────────────────


def _fake_scan_dir(tmp_path: Path, flat_height: bool) -> Path:
    d = tmp_path / "scan_fake"
    d.mkdir()
    rng = np.random.default_rng(11)
    patterns.save_png(rng.random((64, 64, 3)) * 0.8 + 0.1, d / "albedo.png")
    if flat_height:
        patterns.save_png(np.full((64, 64), 1.0), d / "height.png")  # placeholder
    else:
        patterns.save_png(rng.random((64, 64)), d / "height.png")
    patterns.save_png(rng.random((64, 64)) * 0.5 + 0.25, d / "roughness.png")
    # AO with real relief regardless (cavity dark = low)
    patterns.save_png(rng.random((64, 64)) * 0.6 + 0.2, d / "ao.png")
    (d / "SOURCE.json").write_text(json.dumps({
        "asset": "scan_fake", "site": "example", "url": "https://example.invalid/x",
        "licence": "CC0", "physical_size_cm": [100.0, 100.0],
    }), encoding="utf-8")
    return d


def _surface(**over):
    from src.spec.template import SurfaceSpec

    base = {"base": "scan", "scan": "scan_fake", "tint": [0.5, 0.5, 0.5],
            "tile_m": 1.0, "resolution": 128, "roughness": 0.7,
            "bump_strength": 0.1}
    base.update(over)
    return SurfaceSpec.model_validate(base)


def test_compose_surface_writes_maps_and_manifest(tmp_path: Path, monkeypatch):
    from src.textures import compose

    _fake_scan_dir(tmp_path, flat_height=False)
    monkeypatch.setattr(compose, "SCAN_ROOT", tmp_path)
    out = compose.compose_surface("prod", "surf", _surface(), out_root=tmp_path / "out")
    for name in ("albedo.png", "roughness.png", "height.png", "manifest.json"):
        assert (out / name).is_file(), name
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["scan"]["asset"] == "scan_fake"
    assert manifest["scan"]["licence"] == "CC0"
    assert "height_source" not in manifest  # displacement had relief — no fallback


def test_compose_surface_falls_back_to_ao_height(tmp_path: Path, monkeypatch):
    """Poly Haven fabric Displacement maps can be constant-1.0 placeholders —
    compose must detect the flat map and derive height from AO instead, and
    RECORD it in the manifest (never silently)."""
    from src.textures import compose

    _fake_scan_dir(tmp_path, flat_height=True)
    monkeypatch.setattr(compose, "SCAN_ROOT", tmp_path)
    out = compose.compose_surface("prod", "surf", _surface(), out_root=tmp_path / "o2")
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert "ao" in manifest["height_source"]
    h = patterns.load_gray(out / "height.png")
    assert h.std() > 1e-3, "fallback height must carry the AO relief"


def test_compose_surface_missing_scan_raises(tmp_path: Path, monkeypatch):
    from src.textures import compose

    monkeypatch.setattr(compose, "SCAN_ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        compose.compose_surface("prod", "surf", _surface(scan="nope"),
                                out_root=tmp_path / "o3")


def test_compose_surface_layers_modulate_height(tmp_path: Path, monkeypatch):
    """A layer with height_delta must actually emboss the height map."""
    from src.spec.template import TextureLayerSpec
    from src.textures import compose

    _fake_scan_dir(tmp_path, flat_height=False)
    monkeypatch.setattr(compose, "SCAN_ROOT", tmp_path)
    surface = _surface(layers=[TextureLayerSpec.model_validate({
        "kind": "chevron", "params": {"pitch_m": 0.25, "thickness_m": 0.05,
                                      "amplitude_m": 0.1},
        "color": [0.2, 0.2, 0.2], "opacity": 0.5, "height_delta": -0.3,
    })])
    out = compose.compose_surface("prod", "surf", surface, out_root=tmp_path / "o4")
    h = patterns.load_gray(out / "height.png")
    assert h.std() > 0.05, "chevron emboss did not reach the height map"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["layers"][0]["kind"] == "chevron"


# ── placeholder decal (§5.3 label, blind-safe) ───────────────────────────────


def test_placeholder_decal_deterministic_and_structured(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    generate_placeholder_decal(a)
    generate_placeholder_decal(b)
    assert (a / "albedo.png").is_file()
    assert (a / "albedo.png").read_bytes() == (b / "albedo.png").read_bytes()
    img = patterns.load_rgb(a / "albedo.png")
    assert img.shape[:2] == (1024, 512)  # portrait per §5.3
    # not a blank fill: the label art carries structure
    assert img.std() > 0.05
