# AGENTS.md — 3D Builder Agent System Documentation

> **Read `PROJECT_PLAN.md` first** — it is the master plan/handoff: full
> history, current state, the complete gotchas ledger, machine-transfer
> runbook, and the M4 roadmap. This file is the quick operational summary.

## Architecture
3D Builder is structured in 4 layers plus a web interface and a GPU microservice:
1. **Interfaces**: `src/cli.py` (Typer CLI), `src/mcp_server.py` (MCP stdio server; mcp 2.x `MCPServer`, 1.x fallback), `src/webapp/` (FastAPI server + run registry) serving `web/` (three.js studio UI; `python -m src.cli ui`).
2. **Agent Layer**: `src/agent/loop.py` (analyst → neural parts → build → measure → render → gates → corrector; emits progress events, supports cancel + run_dir reuse), `src/agent/prompts.py` (Analyst, Corrector), `src/agent/verifier.py` (dimension + mesh gates), `src/ai/aptos.py` (GLM-5.3 integration), `src/ai/vlm.py` (vision providers behind a `VisionProvider` ABC — T5: local Qwen-VL via OpenAI-compatible vLLM AND Google Gemini v1beta; analyst eye + advisory visual gate; selected by `config/ai.yaml` `vision.vlm.provider`).
3. **Spec Layer**: `src/spec/schema.py` (ObjectSpec v2 Pydantic model), `src/spec/resolver.py` (spec → build params), `src/spec/validation.py` (dimension gate), `src/spec/template.py` + `templates/<product_class>.yaml` (T4 product templates: proportions × job-card dims → ObjectSpec; the ONLY place product knowledge lives, rule 11), `src/textures/` (T4 texture composition: CC0 scans + procedural tileable patterns → canonical map sets; `scripts/fetch_cc0_textures.py` + `scripts/gen_template_textures.py`).
4. **Capability Layer**: `src/blender/runner.py` (subprocess runner), `src/blender/harness_script.py` (self-contained headless Blender engine — runs inside Blender's Python, must not import project code), `src/img3d/client.py` (RemoteImg3DProvider → the neural service), `services/img3d_service/` (FastAPI GPU microservice, PLAN.md §9: single-job queue, mock + tripo_sr backends, trellis/hunyuan3d bake-off slots).
5. **Client Layer**: `src/client/` (MetaZtech delivery compliance — knows the contract, never the product): `job.py` (JobCard from job.yaml; dims + explicit unit REQUIRED, never inferred; axis_map L→X/W→Y/H→Z; client dim tolerance ±0.01 in default, separate from the internal ±1 mm), `contract.py` (the single shared deliverable-set + tier-ceiling definition), `gates.py` (six pure validator gates + MeshFacts, fail-closed without mesh facts), `units.py` (metres ↔ client units, boundary only), `fbx_inspect.py` (independent binary-FBX reader — GlobalSettings/geometry/Model transforms parsed without Blender, plus the signed-permutation chirality machinery), `package.py` (assembles `output/packages/<JOB>/` + `qa_report.json`). `python -m src.cli package <glb> --job job.yaml` then `validate <pkg_dir>` mirror the client's validator locally.

## Operational Rules
- All lengths are stored in **meters** in internal representations.
- Origin is centered at bottom-center `(0, 0, 0)` so models sit on the ground plane.
- Geometry builds must verify against dimension tolerances before final export.
- **Two Python environments, never mixed**: main `.venv` (light deps) and
  `services/img3d_service/.venv` (torch cu124 + model deps; Forge only).
  Start the service with `scripts/start-img3d.ps1 [tripo_sr]` before builds
  that contain `image_to_3d` parts (mock backend runs in the main env).

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
  HTTP 400). The vision ladder is wired in `src/ai/vlm.py`: local Qwen-VL
  (vLLM, OpenAI-compatible) or Gemini (Google v1beta, `x-goog-api-key`,
  `contents`/`parts`), selected by `vision.vlm.provider`. Gemini models must
  be PINNED versions (`gemini-3.6-flash`; `-latest` aliases are rejected at
  construction) — `gemini-2.5-flash` is retired for new keys. API keys come
  from the ENV only (`THREED_VLM_API_KEY`, Gemini falls back to
  `GEMINI_API_KEY`); never commit a key.
- **glTF export splits vertices** per normal/UV attribute and stores part
  positions as node transforms. Mesh-gate checks must load via
  `src/agent/verifier.py: load_merged_mesh()` (`scene.to_mesh()` + constructor
  `process=True`) — plain `trimesh.util.concatenate` reports wrong bounds and
  false non-watertightness.
- **Procedural node shaders do not survive GLB export.** Default materials are
  flat PBR values; use the `bake_materials` op when procedural detail is needed.
- **Selected-to-active bakes cast rays INWARD (Blender `bake.cc` negate_v3).**
  Without a custom cage, HP geometry above the LP is invisible to the bake, an
  enclosing HP shell bakes its FAR side (hit normal flipped), and a ray miss
  writes neutral (128,128,255) — not black — so a dead bake looks clean. The
  `bake_maps` op therefore builds a cage shrunk inside each LP (outward rays,
  near-side detail), after the AO pass and deleted before re-save. The
  ramp proof (`tests/test_delivery_finish.py`) pins the baked normals to the
  analytic prediction within 1 LSB and proves `normal_g="POS_Y"` = OpenGL.
- **Blender resolves relative image paths against the .blend file**, not the
  process CWD — `img.save()` with a relative out_dir silently writes nothing.
  Bake outputs are `abspath`'d in the harness; package roots `.resolve()`d in
  `package.py`.
