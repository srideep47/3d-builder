# AGENTS.md — 3D Builder Agent System Documentation

> **Read `PROJECT_PLAN.md` first** — it is the master plan/handoff: full
> history, current state, the complete gotchas ledger, machine-transfer
> runbook, and the M4 roadmap. This file is the quick operational summary.

## Architecture
3D Builder is structured in 4 layers plus a web interface and a GPU microservice:
1. **Interfaces**: `src/cli.py` (Typer CLI), `src/mcp_server.py` (MCP stdio server; mcp 2.x `MCPServer`, 1.x fallback), `src/webapp/` (FastAPI server + run registry) serving `web/` (three.js studio UI; `python -m src.cli ui`).
2. **Agent Layer**: `src/agent/loop.py` (analyst → neural parts → build → measure → render → gates → corrector; emits progress events, supports cancel + run_dir reuse; Phase 5 closed loop — gates before eyes, iteration cap 8 (`config/ai.yaml agent.max_iterations`) with an honest cap report: a cap-exhausted run gets manifest status `iteration_cap_exhausted`, non-None `unresolved_error`, and `metrics.cap_report` carrying the failed gate evidence; the spec's `review_closeups` ride into render_views so the visual gate sees label/border detail; the owner texture library `input/textures/owner/` (or an explicit `owner_texture_root`) is indexed and offered to the analyst as a texture_dir menu — never diffusion), `src/agent/tools.py` (AGENT_TOOLS_SCHEMA + AgentToolExecutor: build_spec/measure_model/render_model/inspect/review/finish/package — measured facts only, rule-9 refusals as tool results, hash-keyed verdict cache, shared one-step vision escalation), `src/agent/prompts.py` (Analyst, Corrector), `src/agent/verifier.py` (dimension + mesh gates), `src/ai/aptos.py` (GLM-5.3 integration), `src/ai/vlm.py` (vision providers behind a `VisionProvider` ABC — T5: local Qwen-VL via OpenAI-compatible vLLM AND Google Gemini v1beta; analyst eye + advisory visual gate; selected by `config/ai.yaml` `vision.vlm.provider`; VISION_CONFIG §7 429 branch — RATE_LIMIT_EXCEEDED → exponential backoff with jitter 2 s→60 s, bounded retries, QUOTA_EXCEEDED/RESOURCE_EXHAUSTED → stop retrying and take the verdict from the local Qwen fallback `vision.local_fallback` (quota_fallback recorded honestly); Phase 5 image policy — overview renders + reference photos downscaled to ≤768×768, close-ups NEVER downscaled).
3. **Spec Layer**: `src/spec/schema.py` (ObjectSpec v2 Pydantic model), `src/spec/resolver.py` (spec → build params), `src/spec/validation.py` (dimension gate), `src/spec/template.py` + `templates/<product_class>.yaml` (T4 product templates: proportions × job-card dims → ObjectSpec; the ONLY place product knowledge lives, rule 11), `src/textures/` (T4 texture composition: CC0 scans + procedural tileable patterns → canonical map sets; `scripts/fetch_cc0_textures.py` + `scripts/gen_template_textures.py`; `owner_index.py` — Phase 4 owner drop-directory `input/textures/owner/<surface>/` → deterministic `index.json` with measured per-map facts; a selected surface's path goes straight into `PBRMaterial.texture_dir`; if a surface has no supplied file compose from CC0 — NEVER diffusion-generate a texture).
4. **Capability Layer**: `src/blender/runner.py` (subprocess runner), `src/blender/harness_script.py` (self-contained headless Blender engine — runs inside Blender's Python, must not import project code), `src/img3d/client.py` (RemoteImg3DProvider → the neural service), `services/img3d_service/` (FastAPI GPU microservice, PLAN.md §9: single-job queue, mock + tripo_sr backends, trellis/hunyuan3d bake-off slots).
5. **Client Layer**: `src/client/` (MetaZtech delivery compliance — knows the contract, never the product): `job.py` (JobCard from job.yaml; dims + explicit unit REQUIRED, never inferred; axis_map L→X/W→Y/H→Z; client dim tolerance ±0.01 in default, separate from the internal ±1 mm; Phase 4: optional dynamic constraints — polycount_ceiling/semantics, file_size_caps (SizeCap with MB-vs-MiB basis), required_formats, texture_resolution, fbx_axis pair — all consumed via `effective_*()` helpers, card > contract.py table, so override and enforcement cannot drift; `intake_from_prompt()` — deterministic regex prompt → JobCard, extracts ONLY explicit constraints, every silence/ambiguity is a loud IntakeError, provenance in `intake_evidence` → qa_report.json; `dump_job_yaml()` round-trips `load_job`), `contract.py` (the single shared deliverable-set + tier-ceiling definition), `gates.py` (six pure validator gates + MeshFacts, fail-closed without mesh facts; read the card's effective values — a `complex` job with no ceiling fails closed until the card states one), `units.py` (metres ↔ client units, boundary only), `fbx_inspect.py` (independent binary-FBX reader — GlobalSettings/geometry/Model transforms parsed without Blender, plus the signed-permutation chirality machinery), `package.py` (assembles `output/packages/<JOB>/` + `qa_report.json`; threads the card's FBX axes, ceiling, resolution; marks each emitted file `required:` true/false vs the card's set). `python -m src.cli package <glb> --job job.yaml` then `validate <pkg_dir>` mirror the client's validator locally.

## Operational Rules
- All lengths are stored in **meters** in internal representations.
- Origin is centered at bottom-center `(0, 0, 0)` so models sit on the ground plane.
- Geometry builds must verify against dimension tolerances before final export.
- **Retopology is scoped, not built** — `docs/MESH_SOURCES.md` holds the
  measured audit (why dense triangulated meshes shatter the UV atlas:
  glTF per-attribute vertex splitting, NOT triangles), the tool survey
  (weld `remove_doubles` 1e-6 m is the root-cause fix; QuadriFlow works
  on welded meshes; voxel remesh collapses at voxel_size 0.004; QuadriFlow
  silently no-ops on voxel output — never chain them; the GLB round trip
  triangulates quads, so retopology must run in the live harness scene),
  the 7-point output contract, and the phased plan (R1 weld-on-import →
  R2 `retopology` spec block → R3 external backends). Phase 8.5 neural
  image-to-3D depends on R1. Evidence fixtures: `input/jobs/RETOPO0001/0002.yaml`.
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
  false non-watertightness. Same trap harness-side: a closed box imported
  from GLB shows `boundary_edges: 24` per part, so `topology_report`'s
  per-object `closed_solid` is computed on a WELDED copy
  (`_welded_closed_solid`, remove_doubles 1e-6 m); the raw edge counts stay
  in the report as file facts.
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
- **The EXACT boolean solver leaves zero-length edges behind.** Where a cut
  crosses a tri-fan cap ring it can emit coincident-but-distinct vertex pairs
  joined by zero-length edges — the live mesh stays edge-closed (and
  `validate()` strips nothing), but glTF tessellation ships them as zero-area
  triangles whose position-welded edges read non-manifold in the watertight
  check. `apply_boolean` dissolves them (`_weld_solver_duplicates`, dist
  1e-7 m); pinned in `tests/test_spec_shapes_delivery.py`.
- **Primitive caps must be TRIFAN, and the Blender 4.x parameter is
  `end_fill_type`** (`fill_type` is unrecognized) on
  `primitive_cylinder_add`/`primitive_cone_add`. NGON caps put n-gons in the
  delivery scene and `prepare_delivery_scene` refuses (client gate: strict 0).
  Extrude parts default `caps: fan` at the schema level. Same
  triangle-equivalent count, so tier ceilings are unaffected.
- **The corrector is retried, never silently dropped.** A corrector response
  failing JSON extraction or ObjectSpec validation is transient (one retry
  with the reason quoted); a real give-up records its reason into the
  manifest `unresolved_error`. Gate-failure correction prompts carry the
  measured per-part geometry table (dims/center/bottom_z/top_z) — deltas
  alone leave repositioning to guesswork.
- **The shape enum, the analyst prompt, and the harness `_build_shape`
  dispatch must stay in sync.** Shapes: box, rounded_box, cylinder,
  tapered_cylinder, sphere, cone, torus, tapered_extrude, revolve_lathe,
  extrude, sweep, organic. (`organic` cannot be built parametrically: the
  loop auto-routes parametric/script organics to `image_to_3d`; the harness
  skips an organic part with no mesh with a warning.)
- **The mesh-source contract** (Phase 8 item 3): a part declares exactly ONE
  geometry source (`method`), and its fields are entailed by that source —
  enforced fail-closed by a `PartSpec` cross-field validator. File-backed
  methods (`image_to_3d`, `imported`, `scanned`) all pass through ONE
  harness path (import → join → rescale to `target_size` → place);
  `imported`/`scanned` REQUIRE `mesh_path` + `target_size` (owner-stated —
  file units are never trusted) and differ only in provenance: a scan is
  raw capture that must be retopologized before delivery (the 8.4 hook).
  `mesh_scale: fit` (default) lands bounds exactly on `target_size`;
  `uniform` preserves aspect (one factor, no axis overshoots). The loop
  absolutizes authored mesh paths (the harness subprocess must not depend
  on the caller's CWD) and fires `mesh_source_error` when a file is
  absent. An `organic` part that already declares a file-backed source
  KEEPS it — the normalize pass must not retarget an authored mesh at
  neural generation.
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
- **The internal dimension gate verifies the analyst's own declared
  bindings, not the client's axis convention.** A spec that binds
  "length" to the Y extent passes its own gate while the client card
  (L→X, W→Y, H→Z) fails at package time with a 90° Z rotation, and the
  client dim tolerance (0.01 in the card's declared unit — ±0.01 mm for
  mm cards) is ~100× tighter than the internal ±1 mm, so an
  internally-green build can fail delivery by +0.1 mm. `src.cli build
  --job <card>` threads the card into the loop: the analyst prompt gets a
  CLIENT JOB CARD CONTRACT section (axis map + meter-converted dims +
  applies_to bindings) and `evaluate_card_axis_gate` (verifier.py) checks
  the measured overall extents against the card at the CARD's tolerance
  inside `verify_run`, so the corrector fixes both failures in-loop.
  Pinned in `tests/test_card_axis_gate.py`.
- **Polycount phrasing decides gate semantics.** "under N triangles" in a
  prompt makes intake set `polycount_semantics: triangles` — the client
  Polycount gate then counts literal triangle faces, which read ~0 on a
  quad-clean FBX (vacuous pass). Author prompts as "polycount ceiling N"
  (noun `polycount` → semantics None → the conservative
  triangle-equivalent contract default).
- **Per-surface texel priority** (Phase 8 item 1): `PartSpec.texel_priority`
  (default 1.0; `DecalSpec` defaults 4.0 for brand labels) multiplies a
  part's texel density in the shared atlas — the packer targets
  rho·prio²·world_area with rho renormalised, so total atlas use NEVER
  changes; priorities redistribute the budget. The resolver omits the field
  at 1.0 (historic build params byte-identical). In UV diagnostics the RAW
  ratio honestly reports the authored spread; `ratio_priority_weighted` is
  the uniformity metric that must stay ~1.0 — never read a raw ratio as
  starvation when the spread was authored. Pinned in
  `tests/test_texel_priority.py`.
- **The review rig is a raking instrument** (Phase 8 item 2, §H): the
  committed `setup_studio_lighting` defaults are KeyA/KeyB SUN 2.5 at
  10° elevation on perpendicular axes, fill 0.1 (a whisper — each 0.1
  fill costs ~0.5 grey levels of relief amplitude), rim 0.6/35°. Relief
  contrast scales with cot(elevation); the round-4 40° keys left the
  quilt under the floor while the FFT ratio looked healthy. Tuned under
  EEVEE Next + AgX scene defaults — a Blender upgrade that changes them
  trips the amplitude pin first (`tests/test_render_rig.py`).
- **Never tune or pin the rig on a prepared-but-unbaked GLB** (SUBSTRATE
  RULE): after `prepare_delivery_scene` the UVs are atlas-repacked while
  materials still reference SOURCE textures — normal maps then sample
  garbage and tilt the shading normals arbitrarily (crown rendered black
  under healthy file normals). Tune on a pure-form substrate (flat
  albedo, textures stripped); verify on the real baked LP. And
  discriminate form from albedo with a zero-gradient overhead sun — knit
  albedo aliased onto the quilt grid measures ~10.5 grey levels of
  SPURIOUS modulation.
- **Review contrast gates on absolute grey-level amplitude, weakest
  gated axis** (Phase 8 item 2): `src/render/metrics.py:
  measure_contrast_probe` (Hann rFFT2 at the template-authored pitch,
  ±0.6–1.4× search band; conservative ~13% at 10 cycles across from the
  detrend sinc — the floor is calibrated against THIS analyzer). A ratio
  alone must never gate (§H: ratio 0.87 while the quilt sat at 0.81/0.96
  grey levels), and neither may one axis stand in for another — the
  floor keys on min(amp_x, amp_y) with `axes: both|x|y`. Probes live in
  `templates/<class>.yaml` (`contrast_probes`, rule 11) — cycles are
  scale-invariant (0.575 × cells_across at the square view framing),
  floor 6.0 grey levels = a 12-level visible swing. Results land in
  `finish.render_metrics` (qa_report, delivered AND blocked flows) and
  the CLI panel; probe failure is loud evidence, NOT delivery refusal —
  the six client gates own refusal. Pinned in `tests/test_render_metrics.py`
  (ratio-never-gates semantics) + `tests/test_render_rig.py` (floor on
  the rendered fixture).

## Verification
- `python -m pytest tests -q` — 439 tests; `blender`-marked tests auto-skip
  when no Blender is found.
- AI builds against a client card: `python -m src.cli build -p <prompt> -m
  <measurements> -i <photo> -n <name> --job input/jobs/<CODE>.yaml` (the
  card's axis contract + delivery tolerance ride into the loop);
  batch driver: `scripts/phase7_batch.py --tag <tag>` (N jobs concurrently,
  CPU 1K bakes, per-step logs + summary.json under `output/phase7/<tag>/`).
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
- Review-render contrast evidence: every package/blocked qa_report carries
  `finish.render_metrics` (per-view balance stats + the template's
  absolute-contrast probes, grey levels with floors). Rig-physics fixture:
  `input/jobs/RIGTUNE0001.yaml` (authored flat-mattress dims — a fixture,
  not a product job) → `python -m src.cli package --template
  templates/mattress.yaml --job input/jobs/RIGTUNE0001.yaml` measures the
  real baked LP (x 9.587 / y 7.578 grey levels at the 6.0 floor).
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
