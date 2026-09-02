"""Phase 4 owner-supplied texture index tests.

The index is the brain's only view of the owner's drop-directory (the brain
is text-only): surface names, per-map measured facts (resolution, sha256,
edge-wrap value continuity), skipped drops with reasons, and the
never-diffusion selection contract. Facts are pinned against the files on
disk; the edge-wrap metric's documented caveat — a high-frequency pattern
reads high even when geometrically tileable — is pinned as behavior, not
fixed: the number means value continuity, judgment stays with the brain.
"""

import hashlib
import json
import random
from pathlib import Path

import pytest
from PIL import Image

from src.textures.owner_index import (
    INDEX_NAME,
    _edge_wrap_delta_mean,
    index_owner_textures,
)


def _write_png(path: Path, size=(8, 8), fill=(128, 128, 128)) -> Path:
    Image.new("RGB", size, fill).save(path, format="PNG")
    return path


def _write_noise_png(path: Path, size=(8, 8), seed=7) -> Path:
    rng = random.Random(seed)
    img = Image.new("RGB", size)
    img.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                 for _ in range(size[0] * size[1])])
    img.save(path, format="PNG")
    return path


def _write_seam_png(path: Path, size=(8, 8)) -> Path:
    img = Image.new("RGB", size, (0, 0, 0))
    for x in range(size[0] // 2, size[0]):
        for y in range(size[1]):
            img.putpixel((x, y), (255, 255, 255))
    img.save(path, format="PNG")
    return path


def _write_checker_png(path: Path, size=(8, 8)) -> Path:
    img = Image.new("RGB", size)
    img.putdata([((255, 255, 255) if (x + y) % 2 == 0 else (0, 0, 0))
                 for y in range(size[1]) for x in range(size[0])])
    img.save(path, format="PNG")
    return path


# ── the edge-wrap metric ──────────────────────────────────────────────────────


def _wrap_of(path: Path) -> float | None:
    with Image.open(path) as img:
        return _edge_wrap_delta_mean(img)


def test_edge_wrap_zero_when_edge_values_continue(tmp_path: Path):
    """A flat image wraps perfectly: 0.0 on the 0–255 scale."""
    p = _write_png(tmp_path / "wrap_flat.png")
    assert _wrap_of(p) == 0.0


def test_edge_wrap_detects_a_hard_seam(tmp_path: Path):
    """Half black / half white: left vs right edges disagree on every pixel
    (top vs bottom rows are identical), so the mean lands well above 0."""
    p = _write_seam_png(tmp_path / "wrap_seam.png")
    assert _wrap_of(p) > 100


def test_edge_wrap_caveat_high_frequency_reads_high(tmp_path: Path):
    """The documented caveat, pinned: a 1px checker IS geometrically tileable
    (period divides the size) yet the metric reads ~max, because opposite
    EDGES hold opposite values. The number measures value continuity — the
    brain must not read it as 'does not tile'."""
    p = _write_checker_png(tmp_path / "wrap_checker.png")
    assert _wrap_of(p) >= 200


def test_edge_wrap_none_when_too_small_to_wrap():
    img = Image.new("RGB", (1, 1), (10, 10, 10))
    assert _edge_wrap_delta_mean(img) is None


# ── the index ────────────────────────────────────────────────────────────────


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """A representative drop-directory: two usable surfaces (one via the
    .jpg alias), a normal-map-only drop, a hidden dir, a loose root file."""
    weave = tmp_path / "weave"
    weave.mkdir()
    _write_png(weave / "albedo.png")                      # uniform → wraps 0.0
    _write_noise_png(weave / "roughness.png", size=(16, 8))
    _write_png(weave / "height.png")
    (weave / "notes.txt").write_text("shot on a flatbed", encoding="utf-8")

    noise_set = tmp_path / "noise_set"
    noise_set.mkdir()
    Image.new("RGB", (16, 16), (90, 90, 95)).save(noise_set / "albedo.jpg",
                                                  format="JPEG")

    bare = tmp_path / "bare_metal"                        # no harness-consumed map
    bare.mkdir()
    _write_png(bare / "normal.png")

    (tmp_path / ".cache").mkdir()
    (tmp_path / "README.md").write_text("drop texture sets here", encoding="utf-8")
    return tmp_path


def test_index_surfaces_maps_and_measured_facts(library: Path):
    index = index_owner_textures(library, write=False)

    assert index["schema"] == "threed-owner-textures/1"
    assert index["surface_count"] == 2
    # sorted order — the brain sees a stable listing
    assert [s["name"] for s in index["surfaces"]] == ["noise_set", "weave"]

    weave = index["surfaces"][1]
    assert set(weave["maps"]) == {"albedo", "roughness", "height"}
    assert weave["path"] == str((library / "weave").resolve())
    assert weave["other_files"] == ["notes.txt"]
    # facts measured, not asserted: resolution, content hash, wrap continuity
    assert weave["maps"]["albedo"]["resolution_px"] == [8, 8]
    assert weave["maps"]["roughness"]["resolution_px"] == [16, 8]
    assert weave["maps"]["albedo"]["sha256"] == hashlib.sha256(
        (library / "weave" / "albedo.png").read_bytes()).hexdigest()
    assert weave["maps"]["albedo"]["edge_wrap_delta_mean"] == 0.0
    assert weave["maps"]["roughness"]["edge_wrap_delta_mean"] > 0
    assert weave["min_resolution_px"] == 8

    noise_set = index["surfaces"][0]
    # the .jpg alias satisfies the albedo slot; one map is a usable surface
    assert set(noise_set["maps"]) == {"albedo"}
    assert noise_set["maps"]["albedo"]["file"] == "albedo.jpg"
    assert noise_set["min_resolution_px"] == 16


def test_index_records_skipped_and_root_files(library: Path):
    index = index_owner_textures(library, write=False)

    skipped = {s["name"]: s for s in index["skipped"]}
    assert set(skipped) == {".cache", "bare_metal"}
    assert skipped[".cache"]["reason"] == "hidden directory"
    assert "no canonical map" in skipped["bare_metal"]["reason"]
    assert skipped["bare_metal"]["files"] == ["normal.png"]
    assert index["root_files"] == ["README.md"]


def test_index_carries_the_selection_contract(library: Path):
    index = index_owner_textures(library, write=False)
    # the never-diffusion order rides in the index itself — the brain reads
    # the contract where it reads the surfaces
    assert "diffusion" in index["selection_contract"]
    assert "texture_dir" in index["selection_contract"]


def test_disp_png_alias_fills_the_height_slot(tmp_path: Path):
    surface = tmp_path / "metal_brush"
    surface.mkdir()
    _write_png(surface / "albedo.png")
    _write_png(surface / "disp.png")
    index = index_owner_textures(tmp_path, write=False)
    assert index["surface_count"] == 1
    assert index["surfaces"][0]["maps"]["height"]["file"] == "disp.png"


def test_index_json_written_deterministic_and_ignores_itself(library: Path):
    first = index_owner_textures(library)  # write=True default
    on_disk = json.loads((library / INDEX_NAME).read_text(encoding="utf-8"))
    assert on_disk == first

    second = index_owner_textures(library)
    # everything except the timestamp is byte-stable across runs
    assert second["surfaces"] == first["surfaces"]
    assert second["skipped"] == first["skipped"]
    assert second["root_files"] == first["root_files"] == ["README.md"]
    assert INDEX_NAME not in second["root_files"]  # own output, ignored


def test_write_false_leaves_no_index(library: Path):
    index_owner_textures(library, write=False)
    assert not (library / INDEX_NAME).exists()


def test_missing_root_fails_loud(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="not found"):
        index_owner_textures(tmp_path / "nope")
