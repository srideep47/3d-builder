# 3D Builder — Master Project Plan & Handoff Document

> **This is the single source of truth for continuing 3D Builder.** It records
> the original plan, everything built so far, every hard-won technical finding,
> and the exact runbook for moving/continuing work on a new machine. Any agent
> (or human) picking this project up should read this document fully before
> changing anything. Last updated: 2026-09-01, on **Forge**, during **M4**
> (img3d neural service live, vision plug points wired, GPU bake-off started).
>
> Companion doc: **`PLAN.md`** is the original Master Plan v2 (requirements,
> full design rationale, ObjectSpec/harness/material/gate design, Appendix A
> "delta vs the Gemini draft"). This file is the living status + handoff;
> `PLAN.md` is the why. AGENTS.md is the quick operational summary.

---

## 1. What this project is

**3D Builder** turns a text description + exact real-world measurements
(and, in phase 2, reference images) into **verified, real-world-scaled 3D
models** exported as GLB. Accuracy is the product: every build is measured
back and gated against the requested dimensions before it is accepted.

- **Brain**: GLM-5.3 via the Aptos inference endpoint
  (`https://host0.inference.aptoslabs.com/v1/`, OpenAI-compatible, keyless
  for text, **text-only — NOT multimodal**, **reasoning model**).
- **Hands**: headless **Blender 4.5** harness driven by subprocess
  (one process per operation, JSON in/out).
- **Strategy**: **hybrid** — parametric construction for measured / man-made
  parts (exact dimensions), image-to-3D for organic shapes (phase 2, pluggable).
- **Verification**: closed measure-and-render loop. The dimension gate and
  mesh gate are **hard** gates; the visual gate is advisory (needs a VLM).
- **Deliverables**: autonomous CLI agent + stdio MCP server + **web studio UI**
  (`python -m src.cli ui`).

## 2. Machines & people

| Machine | Hardware | Role |
|---|---|---|
| **Forge** (PC 1, target) | RTX 4080 Super (16 GB VRAM), Ryzen 9 9950X, 64 GB RAM, Win 11 | Primary dev machine + **local inference host**. Takes over development now. Will host the img3d neural service (TRELLIS / Hunyuan3D / TripoSR bake-off) and the **local Qwen2.5-VL** vision model. |
| **Scout** (PC 2, current dev) | Ryzen 5 4600H, 40 GB DDR4, GTX 1650 Ti (4 GB VRAM), Win 11 | Where M0–M3 + Web UI were built and verified. Runs the full stack (Blender is CPU-bound); can call Forge's img3d service over LAN. Its 4 GB VRAM **cannot** run quality image-to-3D — never schedule it there. |
| Phone client | — | **Deferred. Do not plan around it yet** (explicit owner decision). |

The owner builds the vision model themselves (Qwen with VL): it will serve as
(1) the reference-image processor for the analyst and (2) the Visual Tester
that compares studio renders against the reference. The integration points are
prepared and documented in §13.

## 3. Locked decisions (do not relitigate without the owner)

1. **Approach**: hybrid — parametric-first (exact measurements), image-to-3D
   for organic parts (phase 2).
2. **Deliverables**: CLI + MCP server + web UI (web UI chosen over Electron:
   no Node toolchain, no bundler, one command to serve, LAN-reachable).
3. **Brain**: GLM-5.3 @ Aptos endpoint. Vision was claimed but is **provably
   absent** (§7.1) — the code probes and falls back; do not remove the probe.
4. **Language**: Python 3.12 (`>=3.11,<3.13`), uv + hatchling, `package = false`
   (repo is imported from repo root, not installed as a package).
