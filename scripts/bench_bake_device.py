"""Bake-device benchmark: CPU vs OptiX vs CUDA on the real 4K workload.

Measures the MECHANISM, not a proxy: while each bake runs, GPU
utilisation + VRAM are sampled from nvidia-smi at 1 Hz and the Blender
process's CPU-seconds are polled — the exact signals that exposed the
original bug (531 s wall, 1348 CPU-s, GPU pinned at 0% / 0 MiB because
scene.cycles.device was hardcoded "CPU").

Workload: the TEST-QUEEN mattress through the REAL chain inputs
(template + job card → prepare_delivery_scene → bake_maps), the same bake
the reviewer timed. The prepared scene is regenerated per run (the bake
op re-saves the scene after removing the HP shell).

After all runs, baked maps are pixel-diffed across devices (max/mean abs
delta in 8-bit levels, fraction of texels beyond 2 LSB) — GPU and CPU
Cycles do not produce bit-identical output, so the question is whether
the difference stays within the pinned test tolerances.

Usage:
    uv run python scripts/bench_bake_device.py [--res 4096]
        [--devices cpu,optix,cuda] [--job input/jobs/TEST-QUEEN.yaml]
Writes output/bakeoff/bake_device_bench.json and prints a table.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.blender.runner import BlenderRunner  # noqa: E402
from src.client.job import load_job  # noqa: E402
from src.spec.resolver import resolve_spec_to_build_params  # noqa: E402
from src.spec.template import compile_spec, load_template  # noqa: E402

MAP_NAMES = ("ao", "basecolor", "metallic", "normal", "roughness")


class GpuSampler:
    """1 Hz nvidia-smi utilisation/VRAM + per-process Blender CPU-seconds.
    Falls back to no-op when nvidia-smi is absent (CPU-only hosts)."""

    def __init__(self):
        self.samples: list[dict] = []
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._procs: list[subprocess.Popen] = []
        self.available = False

    def start(self):
        try:
            self._nvidia = subprocess.Popen(
                ["nvidia-smi",
                 "--query-gpu=utilization.gpu,memory.used",
                 "--format=csv,noheader,nounits", "-l", "1"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
            self.available = True
        except (OSError, FileNotFoundError):
            return
        # One PowerShell streaming blender.exe CPU-seconds (accumulated).
        self._ps = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command",
             "while($true){$p=Get-Process blender -ErrorAction SilentlyContinue;"
             "if($p){($p|Measure-Object CPU -Sum).Sum};Start-Sleep -Milliseconds 900}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        self._procs = [self._nvidia, self._ps]
        self._threads = [
            threading.Thread(target=self._read_gpu, daemon=True),
            threading.Thread(target=self._read_cpu, daemon=True),
        ]
        for t in self._threads:
            t.start()

    def _read_gpu(self):
        for line in self._nvidia.stdout:  # blocks; ends on terminate()
            line = line.strip()
            if not line or self._stop.is_set():
                continue
            try:
                util, vram = (int(float(v)) for v in line.split(","))
                self.samples.append({"gpu_util_pct": util, "vram_mib": vram})
            except ValueError:
                pass

    def _read_cpu(self):
        for line in self._ps.stdout:
            line = line.strip()
            if not line or self._stop.is_set():
                continue
            try:
                self.samples.append({"blender_cpu_sec": float(line)})
            except ValueError:
                pass

    def stop(self) -> dict:
        self._stop.set()
        for p in self._procs:
            try:
                p.terminate()
            except OSError:
                pass
        for t in self._threads:
            t.join(timeout=5)
        utils = [s["gpu_util_pct"] for s in self.samples if "gpu_util_pct" in s]
        vrams = [s["vram_mib"] for s in self.samples if "vram_mib" in s]
        cpus = [s["blender_cpu_sec"] for s in self.samples if "blender_cpu_sec" in s]
        return {
            "gpu_util_mean_pct": round(sum(utils) / len(utils), 1) if utils else None,
            "gpu_util_max_pct": max(utils) if utils else None,
            "gpu_util_samples": len(utils),
            "vram_max_mib": max(vrams) if vrams else None,
            "blender_cpu_sec_last_sample": round(max(cpus), 1) if cpus else None,
            "sampler_available": self.available,
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:16]


def pixel_diff(a: Path, b: Path) -> dict | None:
    """8-bit per-channel deltas between two baked PNGs."""
    import numpy as np
    from PIL import Image

    if not (a.is_file() and b.is_file()):
        return None
    with Image.open(a) as ia, Image.open(b) as ib:
        aa = np.asarray(ia.convert("RGB"), dtype=np.int16)
        ab = np.asarray(ib.convert("RGB"), dtype=np.int16)
    if aa.shape != ab.shape:
        return {"shape_mismatch": [list(aa.shape), list(ab.shape)]}
    d = np.abs(aa - ab)
    return {
        "max_delta_lsbs": int(d.max()),
        "mean_delta_lsbs": round(float(d.mean()), 4),
        "frac_texels_off": round(float((d.max(axis=2) > 0).mean()), 6),
        "frac_texels_beyond_2lsb": round(float((d.max(axis=2) > 2).mean()), 6),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--res", type=int, default=4096)
    ap.add_argument("--devices", default="cpu,optix,cuda")
    ap.add_argument("--job", default="input/jobs/TEST-QUEEN.yaml")
    ap.add_argument("--out", default="output/bakeoff")
    ap.add_argument("--timeout", type=float, default=3600.0)
    ap.add_argument("--persistent", action="store_true",
                    help="Pass persistent_data=True to the bake op (keeps "
                         "the Cycles session across the ~20 per-pass bake "
                         "calls instead of rebuilding the BVH each time)")
    args = ap.parse_args()

    job = load_job(PROJECT_ROOT / args.job)
    spec, _ = compile_spec(load_template(PROJECT_ROOT / "templates/mattress.yaml"), job)
    build = resolve_spec_to_build_params(spec)
    detail_map = {p["name"]: p["detail"] for p in build["spec"]["parts"] if p.get("detail")}

    runner = BlenderRunner()
    if not runner.is_available:
        print("Blender not found", file=sys.stderr)
        return 1

    out_root = PROJECT_ROOT / args.out
    out_root.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}

    for device in [d.strip() for d in args.devices.split(",") if d.strip()]:
        work = out_root / f"bench_{device}"
        work.mkdir(parents=True, exist_ok=True)
        scene_blend = work / "scene.blend"

        print(f"\n=== {device}: prepare (quad-verify + UV atlas) ===", flush=True)
        t0 = time.perf_counter()
        prep = runner.execute_op("prepare_delivery_scene", {
            "build": build, "out_blend": str(scene_blend)})
        prep_sec = round(time.perf_counter() - t0, 1)
        assert prep.get("success"), prep.get("error")
        print(f"    prepared in {prep_sec} s "
              f"(pack_scale {prep.get('uv_atlas', {}).get('pack_scale')})", flush=True)

        print(f"=== {device}: bake {args.res}px (5 maps) ===", flush=True)
        sampler = GpuSampler()
        sampler.start()
        t0 = time.perf_counter()
        try:
            bake = runner.execute_op("bake_maps", {
                "input": str(scene_blend),
                "out_dir": str(work / "maps"),
                "maps": None,
                "resolution": args.res,
                "detail": detail_map,
                "detail_normal": True,
                "hp_glb": str(work / "hp.glb"),
                "save_blend": str(scene_blend),
                "device": device,
                "persistent_data": args.persistent,
            }, timeout_sec=args.timeout)
        except Exception as e:  # noqa: BLE001 — a device crash is a finding
            wall_sec = round(time.perf_counter() - t0, 1)
            gpu = sampler.stop()
            print(f"    BAKE CRASHED on {device} after {wall_sec} s: "
                  f"{str(e)[:400]}", flush=True)
            results[device] = {"success": False, "crashed": True,
                               "error": str(e)[:800], "wall_sec": wall_sec,
                               **gpu}
            continue
        wall_sec = round(time.perf_counter() - t0, 1)
        gpu = sampler.stop()

        if not bake.get("success"):
            print(f"    BAKE FAILED on {device}: {bake.get('error')}", flush=True)
            results[device] = {"success": False, "error": bake.get("error"),
                               "wall_sec": wall_sec, **gpu}
            continue

        maps = {}
        for m in MAP_NAMES:
            p = work / "maps" / f"{m}.png"
            maps[m] = ({"bytes": p.stat().st_size, "sha256_16": _sha256(p)}
                       if p.is_file() else None)
        results[device] = {
            "success": True,
            "wall_sec": wall_sec,
            "prepare_sec": prep_sec,
            "device_evidence": bake.get("device"),
            "bake_warnings": bake.get("warnings"),
            "hp_tri": bake.get("hp_triangle_equivalent"),
            "lp_tri": bake.get("lp_triangle_equivalent"),
            "maps": maps,
            "map_stats": {m: (bake.get("maps", {}).get(m, {}) or {}).get("stats")
                          for m in MAP_NAMES},
            **gpu,
        }
        print(f"    wall {wall_sec} s | GPU util mean {gpu['gpu_util_mean_pct']}% "
              f"max {gpu['gpu_util_max_pct']}% | VRAM max {gpu['vram_max_mib']} MiB "
              f"| blender CPU ~{gpu['blender_cpu_sec_last_sample']} s", flush=True)
        print(f"    device: {json.dumps(bake.get('device'))}", flush=True)

    # ── cross-device output comparison (determinism drift) ──────────────────
    devs = [d for d, r in results.items() if r.get("success")]
    diffs: dict[str, dict] = {}
    for i, a in enumerate(devs):
        for b in devs[i + 1:]:
            pair = {}
            for m in MAP_NAMES:
                pa = out_root / f"bench_{a}" / "maps" / f"{m}.png"
                pb = out_root / f"bench_{b}" / "maps" / f"{m}.png"
                d = pixel_diff(pa, pb)
                if d:
                    pair[m] = d
            diffs[f"{a}_vs_{b}"] = pair
    report = {
        "job": args.job, "resolution": args.res,
        "persistent_data": args.persistent,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "devices": results, "output_diffs": diffs,
    }
    out_file = out_root / "bake_device_bench.json"
    out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"\n{'device':<8}{'wall s':>9}{'GPU%':>12}{'VRAM MiB':>10}{'CPU s':>9}")
    for d, r in results.items():
        if not r.get("success"):
            print(f"{d:<8}  FAILED: {r.get('error', '')[:60]}")
            continue
        print(f"{d:<8}{r['wall_sec']:>9}{str(r['gpu_util_mean_pct']) + ' avg':>12}"
              f"{r['vram_max_mib']:>10}{r['blender_cpu_sec_last_sample']:>9}")
    print(f"\nreport: {out_file}")
    for pair, per_map in diffs.items():
        for m, d in per_map.items():
            if "max_delta_lsbs" in d:
                print(f"  {pair} {m}: max {d['max_delta_lsbs']} LSB, "
                      f"mean {d['mean_delta_lsbs']}, "
                      f">{2}LSB on {d['frac_texels_beyond_2lsb'] * 100:.2f}% of texels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
