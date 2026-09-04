"""Phase 7 batch throughput driver — N concurrent end-to-end chains.

Master order Phase 7: "3 jobs concurrently (32 threads, 64 GB), CPU bakes at
1K for iteration, GPU reserved for final 4K; then a batch of 5 with real
measured wall clock per model and total."

Per job the full Phase 6 chain runs as subprocesses (one process per op, no
shared state): `src.cli build` (agent loop; vision env vars STRIPPED per the
standing S1 hold) → `src.cli package --spec` (T3 finish chain, CPU bakes at
the default 1K) → `src.cli validate`. Wall clock is measured per step, per
job, and for the whole batch; logs and a summary JSON land under
output/phase7/<tag>/.

Thread policy: each worker sets THREED_BLENDER_THREADS so N concurrent
Blender processes split the box instead of each grabbing every core (see
src/blender/runner.py — explicit env, no per-op override). Default is
TOTAL_THREADS // concurrency (10 for the 3-job shakedown, 6 for the batch
of 5 on the 32-thread box).

Usage:
  uv run python scripts/phase7_batch.py --intake          # write job cards
  uv run python scripts/phase7_batch.py --tag shakedown --codes D,E,F
  uv run python scripts/phase7_batch.py --tag batch5
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# code, product_class, ref dir, run-name prefix, inline measurements (metres).
# The measurements mirror each PROMPT.md's stated test constraints exactly.
SUBJECTS: list[dict[str, str]] = [
    {
        "letter": "D",
        "code": "STEPSTOOL0001",
        "product_class": "step_stool",
        "ref_dir": "PHASE7-D-STEPSTOOL",
        "name": "phase7_d_stepstool",
        "measurements": (
            "overall length 0.45 m; overall width 0.42 m; overall height 0.48 m; "
            "lower tread top 0.24 m above the floor; tread depth 0.225 m; "
            "tread width 0.42 m; side panel thickness 0.02 m"
        ),
    },
    {
        "letter": "E",
        "code": "MILKCHURN0001",
        "product_class": "milk_churn",
        "ref_dir": "PHASE7-E-MILKCHURN",
        "name": "phase7_e_milkchurn",
        "measurements": (
            "overall length 0.34 m; overall width 0.34 m; overall height 0.64 m; "
            "body height 0.56 m; base diameter 0.30 m; shoulder diameter 0.34 m"
        ),
    },
    {
        "letter": "F",
        "code": "GARDENTROWEL0001",
        "product_class": "garden_trowel",
        "ref_dir": "PHASE7-F-TROWEL",
        "name": "phase7_f_trowel",
        "measurements": (
            "overall length 0.32 m; overall width 0.07 m; overall height 0.045 m; "
            "blade length 0.16 m; blade width 0.07 m; grip diameter 0.032 m; "
            "grip length 0.14 m; grip axis 0.029 m above the floor"
        ),
    },
    {
        "letter": "G",
        "code": "CHAMBERSTICK0001",
        "product_class": "chamberstick",
        "ref_dir": "PHASE7-G-CHAMBERSTICK",
        "name": "phase7_g_chamberstick",
        "measurements": (
            "overall length 0.19 m; overall width 0.14 m; overall height 0.065 m; "
            "pan diameter 0.14 m; socket top 0.065 m above the floor; "
            "handle reach beyond rim 0.05 m"
        ),
    },
    {
        "letter": "H",
        "code": "GALVBUCKET0001",
        "product_class": "water_bucket",
        "ref_dir": "PHASE7-H-BUCKET",
        "name": "phase7_h_bucket",
        "measurements": (
            "overall length 0.26 m; overall width 0.26 m; overall height 0.33 m; "
            "body height 0.25 m; base diameter 0.21 m; rim diameter 0.26 m"
        ),
    },
]

PRINT_LOCK = threading.Lock()


def say(msg: str) -> None:
    with PRINT_LOCK:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def photos_for(subject: dict[str, str]) -> list[Path]:
    ref = ROOT / "input" / "references" / subject["ref_dir"]
    prov = json.loads((ref / "PROVENANCE.json").read_text(encoding="utf-8"))
    return [ref / e["local_file"] for e in prov]


def run_intake(redo: bool = False) -> None:
    """Deterministic front door: PROMPT.md → input/jobs/<CODE>.yaml."""
    sys.path.insert(0, str(ROOT))
    from src.client.job import dump_job_yaml, intake_from_prompt

    for s in SUBJECTS:
        ref_dir = ROOT / "input" / "references" / s["ref_dir"]
        dest = ROOT / "input" / "jobs" / f"{s['code']}.yaml"
        if dest.exists() and not redo:
            say(f"intake: {s['code']} card exists, skipping")
            continue
        prompt = (ref_dir / "PROMPT.md").read_text(encoding="utf-8")
        card = intake_from_prompt(
            prompt,
            job_code=s["code"],
            product_class=s["product_class"],
            reference_dir=ref_dir,
        )
        dest.write_text(dump_job_yaml(card), encoding="utf-8")
        say(f"intake: {s['code']} → {dest.relative_to(ROOT)} "
            f"({card.dims.length:g}x{card.dims.width:g}x{card.dims.height:g} "
            f"{card.dims.unit}, {card.complexity}, ceiling "
            f"{card.polycount_ceiling:,} {card.polycount_semantics})")


def worker_env(threads: int) -> dict[str, str]:
    """Child env: vision keys STRIPPED (standing S1 hold — no live vision
    calls, providers report unavailable without probing) + Blender thread
    cap so concurrent workers split the box."""
    env = dict(os.environ)
    for key in ("THREED_VLM_API_KEY", "GEMINI_API_KEY"):
        env.pop(key, None)
    env["THREED_BLENDER_THREADS"] = str(threads)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def run_step(step: str, cmd: list[str], env: dict[str, str],
             log_path: Path, timeout: float) -> tuple[int, float]:
    t0 = time.perf_counter()
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        log.write(f"# {' '.join(str(c) for c in cmd)}\n\n")
        log.flush()
        proc = subprocess.run(
            cmd, cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    return proc.returncode, time.perf_counter() - t0


def find_run_dir(name: str, preexisting: set[Path]) -> Path | None:
    """Newest run dir for this name, excluding dirs that existed before the
    batch (keeps earlier runs with the same prefix out of the pick)."""
    candidates = [
        d for d in (ROOT / "output" / "runs").glob(f"*_{name}_*")
        if d not in preexisting
        and (d / "spec.json").is_file() and (d / "final.glb").is_file()
    ]
    return max(candidates, key=lambda d: d.name) if candidates else None


def run_job(subject: dict[str, str], tag_dir: Path, threads: int,
            preexisting: set[Path]) -> dict:
    code, name = subject["code"], subject["name"]
    result: dict = {"code": code, "steps": {}, "threads": threads}
    env = worker_env(threads)
    photos = photos_for(subject)
    missing = [str(p) for p in photos if not p.is_file()]
    if missing:
        result["error"] = f"missing reference photos: {missing}"
        return result
    prompt = (ROOT / "input" / "references" / subject["ref_dir"]
              / "PROMPT.md").read_text(encoding="utf-8")
    job_yaml = ROOT / "input" / "jobs" / f"{code}.yaml"

    say(f"{code}: build start ({threads} Blender threads, "
        f"{len(photos)} refs, vision env stripped)")
    cmd = [sys.executable, "-m", "src.cli", "build",
           "-p", prompt, "-m", subject["measurements"],
           "-n", name, "-j", str(job_yaml)]
    for p in photos:
        cmd += ["-i", str(p)]
    rc, wall = run_step("build", cmd, env, tag_dir / f"{code}_build.log", 3600.0)
    result["steps"]["build"] = {"exit": rc, "wall_s": round(wall, 1)}
    say(f"{code}: build exit {rc} in {wall:.0f}s")
    if rc != 0:
        result["error"] = "build failed"
        return result

    run_dir = find_run_dir(name, preexisting)
    if run_dir is None:
        result["error"] = "no run dir with spec.json + final.glb found"
        return result
    result["run_dir"] = str(run_dir.relative_to(ROOT))

    say(f"{code}: package start (CPU 1K iteration bake)")
    rc, wall = run_step(
        "package",
        [sys.executable, "-m", "src.cli", "package",
         "--spec", str(run_dir / "spec.json"), "--job", str(job_yaml),
         "--bake-device", "cpu", "--bake-timeout", "3600"],
        env, tag_dir / f"{code}_package.log", 5400.0)
    result["steps"]["package"] = {"exit": rc, "wall_s": round(wall, 1)}
    say(f"{code}: package exit {rc} in {wall:.0f}s")

    pkg_dir = ROOT / "output" / "packages" / code
    result["package_dir"] = str(pkg_dir.relative_to(ROOT)) if pkg_dir.is_dir() else None
    qa = pkg_dir / "qa_report.json"
    if qa.is_file():
        try:
            report = json.loads(qa.read_text(encoding="utf-8"))
            result["qa_all_passed"] = report.get("all_passed")
            result["gates"] = [
                {"gate": g.get("gate"), "passed": g.get("passed")}
                for g in report.get("gates", [])
            ]
        except (json.JSONDecodeError, OSError):
            pass

    if pkg_dir.is_dir():
        rc, wall = run_step(
            "validate",
            [sys.executable, "-m", "src.cli", "validate", str(pkg_dir),
             "--job", str(job_yaml), "--json"],
            env, tag_dir / f"{code}_validate.log", 900.0)
        result["steps"]["validate"] = {"exit": rc, "wall_s": round(wall, 1)}
        say(f"{code}: validate exit {rc} in {wall:.0f}s")

    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--intake", action="store_true",
                    help="write input/jobs/<CODE>.yaml from the PROMPT.md files")
    ap.add_argument("--redo-intake", action="store_true",
                    help="rewrite job cards even if they exist")
    ap.add_argument("--codes", default="",
                    help="comma-separated subject letters to run (default: all)")
    ap.add_argument("--tag", default="run",
                    help="output/phase7/<tag>/ subdirectory for logs + summary")
    ap.add_argument("--concurrency", type=int, default=0,
                    help="parallel jobs (default: number of selected jobs)")
    ap.add_argument("--threads-per-worker", type=int, default=0,
                    help="Blender threads per worker (default: cpu_count // concurrency)")
    args = ap.parse_args()

    if args.intake:
        run_intake(redo=args.redo_intake)
        if not args.codes:
            return 0

    letters = [x.strip().upper() for x in args.codes.split(",") if x.strip()]
    selected = [s for s in SUBJECTS if not letters or s["letter"] in letters]
    if not selected:
        ap.error(f"no subjects match --codes {args.codes!r}")

    concurrency = args.concurrency or len(selected)
    total_threads = os.cpu_count() or 8
    threads = args.threads_per_worker or max(1, total_threads // concurrency)

    tag_dir = ROOT / "output" / "phase7" / args.tag
    tag_dir.mkdir(parents=True, exist_ok=True)

    say(f"batch '{args.tag}': {len(selected)} jobs, concurrency "
        f"{concurrency}, {threads} Blender threads/worker "
        f"({total_threads} logical cores)")

    batch_start = time.perf_counter()
    runs_root = ROOT / "output" / "runs"
    preexisting = set(runs_root.glob("*")) if runs_root.is_dir() else set()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(run_job, s, tag_dir, threads, preexisting): s
            for s in selected
        }
        for fut in as_completed(futures):
            s = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:  # noqa: BLE001 — record, keep the batch going
                results.append({"code": s["code"], "error": f"{type(e).__name__}: {e}"})
    total_wall = time.perf_counter() - batch_start

    results.sort(key=lambda r: r["code"])
    summary = {
        "tag": args.tag,
        "started_local": time.strftime("%Y-%m-%d %H:%M:%S"),
        "concurrency": concurrency,
        "threads_per_worker": threads,
        "total_wall_s": round(total_wall, 1),
        "jobs": results,
    }
    (tag_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\n=== batch '{args.tag}' — {len(selected)} jobs, "
          f"concurrency {concurrency}, {threads} threads/worker ===")
    for r in results:
        steps = r.get("steps", {})
        step_txt = "  ".join(
            f"{k}:{v['wall_s']}s(e{v['exit']})" for k, v in steps.items())
        gates = r.get("gates")
        gate_txt = ""
        if gates is not None:
            fails = [g["gate"] for g in gates if not g.get("passed")]
            gate_txt = " gates:ALL PASS" if not fails else f" gates FAIL:{fails}"
        err = f"  ERROR: {r['error']}" if r.get("error") else ""
        print(f"{r['code']:<16} {step_txt}{gate_txt}{err}")
    print(f"total wall clock: {total_wall:.0f}s "
          f"({total_wall / 60:.1f} min); summary → "
          f"{(tag_dir / 'summary.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