5. **DCC**: Blender 4.5 headless on Windows, **portable** copy under `tools/`
   (auto-located). Substance was dropped (licensing; it's a texturing tool).
6. **Outputs**: GLB primary (Godot-friendly); FBX/OBJ/USDZ possible via the
   harness `export_any` op.
7. **Frontend**: vanilla ES modules + three.js r169 **vendored locally** under
   `web/vendor/` (no CDN, no build step). FastAPI + WebSocket backend.
8. **Repo hygiene**: `tools/` (1.3 GB Blender), `.venv/`, `output/` are
   gitignored — everything else is small (~8 MB) and safe for GitHub.

## 4. Architecture

Four layers plus the web layer:

```
┌ Interfaces ── src/cli.py (Typer) ── src/mcp_server.py (stdio MCP) ── src/webapp/ (FastAPI)
├ Agent ──────── src/agent/loop.py (tool-calling loop) · prompts.py (Analyst/Builder/
│               Verifier/Corrector roles) · verifier.py (gates) · tools.py
├ AI ─────────── src/ai/aptos.py (GLM-5.3 client, role config, reasoning control)
│               inference_log.py (JSONL log of every call) · vision_probe.py
├ Spec ───────── src/spec/schema.py (ObjectSpec v2, pydantic) · resolver.py (spec →
│               Blender build params) · validation.py (structural checks)
├ Capability ── src/blender/runner.py (subprocess) · harness_script.py (self-contained
│               Blender-side script, all ops) · locate.py (Blender discovery)
│               src/materials/pbr.py (12 Principled-BSDF presets)
│               src/img3d/ (phase-2 provider ABC + WSL client stubs)
└ Foundation ── src/run_store.py (output/runs/<id>/…) · src/pipeline.py (orchestration)
                config/{ai,defaults,hardware}.yaml
```

**End-to-end flow of one AI build** (`pipeline.generate_from_prompt`):

1. **Analyst** (GLM-5.3): prompt + measurements text → drafts an
   `ObjectSpec` (pydantic-validated JSON). One corrector retry on schema
   validation failure.
2. **Iterative loop** (max iterations / wall-clock budget from config):
   - `validate_spec_structure` → auto-correct structural errors
   - resolver → `build_from_spec` Blender op → `steps/step_N.glb`
   - `measure` op (reads back real dimensions) and `render_views` op
     (front/side/top/iso PNGs) — both **must be pointed at the step GLB**
   - `Verifier.verify_run` → dimension gate + mesh gate
   - pass → copy to `final.glb`; fail → **Corrector** (GLM-5.3) rewrites the
     spec using the gate feedback; loop
3. **Finish**: run manifest (`manifest.json`) with gate results, metrics,
   status (`completed` / `completed_with_warnings` / `cancelled` /
   `budget_exhausted`), renders, spec.json.

**Origin convention**: all geometry sits on the ground plane — origin at
bottom-center `(0, 0, 0)`. All lengths are **meters** internally.

## 5. Milestones — plan vs. status

| Milestone | Scope | Status |
|---|---|---|
| **M0 — Environment proof** | scaffold, locate.py, harness lift, smoke script | ✅ done (Blender 4.5.13 portable) |
| **M1 — Deterministic core** | ObjectSpec v2, resolver, build/measure/render ops, run store, gates, CLI `build --spec` | ✅ done |
| **M2 — Agent** | Aptos provider, tool loop, analyst/builder/verifier prompts, self-correction, live e2e | ✅ done (incl. reasoning-model taming, §7.1) |
| **M3 — MCP + polish** | stdio MCP server (7 tools), materials/PBR, docs, golden benchmarks (dimensions.com) | ✅ done |
| **M3.5 — Web studio UI** | FastAPI + WS + three.js studio: input → live progress → output | ✅ done (browser-verified e2e, §6) |
| **M4 — Vision + image-to-3D** | Qwen2.5-VL local (owner-built) as analyst eye + visual gate; img3d provider bake-off; hybrid routing | 🔶 **in progress** (§13): img3d service + hybrid routing ✅ done & live-verified; TripoSR GPU backend wired; vision plug points ✅ wired (awaiting owner's Qwen-VL server); TRELLIS/Hunyuan3D bake-off slots registered |

## 6. Current verified state (as of 2026-09-01, on Forge)

- **Machine transfer executed**: Forge set up per §12 (uv venv 3.12.14,
  Blender 4.5.13 portable in `tools/`), health green, **51/51 tests**, AI
  benchmark **4/4** — parity with Scout confirmed before any M4 work.
- **Tests: 68/68 pass** (`python -m pytest tests -q`):
  - the original 51 (schema, resolver, validation, utils, harness smoke,
    golden benchmarks, webapp API)
  - `test_img3d.py` (13): service API + auth (TestClient), client e2e over a
    live uvicorn thread, AgentLoop neural wiring (generate/cache/skip/normalize),
    full hybrid Blender build (parametric pedestal + neural blob → gates green)
  - `test_vlm.py` (4): LocalVLMClient against a mock OpenAI-compatible server,
    visual-gate wiring, fail-soft when unconfigured
- **AI benchmark**: still 4/4 in a single iteration after the M4 prompt changes.
- **img3d service** (`services/img3d_service/`, PLAN.md §9): FastAPI,
  single-job GPU queue, `/health` `/models` `/generate` `/result/<id>`
  `/download/<id>`, optional bearer token for LAN. Backends: `mock`
  (deterministic, live-verified), `tripo_sr` (GPU, wired — see §13.2),
  `trellis` / `hunyuan3d` (registered slots for the bake-off).
- **Hybrid routing live-verified end-to-end** (spec mode): spec with an
  `image_to_3d` part → loop generates the mesh via the service → caches under
  `run_dir/neural/` → harness imports + scales to `target_size` → dimension +
  mesh gates green at 0.0 mm delta. AI mode: analyst routes organic parts to
  `image_to_3d` (with deterministic normalization for missed routing, §7.7).
- **Vision plug points wired** (§13.1): `src/ai/vlm.py` LocalVLMClient
  (OpenAI-compatible), analyst enrichment + advisory visual gate + manifest
  `metrics.visual_verdict` + web-UI timeline events. Inert until the owner
  stands up Qwen2.5-VL and sets `vision.vlm.base_url`/`model` in ai.yaml.
- MCP server: 8 tools (added `image_to_3d`; health now reports the img3d
  service). CLI: new `img3d` command (live-verified against the service).
- Carried over from M3.5 (still valid): MCP verified over stdio; web studio
  browser-verified end-to-end (evidence: `docs/gui-screenshots/01…07*.png`);
  single AI build wall-clock **~10–18 s** typical (was 315 s before reasoning
  tuning — see §7.1).

## 7. The gotchas ledger (hard-won knowledge — read before touching)

### 7.1 AI / GLM-5.3 / Aptos endpoint

- The endpoint serves **zai-org/GLM-5.3 only**, keyless for text. It is
  **not multimodal**: image chat returns HTTP 400 "not a multimodal model".
  `--image` falls back to text-description mode; `vision_probe.py` guards
  this at runtime (config `vision.mode: auto`).
- It **is a reasoning model**: reasoning tokens are consumed **before**
  content. Symptoms of exhaustion: `finish_reason: "length"` with **empty
  `content`** (reasoning lands in a separate `message.reasoning` field).
  Fix: per-role token budgets in `config/ai.yaml` (analyst/builder 16384,
  verifier 8192) resolved by `_resolve_role_params()` in `src/ai/aptos.py`
  (explicit args > `roles.<role>` > global > defaults).
- **`reasoning_effort: "low"`** (sent via `extra_body`) cut latency ~30×
  (90–180 s → ~6 s per call) with equal output quality. It is configurable
  per role in `config/ai.yaml`. This is the single most important AI setting.
- Prompts for analyst/corrector open with a "REASONING DISCIPLINE: think
  briefly…" directive — keep it; it keeps reasoning short.
- The corrector can burn its whole budget reasoning about a failure and
  return nothing; the loop treats empty content as a failed correction.
- Every call is logged to `output/inference_log.jsonl` (gitignored).
- **Known variance**: the analyst occasionally ignores given measurements
  (seen once on the mug: body 0.148 instead of 0.10). The dimension gate
  catches it and the corrector usually fixes it within the iteration budget;
  occasionally it ends `completed_with_warnings`. This is LLM variance, not a
  code bug — improving analyst adherence is a valid future task (e.g.
  echoing measurements into the spec contractually, or a cheap deterministic
  post-check that scales parts to declared targets).

### 7.2 Blender harness (`src/blender/harness_script.py`)

- **One Blender process per op.** There is no shared scene state between
  ops — `measure` and `render_views` MUST receive `model_path` pointing at
  the exported GLB. Args go via JSON temp files; results come back
  sentinel-framed JSON on stdout (folder name contains a space — never use
  shell strings, always arg lists).
- **Background mode staleness**: after moving objects, `obj.matrix_world` is
  stale until `bpy.context.view_layer.update()`. Symptom: radial-array clones
  collapsed at the origin / wrong world bounding boxes. The harness has
  `_update_view()` called at the top of `world_bbox()` and
  `_clone_with_matrix()`.
- **Booleans**: after `apply_boolean`, tool objects become stale RNA refs.
  Filter by **identity** (`o is not tool_obj`), never by name, when pruning
  object lists.
- **Revolve/lathe**: pole vertices must be **shared** across rings (one vert
  per pole position), pole faces are triangles, zero-width axis bands are
  skipped, and `_fill_boundary_loops` runs before `remove_doubles` —
  otherwise the result is non-watertight.
- **glTF naming**: mesh datablock names come from `obj.data.name`, not
  `obj.name` — set both or exports contain "Cube".
- Harness ops include: `reset_scene`, `import_any`/`export_any`,
  `describe_scene`, `create_primitive`, `build_from_spec`, `measure`,
  `render_views`, `run_script` (LLM-authored Python escape hatch), and the
  cleanup chain (decimate → UV → scale_to_size → center_origin).

### 7.3 Measuring exported GLBs (trimesh)

- glTF export **splits vertices** per normal/UV attribute, and part positions
  live in **node transforms** — a naive `trimesh.load` + `concatenate`
  produces wrong bounding boxes, and trimesh 5.0.0's instance
  `merge_vertices()` does **not** merge scene-loaded geometry.
- The correct recipe is `load_merged_mesh()` in `src/agent/verifier.py`:
  `scene.to_mesh()` then `trimesh.Trimesh(vertices, faces, process=True)`.
  Any new mesh gate must use this helper.

### 7.4 ObjectSpec / resolver

- Spec schema is **v2.0.0** (`src/spec/schema.py`): top-level `measurements`
  with `{name, target_value, unit, applies_to}` (e.g.
  `applies_to: "overall.height_z"` or `"seat.width_x"`), `tolerance_m`
  (default 0.001 = ±1 mm).
- `ShapeType` enum must stay in sync with what prompts advertise and the
  harness implements: box, rounded_box, cylinder, tapered_cylinder, sphere,
  cone, torus, tapered_extrude, revolve_lathe, extrude, sweep, organic.
  (Once drifted and schema validation rejected valid specs.)
- Mismatched part names in `applies_to` yield `actual: None` in gate details —
  check spec part names first when a gate mysteriously reports None.

### 7.5 MCP

- MCP 2.x renamed `FastMCP` → `MCPServer` (`from mcp.server.mcpserver import
  MCPServer`). `src/mcp_server.py` imports with a 1.x fallback. 7 tools:
  `analyze_reference`, `build_model`, `run_blender_op`, `measure_model`,
  `render_model`, `image_to_3d`, `list_runs`/`get_run`.

### 7.6 Web UI

- **three.js r169 is vendored** under `web/vendor/` with an importmap
  (`"three"`, `"three/addons/"`). GLTFLoader imports
  `three/addons/utils/BufferGeometryUtils.js` — if that file is missing the
  **whole module graph fails silently** (page renders but no JS runs:
  no presets, no health dots, no canvas). It is committed; a test asserts it
  is served.
- The agent emits **absolute Windows paths** for renders. The frontend
  normalizes them via `toRenderUrls()` (basename →
  `/api/runs/{id}/file/renders/<name>`); the runs API converts manifest
  paths server-side. Both paths must stay in sync.
- **WebSocket race**: the server subscribes the client queue BEFORE
  replaying history so no event is lost (duplicates tolerated client-side).
  Keep this order.
- Runs execute on **daemon threads** in `RunRegistry`
  (`src/webapp/runner.py`); events cross into asyncio via
  `loop.call_soon_threadsafe(queue.put_nowait, ev)`.
- `GET /api/runs` merges persisted manifests under the in-session registry
  view (registry status is fresher; manifests carry dims/tris/date).
  `active_runs()` reports `live = status == "running"`.
- Cancel: client POSTs `/api/runs/{id}/cancel` → registry sets a
  `threading.Event` → the loop checks `cancelled()` between stages → run
  finishes with status `cancelled` (loop `_finish(user_cancelled=…)`).
- Render `<img loading="lazy">` inside hidden tab panes load only when the
  tab becomes visible — 0×0 natural size while hidden is expected, not a bug.
- **Unknown progress events are safe by design**: the event switch falls
  through to the timeline log. New events (`neural_part_*`, `neural_skipped`,
  `visual_gate`) have proper step cases in `web/js/app.js` (STAGE_LABELS +
  switch) — keep adding cases there when introducing events.

### 7.7 img3d / neural parts / GPU service (M4)

- **Two Python environments, never mixed** (3DGuy's rule, PLAN.md §1): main
  env (agent, Blender runner, MCP — light deps) and the service env
  `services/img3d_service/.venv` (Python 3.11 + torch 2.6 cu124 + model
  deps). The service imports its backends lazily so the mock backend runs in
  either env; torch-touching code only imports inside `load()`/`generate()`.
- **torchmcubes does not build on Windows** (CUDA extension). TripoSR needs
  it in exactly one place (marching cubes over a scalar grid). Fix:
  `_install_torchmcubes_shim()` in `providers/tripo_sr.py` installs a
  scikit-image-based module shim into `sys.modules` — only when the real
  extension is absent. Do not "clean this up" by patching the vendored repo.
- **`rembg` does not import without an onnxruntime backend** — plain
  `pip install rembg` is not enough; `onnxruntime` (CPU build) must be
  installed explicitly, or `import tsr.utils` fails at module level.
- **`TSR.from_pretrained` has a fixed signature** (no cache_dir kwargs) and
  pulls weights via `hf_hub_download`. Redirecting the cache into `models/`
  requires `HF_HUB_CACHE` to be set **before `huggingface_hub` is first
  imported** (it reads the env at import time) — the backend sets it in
  `__init__` with `setdefault` (never override a user's env).
- **Analyst routing variance**: the analyst sometimes sets `shape: "organic"`
  but leaves `method: "parametric"` (default). The loop normalizes this
  deterministically (`_normalize_spec_methods`: organic ⇒ image_to_3d) and
  the harness degrades shape-organic-without-mesh to a warning skip instead
  of the old hard "Unknown shape 'organic'" build error.
- **The analyst hallucinates `image_crop` paths** (e.g.
  `reference_image_1.jpg`). `_analyst_spec` now lists the REAL image paths in
  the user message, and `_resolve_part_image` falls back to spec source
  images → run images when `image_crop` doesn't exist. Never trust
  `image_crop` alone.
- **Corrector rewrites drop `mesh_path`** (it rewrites the whole spec JSON).
  Neural meshes are cached by part name under `run_dir/neural/` and
  re-attached at the top of every iteration (`_reattach_neural_meshes`) —
  no regeneration. The harness re-scales imported meshes to the part's
  CURRENT `target_size`, so dimension corrections on neural parts converge
  without regeneration (the corrector prompt says: fix neural parts via
  `target_size`).
- **Progress-event signature split**: the public `progress` callback takes a
  single dict; the loop-internal `emit(event, **data)` wraps it. Tests that
  call `_prepare_neural_parts` directly must pass an emit-shaped callable
  (`_collect()` in `tests/test_img3d.py`), not `list.append`.
- **TripoSR expects RGB on white background**; the backend composites alpha
  over white with PIL instead of pulling rembg into the inference path.

## 8. Web studio — structure & API surface

Launch: `python -m src.cli ui` → http://127.0.0.1:8137 (options: `--port`,
`--no-open`, `--reload`). Server: `src/webapp/server.py`
(`create_app()`), registry: `src/webapp/runner.py`, frontend: `web/`
(`index.html`, `css/app.css`, `js/app.js` state machine, `js/viewer.js`
ModelViewer class).

REST/WS surface:
`GET /api/health` · `GET /api/presets` · `POST /api/uploads` (multipart) ·
`POST /api/build` (`{mode: ai|spec, prompt, measurements, material_preset,
images}` / `{mode: spec, spec}`) · `POST /api/runs/{id}/cancel` ·
`GET /api/runs` · `GET /api/runs/{id}` ·
`GET /api/runs/{id}/file/{rel_path}` (safe-join, GLB/PNG mimes) ·
`WS /api/ws/{id}`.

Progress event vocabulary (emitted by `AgentLoop.run(progress=…)`):
`run_started`, `analyst_started/done/error`, `iteration_started`,
`build_started/done/error`, `measure_done`, `render_done`, `verification`,
`correction_started/done`, `cancel_requested`, `run_finished`, `run_error`.

Frontend views: **Build** (AI/Spec modes, measurements "accuracy contract",
material presets, drag-drop images), **Runs** (history table → reopen),
**System** (Blender/AI health, config, Qwen-VL plug-point documentation).
Output tabs: 3D View (orbit/wireframe/rotate/reset), Renders, Gates
(dimension table + mesh facts), Spec JSON, Log.

## 9. Golden benchmarks & dimensions.com

`input/benchmarks/` holds spec files grounded in real-world dimensions
(sources in `input/benchmarks/README.md`, primary source
[dimensions.com](https://www.dimensions.com)):

- **counter_stool**: seat Ø 0.38 at 0.635, 4 tapered legs 0.04 × 0.61
  (dimensions.com counter-stool seat range 23–28 in verified live)
- **coffee_table**: top 1.2 × 0.6 × 0.04 at z 0.38, 4 legs 0.05 × 0.36
- **coffee_mug**: body Ø 0.095 × 0.10 with boolean-intersected inner void,
  torus handle
- **desk** (`input/sample_desk.spec.json`): 1.4 × 0.7 × 0.76

`scripts/benchmark_golden.py` scores the **AI flow** (prompt +
measurements only) against these targets; deterministic spec builds are
covered by pytest. When adding benchmarks, keep prompts and golden spec
dimensions aligned (a mismatch once produced phantom "misses").

## 10. Conventions & operational rules

- **Units: meters** everywhere internal. **Origin: bottom-center (0,0,0)**,
  models sit on the ground plane.
- **Tolerance**: default ±1 mm (`tolerance_m: 0.001`); gates enforce it.
- Geometry builds must verify against dimension tolerances before export.
- Never use shell strings for subprocess (folder name has a space).
- `run_script` executes LLM-authored Python — same explicit, user-authorized
  trust model as the rest of the tooling; typed ops remain the default path.
- Commit hygiene: plain commits under the owner's identity; no AI
  attribution/trailers (owner requirement).
- Windows-first (paths, Blender portable); keep POSIX-compat where free.

## 11. Known issues & limitations

1. Endpoint is text-only → reference images are stored but not analyzed
   (until Qwen VL lands, §13.1 — the LocalVLMClient integration is wired and
   inert). UI shows an amber "text-only mode" dot.
2. Analyst measurement-adherence variance (§7.1 last bullet) — mug-class
   objects (hollow interiors) are the hardest; gates catch it, corrector
   usually fixes it, occasionally ends `completed_with_warnings`.
3. `run.error` on cancelled runs says "Cancelled by user" and status is
   `cancelled` — by design, not a failure.
4. The runs registry keeps every run in memory for the server session
   (needed for WS replay); fine for a studio tool, revisit if it ever matters.
5. img3d: `trellis` / `hunyuan3d` backends are registered slots only —
   the bake-off (§13.2) installs them. `tripo_sr` is wired and GPU-verified.
6. If the img3d service is down, specs with `image_to_3d` parts emit
   `neural_skipped` and the harness skips those parts with a warning (gates
   then fail on their measurements) — the run does not crash. A dead service
   costs one short health probe per process.
7. The mock backend's blob is a displaced icosphere — useful for pipeline
   verification, NOT a quality reference. Real quality comes from tripo_sr+.
8. Bake-off reference images in `input/bakeoff/` are synthetic placeholders;
   replace with real object photos for a meaningful TRELLIS/Hunyuan/TripoSR
   comparison.

## 12. Transfer runbook — setting up on Forge

The GitHub repo contains **everything except** `tools/` (Blender, 1.3 GB),
`.venv/`, and `output/` (regenerable). Fresh setup on Forge:

```powershell
# 1. Clone (private repo, owner account)
git clone https://github.com/srideep47/3d-builder.git
cd 3d-builder

# 2. Python env (requires uv; https://docs.astral.sh/uv/)
uv venv
uv sync                      # installs deps from pyproject.toml (package=false)

# 3. Blender — either:
#    a) run the setup script (downloads Blender 4.2 LTS portable into tools/):
powershell -ExecutionPolicy Bypass -File scripts/setup-blender.ps1
#    b) or match the dev machine exactly — Blender 4.5.13 portable (~380 MB):
#      https://download.blender.org/release/Blender4.5/blender-4.5.13-windows-x64.zip
#      extract into tools/blender-4.5.13-windows-x64/
#    (locate.py sweeps tools/blender*; env THREED_BLENDER overrides; any 3.3+ works)

# 4. Verify everything
.venv/Scripts/python -m src.cli health        # endpoint + Blender + vision status
.venv/Scripts/python -m pytest tests -q       # must be 51/51 (Blender tests skip if absent)

# 5. Run the studio
.venv/Scripts/python -m src.cli ui            # http://127.0.0.1:8137
```

Alternative to re-downloading Blender: copy the `tools/` folder from the old
machine over LAN/USB — it is self-contained.

**img3d GPU service (Forge only, optional for M4)**:
```powershell
uv venv services/img3d_service/.venv --python 3.11
uv pip install -r services/img3d_service/requirements-gpu.txt -p services/img3d_service/.venv
powershell -ExecutionPolicy Bypass -File scripts/setup-img3d-gpu.ps1   # vendors TripoSR
scripts/start-img3d.ps1 tripo_sr          # weights download on first generation
```

No API keys are needed (Aptos endpoint is keyless for text). No secrets are
committed; `config/ai.yaml` holds only endpoint/model/budgets.

## 13. Roadmap — M4 (in progress)

### 13.1 Qwen2.5-VL vision model (owner-built, on Forge)

**Integration points are DONE and inert** — they activate the moment the
owner stands up the model (no code changes needed):

1. **Reference analysis** — `src/ai/vlm.py` `LocalVLMClient`
   (OpenAI-compatible, httpx). `AgentLoop._analyst_spec` calls
   `describe_reference_images()` when GLM vision is unavailable and the
   local VLM answers; the description grounds the analyst's spec.
2. **Visual Tester / visual gate** — `AgentLoop._run_visual_gate` runs on
   the passing iteration, compares renders vs references, emits a
   `visual_gate` event (web UI timeline step) and records the verdict in
   `manifest.metrics.visual_verdict`. Advisory — never blocks a run.
3. **Config** — `config/ai.yaml` `vision.vlm` section (`base_url`, `model`,
   `api_key_env`, `timeout_sec`); `vision.mode` controls the GLM endpoint
   probe as before. Set both `base_url` and `model` to activate.
4. Serve Qwen2.5-VL (vLLM or any OpenAI-compatible server) on the 4080
   Super; verified against a mock server in `tests/test_vlm.py`.

### 13.2 Image-to-3D (organic parts) — per PLAN.md §9

**Done**: FastAPI service (`services/img3d_service/`) on Forge —
`/health`, `/models`, `/generate` → job id, `/result/<id>`, `/download/<id>`,
single-job GPU queue, model loaded once, weights under `models/` (gitignored),
optional LAN token. `RemoteImg3DProvider` client (httpx, polling) +
`get_img3d_provider()` factory in `src/img3d/`. Hybrid routing in the agent
loop (§7.7). MCP `image_to_3d` tool + CLI `img3d` command. Bake-off harness:
`scripts/bakeoff_img3d.py` (scores time / tris / watertightness / scale
accuracy per backend, JSON report under `output/`).

**Backend status**:
- `mock` ✅ (deterministic, CPU, pipeline verification)
- `tripo_sr` ✅ wired (vendored repo + HF weights, torchmcubes scikit-image
  shim for Windows, GPU env in `services/img3d_service/.venv`)
- `trellis` ⬜ / `hunyuan3d` ⬜ registered slots — remaining M4 work:
  install each on the GPU env, implement the backend, run the bake-off on
  `input/bakeoff/` (replace synthetic images with real photos first).

**Neural mesh post-processing (always)**: backends decimate to `max_tris`
when the simplifier is available and scale to exact target bounds; the
harness re-scales to the part's `target_size` on import and generates UVs
(`generate_uvs: True` in the resolver). Neural output is never shipped raw.

### 13.3 Remaining M4 backlog

- Complete the TRELLIS / Hunyuan3D-2.1 legs of the bake-off; pick the
  default `IMG3D_MODEL` based on the report.
- Owner: serve Qwen2.5-VL and set `vision.vlm` in `config/ai.yaml` (§13.1).
- Analyst measurement-adherence hardening (deterministic scale-to-target
  post-check) — see §7.1.
- Multi-format export exposure in the UI (harness already supports it).
- Benchmark growth from dimensions.com (more object classes).
- Browser-verification pass of the web UI with neural events (evidence in
  `docs/gui-screenshots/`).

## 14. File map

```
src/ai/         aptos.py (GLM-5.3 client + role config + reasoning control),
                provider.py (ABC), schemas.py, inference_log.py,
                vision_probe.py, vlm.py (local Qwen-VL client + visual gate)
src/agent/      loop.py (instrumented run loop: progress/cancel/run_dir +
                neural-part generation + visual gate), prompts.py,
                verifier.py (gates + load_merged_mesh), tools.py
src/spec/       schema.py (ObjectSpec v2), resolver.py, validation.py
src/blender/    locate.py, runner.py, harness_script.py (all Blender ops)
src/materials/  pbr.py (12 presets)
src/img3d/      provider.py (ABC), client.py (RemoteImg3DProvider + factory)
src/webapp/     server.py (FastAPI REST+WS+static), runner.py (RunRegistry)
src/            pipeline.py, run_store.py, cli.py, mcp_server.py
services/img3d_service/  app.py (FastAPI GPU service), providers/
                (base, mock, tripo_sr, trellis, hunyuan3d), README.md,
                requirements.txt (light) / requirements-gpu.txt (torch cu124),
                .venv/ (GPU env, gitignored), vendor/TripoSR (gitignored)
web/            index.html, css/app.css, js/app.js, js/viewer.js,
                vendor/ (three.js r169 + addons — see §7.6)
input/          benchmarks/*.spec.json (golden), sample_desk.spec.json,
                bakeoff/ (img3d bake-off reference images)
config/         ai.yaml (endpoint/roles/budgets/reasoning_effort/vision.vlm),
                defaults.yaml, hardware.yaml (role + img3d service)
scripts/        benchmark_golden.py, bakeoff_img3d.py, setup-blender.ps1,
                setup-img3d-gpu.ps1, start-img3d.ps1, blender-smoke.ps1
tests/          68 tests across 9 files (§6)
docs/           gui-screenshots/ (web UI browser-verification evidence)
output/         (gitignored) runs/<id>/{final.glb, spec.json, manifest.json,
                renders/, steps/, neural/}, uploads/, inference_log.jsonl,
                bakeoff/ (bake-off artifacts)
models/         (gitignored) img3d weights (HF cache redirect)
tools/          (gitignored) portable Blender
```

## 15. Command reference

```powershell
.venv/Scripts/python -m src.cli health                  # endpoint/Blender/vision status
.venv/Scripts/python -m src.cli build --spec input/benchmarks/counter_stool.spec.json
.venv/Scripts/python -m src.cli build --prompt "..." --measurements "overall height 0.45 m"
.venv/Scripts/python -m src.cli measure output/runs/<id>/final.glb
.venv/Scripts/python -m src.cli render  output/runs/<id>/final.glb
.venv/Scripts/python -m src.cli img3d   input/ref.png --target 0.3,0.2,0.15
.venv/Scripts/python -m src.cli runs list | runs show <id>
.venv/Scripts/python -m src.cli presets                  # 12 material presets
.venv/Scripts/python -m src.cli mcp                      # stdio MCP server
.venv/Scripts/python -m src.cli ui                       # web studio (:8137)
.venv/Scripts/python -m pytest tests -q                  # 68 tests
.venv/Scripts/python scripts/benchmark_golden.py         # AI benchmark
.venv/Scripts/python scripts/bakeoff_img3d.py            # img3d backend bake-off

# img3d service (start BEFORE builds with image_to_3d parts):
scripts/start-img3d.ps1              # mock backend (main env)
scripts/start-img3d.ps1 tripo_sr     # GPU backend (service venv, port 8501)
```

## 16. Rules for the next agent

1. Read §7 (gotchas) before touching AI, Blender, or measurement code —
   every entry there cost hours.
2. Never weaken a gate to make a build pass. Fix the spec/build instead.
3. Meters, bottom-center origin, ±1 mm default tolerance — non-negotiable.
4. Keep `ShapeType`, prompts, and harness ops in sync.
5. Any change to run events or artifact paths must keep the web UI, CLI,
   and MCP views consistent (they share the run store).
6. Run the full test suite before committing; keep it green.
7. Commits go under the owner's identity only — no AI attribution trailers.
8. Phone client: still deferred. Do not plan around it.
