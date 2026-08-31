# Golden Benchmark Specs

Real-world objects with authoritative measurements, used to verify the whole
pipeline (build → measure → gates) against known-correct dimensions.

| Benchmark | Dimensions | Source |
|---|---|---|
| `counter_stool.spec.json` | seat height 0.66 m, seat Ø 0.38 m | dimensions.com — Stools collection: counter-height stool seats are 23"–28" (58–71 cm); 0.66 m sits mid-range for a 89–94 cm counter |
| `coffee_table.spec.json` | 1.2 × 0.6 × 0.40 m | industry-standard coffee table proportions (dimensions.com furniture guides) |
| `coffee_mug.spec.json` | Ø 0.095 m × 0.10 m, ~350 ml | standard mug size (dimensions.com drinkware guides) |
| `../sample_desk.spec.json` | 1.4 × 0.7 × 0.76 m | standard office desk height 76 cm (30") |

Deterministic layer: `tests/test_golden_benchmarks.py` builds every spec here
and requires both gates (dimension + mesh) to pass.

AI layer: `scripts/benchmark_golden.py` feeds each object's *measurements
only* (no spec) through the GLM-5.3 analyst and scores how often the agent
lands within tolerance of these same targets.
