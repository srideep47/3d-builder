# AGENTS.md — 3D Builder Agent System Documentation

> **Read `PROJECT_PLAN.md` first** — it is the master plan/handoff: full
> history, current state, the complete gotchas ledger, machine-transfer
> runbook, and the M4 roadmap. This file is the quick operational summary.

## Architecture
3D Builder is structured in 4 layers plus a web interface:
1. **Interfaces**: `src/cli.py` (Typer CLI), `src/mcp_server.py` (MCP stdio server; mcp 2.x `MCPServer`, 1.x fallback), `src/webapp/` (FastAPI server + run registry) serving `web/` (three.js studio UI; `python -m src.cli ui`).
2. **Agent Layer**: `src/agent/loop.py` (analyst → build → measure → render → gates → corrector; emits progress events, supports cancel + run_dir reuse), `src/agent/prompts.py` (Analyst, Corrector), `src/agent/verifier.py` (dimension + mesh gates), `src/ai/aptos.py` (GLM-5.3 integration).
3. **Spec Layer**: `src/spec/schema.py` (ObjectSpec v2 Pydantic model), `src/spec/resolver.py` (spec → build params), `src/spec/validation.py` (dimension gate).
4. **Capability Layer**: `src/blender/runner.py` (subprocess runner), `src/blender/harness_script.py` (self-contained headless Blender engine — runs inside Blender's Python, must not import project code).

## Operational Rules
- All lengths are stored in **meters** in internal representations.
- Origin is centered at bottom-center `(0, 0, 0)` so models sit on the ground plane.
- Geometry builds must verify against dimension tolerances before final export.

## Hard-won invariants (do not regress these)
- **One Blender process per op.** `measure` / `render_views` run in fresh
  processes with no shared scene — they MUST be passed `model_path` pointing
  at the exported step GLB.
- **Background mode has stale `matrix_world`.** After setting `obj.location`,
  `obj.matrix_world` returns the old matrix until `view_layer.update()`
  (`_update_view()` in the harness). Reading it early silently breaks world-space
  clones (radial arrays collapse onto the origin).
- **GLM-5.3 is a reasoning model.** Reasoning tokens are spent before content;
  budgets come from `config/ai.yaml` (`roles.<role>.max_tokens`, analyst 16384).
  `finish_reason: "length"` + empty content = budget exhausted by reasoning.
  `reasoning_effort: low` (also in `config/ai.yaml`, sent via `extra_body`)
  cuts per-call latency ~30× with equal quality — do not remove it.
  The endpoint is keyless for text and NOT multimodal (vision probes fail with
  HTTP 400).
- **glTF export splits vertices** per normal/UV attribute and stores part
  positions as node transforms. Mesh-gate checks must load via
  `src/agent/verifier.py: load_merged_mesh()` (`scene.to_mesh()` + constructor
  `process=True`) — plain `trimesh.util.concatenate` reports wrong bounds and
  false non-watertightness.
- **Procedural node shaders do not survive GLB export.** Default materials are
  flat PBR values; use the `bake_materials` op when procedural detail is needed.
- **Boolean-consumed parts have freed RNA structs.** After `apply_boolean`
  removes the tool object, filter object lists by identity (`is not`), never by
  `.name`.
- **The shape enum, the analyst prompt, and the harness `_build_shape`
  dispatch must stay in sync.** Shapes: box, rounded_box, cylinder,
  tapered_cylinder, sphere, cone, torus, tapered_extrude, revolve_lathe,
  extrude, sweep, organic.

## Verification
- `python -m pytest tests -q` — 51 tests; `blender`-marked tests auto-skip
  when no Blender is found.
- Golden benchmarks in `input/benchmarks/` (dimensions.com-sourced) must pass
  both gates deterministically; `scripts/benchmark_golden.py` scores the AI flow.
- Web UI browser-verification evidence lives in `docs/gui-screenshots/`.