- **Bakes need an ACTIVE TexImage node in every baked material** — without
  one Blender silently bakes nothing (`_with_active_image` owns this).
- **Boolean-consumed parts have freed RNA structs.** After `apply_boolean`
  removes the tool object, filter object lists by identity (`is not`), never by
  `.name`.
- **The shape enum, the analyst prompt, and the harness `_build_shape`
  dispatch must stay in sync.** Shapes: box, rounded_box, cylinder,
  tapered_cylinder, sphere, cone, torus, tapered_extrude, revolve_lathe,
  extrude, sweep, organic. (`organic` is neural-only: the loop auto-routes it
  to `image_to_3d`; the harness skips it with a warning when no mesh exists.)
- **Neural parts** (`method: image_to_3d`): the loop generates meshes via the
  img3d service before build, caches them by part name under
  `run_dir/neural/`, and re-attaches from cache after corrector rewrites.
  The harness re-scales imported meshes to the part's current `target_size` —
  correct measurement fixes go through `target_size`, never re-generation.
- **torchmcubes doesn't build on Windows** — TripoSR uses a scikit-image shim
  installed into `sys.modules` by `providers/tripo_sr.py` (only when the real
  extension is absent). `rembg` needs an explicit `onnxruntime` install.
  `HF_HUB_CACHE` must be set before `huggingface_hub` is first imported
  (backend `__init__`).
- **`curve.bevel_object` is IGNORED unless `curve.bevel_mode = "OBJECT"`**
  (Blender 2.90+). Without the mode, `convert()` yields a bare polyline and
  glTF exports sweep parts (tape edges) as EMPTY transform-only nodes.
- **The template geometry contract**: band bodies are inset by the tape
  protrusion so tape outer faces land EXACTLY on nominal L/W — overall
  bounds equal the job card at any template scale; the decal is recessed
  behind the tape plane. Pinned in `tests/test_template_harness.py`.
- **Triplanar BOX mapping anchors its tile grid at the object-local
  origin** — the Mapping node needs `Location=(0.5,0.5,0.5)` or one-tile
  textures wrap and show twice mirrored. The glTF exporter converts
  BOX-projection materials to UV-mapped ones, so orientation must be
  probed on the live scene, never a GLB round trip. Label orientation
  pinned by NCC (identity 0.99 vs all flips negative).
- **Materials are created through a name-keyed cache** threaded through
  `apply_material` — per-part creation leaks .001/.002 datablock variants
  into the delivery scene.
- **glTF triangulates quads and splits vertices per attribute** — n-gon
  gates are only meaningful on .blend/FBX (the live quad-clean scene),
  never on a GLB. trimesh reads glTF Y-up; do screen/world math
  Blender-side (`matrix_world`).
- **Placeholder dims refuse delivery** (rule 9): a job card with
  `dims_placeholder: true` runs the full chain for structural review
  renders but emits NO package — `output/blocked/<JOB>/qa_report.json` +
  `PlaceholderDimensionsError` (CLI exit code 2). Never infer dims, never
  guess a standard size.

## Verification
- `python -m pytest tests -q` — 238 tests; `blender`-marked tests auto-skip
  when no Blender is found.
- Client packages: `python -m src.cli package --spec <spec.json> --job
  job.yaml` runs the full T3 finish chain (build → quad-verify + per-island
  UV atlas → 5-map bake → LP decimation → FBX from the live quad-clean scene
  → gates + qa_report with bake/UV evidence + review renders);
  `package --template templates/<class>.yaml --job job.yaml` compiles the
  product template with the job card's dimensions first (T4; a
  `dims_placeholder: true` card is REFUSED at package emission — exit 2,
  evidence in `output/blocked/`);
  `package <source_glb> --job job.yaml` is the T2 placeholder flow (assembles
  `output/packages/<JOB>/` + `qa_report.json`: gates, axis convention as
  independently parsed, hashes, placeholders);
  `python -m src.cli validate <package_dir> --job job.yaml` re-checks a
  package on disk (local mirror of the MetaZtech validator panel).
- Template surfaces: `python scripts/gen_template_textures.py --template
  templates/<class>.yaml` composes CC0 scan + procedural textures into
  `assets/textures/<class>/` (deterministic; provenance in each
  manifest.json; `--placeholder-decal` for the label stand-in).
- FBX axis/handedness is verified WITHOUT a Blender round trip
  (`src/client/fbx_inspect.py` independent binary parse + trimesh GLB
  cross-load) against the permanent chiral fixture
  `input/fixtures/chiral_test.spec.json`; the values Blender 4.5 actually
  writes are pinned in `tests/test_client_export.py` and recorded in
  PROGRESS.md.
- Golden benchmarks in `input/benchmarks/` (dimensions.com-sourced) must pass
  both gates deterministically; `scripts/benchmark_golden.py` scores the AI flow.
- img3d backend bake-off: `scripts/bakeoff_img3d.py` (service must be running).
- Web UI browser-verification evidence lives in `docs/gui-screenshots/`
  (08/09: the Delivery view — job intake + compliance panel with the live
  six-gate re-validation).
- Web UI Delivery view (`python -m src.cli ui`): job-intake form →
  `input/jobs/<code>.yaml` (dims + explicit unit required, rule 9;
  placeholder stand-ins flag delivery refusal), compliance panel mirrors
  the client validator over `output/packages/` + `output/blocked/` with a
  live re-run (`POST /api/packages/<JOB>/validate`, fresh Blender process
  for mesh facts).
