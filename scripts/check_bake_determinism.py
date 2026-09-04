"""Determinism drift check: CPU vs GPU on the analytically-pinned fixtures.

Reproduces the exact fixtures and bake parameters of
tests/test_delivery_finish.py (the ramp normal-map proof and the AO cavity
proof), bakes them on each device, and reports:

1. The EXACT pixels the tests assert (test lines 206/208/213 territory):
   G/R/B at wy = 0.15/0.30/0.45 vs the analytic prediction, the neutral
   texel below the bend, and the AO cavity/exposed texels — per device.
2. Whole-map deltas vs the CPU bake (max/mean in 8-bit levels, fraction of
   texels beyond the tests' 2-LSB tolerance).

The question this answers: is GPU output equivalent within the pinned test
tolerances, or genuinely different? (GPU and CPU Cycles are not
bit-identical by design — different RNG and floating point.)

Usage: uv run python scripts/check_bake_determinism.py
Writes output/bakeoff/bake_determinism.json.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.blender.runner import BlenderRunner  # noqa: E402

DEVICES = ("cpu", "optix", "cuda")

# Verbatim from tests/test_delivery_finish.py (the pinned fixtures).
_RAMP_HP_SCRIPT = """
import bpy
src = bpy.data.objects["ramp"]
bpy.ops.object.select_all(action="DESELECT")
src.select_set(True)
bpy.context.view_layer.objects.active = src
bpy.ops.object.duplicate()
hp = bpy.context.active_object
hp.name = "ramp__HP"
for v in hp.data.vertices:
    if v.co.y > 0:
        v.co.z += 0.15 * (v.co.y / 0.5) ** 2
bpy.ops.object.shade_smooth()
RESULT = {"hp": "ramp__HP"}
"""


def _make_ramp_blend(runner, tmp: Path) -> Path:
    blend = tmp / "ramp.blend"
    code = f"""
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_grid_add(x_subdivisions=33, y_subdivisions=33, size=1.0, location=(0, 0, 0))
obj = bpy.context.active_object
obj.name = "ramp"
me = obj.data
uv = me.uv_layers.new(name="UVMap")
me.uv_layers.active = uv
for loop in me.loops:
    v = me.vertices[loop.vertex_index]
    uv.data[loop.index].uv = (v.co.x + 0.5, v.co.y + 0.5)
bpy.ops.wm.save_as_mainfile(filepath=r"{blend}")
RESULT = {{"blend": r"{blend}"}}
"""
    r = runner.execute_op("run_script", {"code": code})
    assert r["success"], r.get("error")
    return blend


def _make_ao_blend(runner, tmp: Path) -> Path:
    blend = tmp / "aocav.blend"
    code = f"""
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_grid_add(x_subdivisions=33, y_subdivisions=33, size=1.0, location=(0, 0, 0))
obj = bpy.context.active_object
obj.name = "floor_plane"
me = obj.data
uv = me.uv_layers.new(name="UVMap")
me.uv_layers.active = uv
for loop in me.loops:
    v = me.vertices[loop.vertex_index]
    uv.data[loop.index].uv = (v.co.x + 0.5, v.co.y + 0.5)
bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.25, 0.25, 0.03))
box = bpy.context.active_object
box.name = "blocker"
box.scale = (0.2, 0.2, 0.06)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
bpy.ops.wm.save_as_mainfile(filepath=r"{blend}")
RESULT = {{"blend": r"{blend}"}}
"""
    r = runner.execute_op("run_script", {"code": code})
    assert r["success"], r.get("error")
    return blend


def _png_pixel(path: Path, u: float, v: float):
    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size
        x = min(int(u * w), w - 1)
        y = h - 1 - min(int(v * h), h - 1)
        return img.getpixel((x, y))


def _map_diff(a: Path, b: Path) -> dict:
    import numpy as np
    from PIL import Image

    with Image.open(a) as ia, Image.open(b) as ib:
        aa = np.asarray(ia.convert("RGB"), dtype=np.int16)
        ab = np.asarray(ib.convert("RGB"), dtype=np.int16)
    d = np.abs(aa - ab)
    return {
        "max_delta_lsbs": int(d.max()),
        "mean_delta_lsbs": round(float(d.mean()), 4),
        "frac_texels_beyond_2lsb": round(float((d.max(axis=2) > 2).mean()), 6),
    }


def main() -> int:
    runner = BlenderRunner()
    if not runner.is_available:
        print("Blender not found", file=sys.stderr)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="determinism_"))
    ramp_blend = _make_ramp_blend(runner, tmp)
    ao_blend = _make_ao_blend(runner, tmp)

    out: dict = {"ramp_pinned_pixels": {}, "ao_pinned_pixels": {},
                 "normal_diff_vs_cpu": {}, "ao_diff_vs_cpu": {},
                 "bake_sec": {}}

    crashed: set[str] = set()
    for device in DEVICES:
        try:
            t0 = time.perf_counter()
            rdir = tmp / f"ramp_maps_{device}"
            r = runner.execute_op("bake_maps", {
                "input": str(ramp_blend), "out_dir": str(rdir),
                "maps": ["normal"], "hp_mode": "script",
                "hp_script": _RAMP_HP_SCRIPT, "resolution": 512,
                "samples": 8, "ray_distance_factor": 0.5, "device": device,
            }, timeout_sec=600)
            assert r["success"], f"ramp bake failed: {r.get('error')}"
            out["bake_sec"][f"ramp_normal_{device}"] = round(
                time.perf_counter() - t0, 1)
            out["ramp_pinned_pixels"][device] = {"device_evidence": r["device"]}
            normal_png = rdir / "normal.png"

            adir = tmp / f"ao_maps_{device}"
            t0 = time.perf_counter()
            r = runner.execute_op("bake_maps", {
                "input": str(ao_blend), "out_dir": str(adir),
                "maps": ["ao"], "resolution": 512, "samples": 16,
                "device": device,
            }, timeout_sec=600)
            assert r["success"], f"AO bake failed: {r.get('error')}"
            out["bake_sec"][f"ao_{device}"] = round(time.perf_counter() - t0, 1)
            ao_png = adir / "ao.png"
        except Exception as e:  # noqa: BLE001 — a device crash is a finding
            # e.g. CUDA hard-killing the Blender process at 4K also applies
            # here; record it and keep the other devices' verdicts intact.
            crashed.add(device)
            out["ramp_pinned_pixels"][device] = {"crashed": str(e)[:400]}
            out["ao_pinned_pixels"][device] = {"crashed": str(e)[:400]}
            print(f"  !! {device} bake CRASHED: {str(e)[:200]}")
            continue

        # The exact pixels test_delivery_finish.py asserts (lines 202-213,
        # 235-236), plus the analytic prediction for G.
        pinned = {}
        for wy in (0.15, 0.30, 0.45):
            r_, g, b_ = _png_pixel(normal_png, 0.5, wy + 0.5)
            slope = 1.2 * wy
            g_pred = round((0.5 - 0.5 * (slope / (1 + slope * slope) ** 0.5)) * 255)
            pinned[f"wy={wy}"] = {"R": r_, "G": g, "B": b_, "G_analytic": g_pred,
                                  "G_minus_analytic": g - g_pred}
        pinned["neutral_wy=-0.3"] = dict(
            zip(("R", "G", "B"), _png_pixel(normal_png, 0.5, 0.2)))
        out["ramp_pinned_pixels"][device].update(pinned)

        under = _png_pixel(ao_png, 0.75, 0.75)
        far = _png_pixel(ao_png, 0.10, 0.10)
        out["ao_pinned_pixels"][device] = {
            "device_evidence": r["device"],
            "cavity_under_box_R": under[0], "exposed_far_R": far[0],
            "contrast_R": far[0] - under[0],
        }

    for device in DEVICES:
        if device == "cpu" or device in crashed:
            continue
        if (tmp / "ramp_maps_cpu" / "normal.png").is_file():
            out["normal_diff_vs_cpu"][device] = _map_diff(
                tmp / "ramp_maps_cpu" / "normal.png",
                tmp / f"ramp_maps_{device}" / "normal.png")
        if (tmp / "ao_maps_cpu" / "ao.png").is_file():
            out["ao_diff_vs_cpu"][device] = _map_diff(
                tmp / "ao_maps_cpu" / "ao.png",
                tmp / f"ao_maps_{device}" / "ao.png")

    out_file = PROJECT_ROOT / "output" / "bakeoff" / "bake_determinism.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print("== ramp normal pinned pixels (test asserts G within ±2 of analytic)")
    for device in DEVICES:
        p = out["ramp_pinned_pixels"][device]
        if "crashed" in p:
            print(f"  {device:<7}CRASHED — no baked map")
            continue
        devs = ", ".join(
            f"wy={wy}: G={p[f'wy={wy}']['G']} (analytic "
            f"{p[f'wy={wy}']['G_analytic']}, Δ{p[f'wy={wy}']['G_minus_analytic']:+d})"
            for wy in (0.15, 0.30, 0.45))
        print(f"  {device:<7}{devs} | neutral {p['neutral_wy=-0.3']}")
    print("== AO cavity pinned pixels (test: under < 0.3*255, far > 0.6*255)")
    for device in DEVICES:
        p = out["ao_pinned_pixels"][device]
        if "crashed" in p:
            continue
        print(f"  {device:<7}under R={p['cavity_under_box_R']}  "
              f"far R={p['exposed_far_R']}  contrast={p['contrast_R']}")
    print("== whole-map diff vs CPU")
    for device in ("optix", "cuda"):
        if device in out["normal_diff_vs_cpu"]:
            print(f"  normal {device}: {out['normal_diff_vs_cpu'][device]}")
        if device in out["ao_diff_vs_cpu"]:
            print(f"  ao     {device}: {out['ao_diff_vs_cpu'][device]}")
    print(f"\nreport: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
