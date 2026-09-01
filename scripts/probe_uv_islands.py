"""Probe (not a test): does the vertex-keyed UV-island matching in
_uv_face_groups actually merge UV-contiguous faces? Compiles the mattress
template and runs prepare_delivery_scene (build + atlas + diagnostics — no
bake), then prints the island count vs face count.

Usage: python scripts/probe_uv_islands.py [job_yaml]
(default job: input/jobs/TEST-QUEEN.yaml)

T4 recorded 2118 islands with the OLD (broken) matching — roughly one island
per face. With the fix the mattress collapses to ~80-84 islands (the exact
count varies slightly with proportions via smart-project seam choice).
"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.blender.runner import BlenderRunner
from src.client.job import load_job
from src.spec.resolver import resolve_spec_to_build_params
from src.spec.template import TemplateSpec, compile_spec

ROOT = Path(__file__).resolve().parents[1]

tpl = TemplateSpec.model_validate(
    yaml.safe_load((ROOT / "templates" / "mattress.yaml").read_text(encoding="utf-8"))
)
job_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "input" / "jobs" / "TEST-QUEEN.yaml"
job = load_job(job_path)
spec, warnings = compile_spec(tpl, job)
params = resolve_spec_to_build_params(spec)

runner = BlenderRunner()
res = runner.execute_op("prepare_delivery_scene", {"build": params})
if not res.get("success"):
    print("FAILED:", res.get("error"))
    sys.exit(1)

uv = res["uv"]
topo = res["topology"]
print(f"faces_total:      {topo['faces_total']}  (quads {topo['quads']}, "
      f"tris {topo['triangles']}, ngons {topo['ngons']})")
print(f"islands_total:    {uv['islands_total']}")
print(f"pack_scale:       {res['uv_atlas']['pack_scale']:.4f}")
print(f"overlaps:         {uv['overlapping_island_pairs']}")
td = uv["texel_density_texels_per_m"]
print(f"texel density:    min {td['min']:.1f} max {td['max']:.1f} ratio {td['ratio']:.4f}")
print(f"compile warnings: {warnings}")
