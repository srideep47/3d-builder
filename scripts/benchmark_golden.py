"""Golden AI benchmark — feeds each benchmark object's MEASUREMENTS ONLY
(no spec, no reference images) through the GLM-5.3 analyst + build loop and
scores how often the agent lands within tolerance of the real-world targets.

Usage:
    python scripts/benchmark_golden.py [--objects stool table mug desk] [--json out.json]

Sources for the target dimensions: input/benchmarks/README.md (dimensions.com).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.blender.runner import BlenderRunner  # noqa: E402
from src.pipeline import ThreeDBuilderPipeline  # noqa: E402
from src.spec.schema import ObjectSpec  # noqa: E402

BENCHMARKS: dict[str, dict] = {
    "stool": {
        "prompt": "A four-legged wooden counter stool with a round seat and slightly tapered legs",
        "measurements": "seat height 0.66 m, seat diameter 0.38 m, leg height 0.61 m",
        "spec": PROJECT_ROOT / "input" / "benchmarks" / "counter_stool.spec.json",
    },
    "table": {
        "prompt": "A rectangular coffee table with four slightly tapered legs and a beveled hardwood top",
        "measurements": "overall length 1.2 m, overall width 0.6 m, overall height 0.40 m",
        "spec": PROJECT_ROOT / "input" / "benchmarks" / "coffee_table.spec.json",
    },
    "mug": {
        "prompt": "A ceramic coffee mug with a hollow interior and a round loop handle on one side",
        "measurements": "overall height 0.10 m, body diameter 0.095 m",
        "spec": PROJECT_ROOT / "input" / "benchmarks" / "coffee_mug.spec.json",
    },
    "desk": {
        "prompt": "A modern wooden office desk with a wide top and four tapered legs",
        "measurements": "overall width 1.4 m, overall depth 0.7 m, overall height 0.76 m",
        "spec": PROJECT_ROOT / "input" / "sample_desk.spec.json",
    },
}


def run_benchmark(name: str, cfg: dict, pipeline: ThreeDBuilderPipeline) -> dict:
    print(f"\n{'='*60}\n[{name}] {cfg['prompt']}\n  measurements: {cfg['measurements']}")
    t0 = time.time()
    result = pipeline.generate_from_prompt(
        prompt=cfg["prompt"],
        measurements=cfg["measurements"],
        run_name=f"bench_{name}",
    )
    elapsed = time.time() - t0

    golden = ObjectSpec.model_validate(json.loads(cfg["spec"].read_text(encoding="utf-8")))

    # Score the AI's own declared measurements (the accuracy contract).
    ai_passed = bool(result.verification and result.verification.passed)

    # Cross-check against the golden spec's targets: the AI spec must declare
    # the same overall measurements and hit them.
    golden_overall = {
        m.applies_to: (m.name, m.target_value)
        for m in golden.measurements
        if m.applies_to.startswith("overall.") or m.applies_to.startswith("seat.")
    }
    hits, misses = [], []
    hit_targets = set()
    if result.verification:
        for d in result.verification.dimension_gate.details:
            target = next((at for at, (n, _) in golden_overall.items() if n == d["name"]), None)
            if target is None:
                continue
            if d.get("passed"):
                hits.append(f"{d['name']}: target {d['target_m']} actual {d['actual_m']}")
                hit_targets.add(target)
            else:
                misses.append(f"{d['name']}: target {d['target_m']} actual {d['actual_m']}")
    undeclared = [n for at, (n, _) in golden_overall.items() if at not in hit_targets]

    outcome = {
        "object": name,
        "success": result.success,
        "ai_gate_passed": ai_passed,
        "iterations": result.iterations,
        "elapsed_s": round(elapsed, 1),
        "final_glb": str(result.final_glb_path) if result.final_glb_path else None,
        "golden_hits": hits,
        "golden_misses": misses,
        "undeclared_measurements": undeclared,
        "error": result.error,
    }
    status = "PASS" if result.success and not misses and not undeclared else "WARN"
    print(f"  => {status} in {outcome['iterations']} iteration(s), {outcome['elapsed_s']}s "
          f"(gates: {'green' if ai_passed else 'red'})")
    for h in hits:
        print(f"     hit   {h}")
    for m in misses:
        print(f"     MISS  {m}")
    for u in undeclared:
        print(f"     ??    {u} not declared by the AI spec")
    if result.error:
        print(f"     error: {result.error[:300]}")
    return outcome


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--objects", nargs="*", default=list(BENCHMARKS), choices=list(BENCHMARKS))
    ap.add_argument("--json", type=Path, default=None, help="write results to this JSON file")
    args = ap.parse_args()

    pipeline = ThreeDBuilderPipeline(runner=BlenderRunner())

    results = []
    for name in args.objects:
        try:
            results.append(run_benchmark(name, BENCHMARKS[name], pipeline))
        except Exception as e:  # keep the suite running
            print(f"  => ERROR {e}")
            results.append({"object": name, "success": False, "error": str(e)})

    passed = sum(1 for r in results if r.get("success"))
    print(f"\n{'='*60}\nSUMMARY: {passed}/{len(results)} objects fully passed "
          f"(dimension + mesh gates green)")

    if args.json:
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"results written to {args.json}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
