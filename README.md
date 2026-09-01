# 3D Builder — AI 3D Model Generation

> ## START HERE
>
> Active work is the **autonomous model-creation system**. Read in this order:
>
> | Order | File | For |
> |---|---|---|
> | 1 | [`PLAN_AUTONOMOUS.md`](PLAN_AUTONOMOUS.md) | Everyone — scope, architecture, 24-hour plan, go/no-go |
> | 2 | [`docs/DESKTOP_SETUP.md`](docs/DESKTOP_SETUP.md) | Machine runbook. **Do this first on a new host** |
> | 3a | [`HANDOFF_CLAUDE_DESKTOP.md`](HANDOFF_CLAUDE_DESKTOP.md) | Reviewer / architect seat — includes the measurement recipes |
> | 3b | [`HANDOFF_GLM_AUTONOMOUS.md`](HANDOFF_GLM_AUTONOMOUS.md) | Builder seat — the work order |
> | 4 | [`docs/VISION_CONFIG.md`](docs/VISION_CONFIG.md) | Gemini limits, cost, tiering, 429 handling |
> | 5 | [`PROGRESS.md`](PROGRESS.md) | Current task-by-task state and the gotchas ledger |
>
> Two things gate everything: **enable billing on the Gemini key before any
> vision call** (free tier trains on submissions), and **confirm GPU Cycles
> baking works** on the new host before trusting any throughput estimate.

Turn **prompts + precise measurements** (and, later, reference images) into
verified, real-world-scaled 3D models. The brain is **GLM-5.3** on the Aptos
endpoint; the hands are a **headless Blender 4.5 harness**; a closed
**build → measure → gate → correct** loop enforces the accuracy contract.

## How it works

```
prompt + measurements ──► GLM-5.3 analyst ──► ObjectSpec v2 (pydantic)
                                                      │
              ┌───────────────────────────────────────┘
              ▼
     Blender harness (one process per op)
     build_from_spec → measure → render_views
              │
              ▼
     Verification gates
     • dimension gate: every declared measurement within tolerance (±1 mm)
     • mesh gate: watertight, triangle budget, real-world scale
              │
      pass ───┴── fail ──► corrector (delta feedback) ──► rebuild (≤5 iterations)
              │
              ▼
     output/runs/<id>/ final.glb + renders/ + manifest.json + spec.json
```

- All lengths in **meters**; origin at **bottom-center** so models sit on Z = 0.
- `position` is the part **center** for box/cylinder/sphere-family shapes and
  **bottom-center** for `tapered_extrude` / `revolve_lathe` / `extrude` / `sweep`.
- Modifiers: bevel, subdivision, radial/linear arrays, world mirror, boolean
  cuts. Materials: flat PBR presets that survive GLB export (plus a
  `bake_materials` op for procedural shaders).

## Quick start

```powershell
# 1. Portable Blender lives in tools/ (auto-located; no install needed)
python -m src.cli health                       # endpoint + Blender + vision status

# 2. Deterministic build from a spec (no AI needed)
python -m src.cli build --spec input/sample_desk.spec.json

# 3. AI build: prompt + measurements -> analyst -> verified GLB
python -m src.cli build --prompt "A simple three-legged wooden stool with splayed round legs and a round seat" `
                        --measurements "seat diameter 0.30 m, overall height 0.45 m, leg diameter 0.035 m"

# 4. Measure / render any model
python -m src.cli measure output/runs/<id>/final.glb
python -m src.cli render  output/runs/<id>/final.glb

# 5. Inspect runs
python -m src.cli runs list
python -m src.cli runs show <id>

# 6. MCP server (stdio) for ZCode or any MCP client
python -m src.cli mcp

# 7. Web UI — the studio (browser opens automatically)
python -m src.cli ui                              # http://127.0.0.1:8137
python -m src.cli ui --port 9000 --no-open       # custom port, no browser
```

## Web UI (studio)

`python -m src.cli ui` serves a dark-theme studio on `http://127.0.0.1:8137`
(FastAPI + WebSocket backend, vanilla three.js frontend — no build step).

- **Build** — two modes: *AI Build* (prompt + the measurements accuracy
  contract + optional reference images and a material preset) or *From Spec*
  (paste/load an ObjectSpec JSON, validated before submission).
- **Live progress** — every agent stage streams over a WebSocket into a
  timeline: analyst → build → measure → render → gates → corrector loops,
  with a running timer and a cancel button that actually stops the run.
- **Output** — five tabs: interactive 3D viewer (orbit / wireframe /
  auto-rotate, GLB auto-loads on success), the four studio renders, the
  dimension + mesh gate tables (target / actual / Δ mm), the resolved
  ObjectSpec JSON, and the raw event log. Download button serves `final.glb`.
- **Runs** — full history with status, dimensions, tri counts and dates;
  click any row to reopen its artifacts. **System** — Blender / AI provider
  health, config, and the plug point for the planned local Qwen2.5-VL
  vision stage (reference analysis + visual gate).

REST surface (also used by the UI): `GET /api/health`, `GET /api/presets`,
`POST /api/uploads`, `POST /api/build`, `POST /api/runs/{id}/cancel`,
`GET /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/file/{path}`,
`WS /api/ws/{id}`.

## Verification & benchmarks

```powershell
python -m pytest tests -q               # 51 tests; Blender tests auto-skip if absent
python scripts/benchmark_golden.py      # AI benchmark: measurements-only -> gates
```

Golden benchmarks (`input/benchmarks/`) use real-world dimensions from
[dimensions.com](https://www.dimensions.com) — counter stool (seat height
0.66 m), coffee table (1.2 × 0.6 × 0.40 m), coffee mug (Ø 0.095 × 0.10 m) —
and must pass both gates deterministically. The AI benchmark scores how often
the analyst-only flow lands within tolerance of the same targets.

## Endpoint reality (verified live)

- `https://host0.inference.aptoslabs.com/v1` serves **zai-org/GLM-5.3 only**,
  keyless for text.
- It is **not multimodal** (HTTP 400 "not a multimodal model"): `--image`
  currently falls back to text-description mode. A local VLM (Qwen2.5-VL on
  the 4080 Super PC) is the planned path for true reference-image analysis.
- It **is a reasoning model**: budgets come from `config/ai.yaml`
  (`roles.<role>.max_tokens`, analyst = 16384) because reasoning consumes
  tokens before content — small budgets return empty content with
  `finish_reason: length`.

## Configuration

- `config/ai.yaml` — endpoint, model, per-role token budgets, agent iteration
  and wall-clock budgets, vision mode (`auto` probes and falls back).
- `config/defaults.yaml` — tolerances and render defaults.
- Blender location: `tools/blender-*/blender.exe` is found automatically; set
  `THREED_BLENDER` to override.

## Repository layout

```
src/ai/        Aptos GLM provider, inference log, vision probe
src/agent/     analyst/builder/verifier loop, prompts, tools, gates
src/spec/      ObjectSpec v2 schema, resolver, validation
src/blender/   locate, subprocess runner, self-contained harness script
src/webapp/    FastAPI server + threaded run registry (REST + WS)
src/           pipeline, run store, CLI, FastMCP server
web/           studio frontend (index.html, css, js, vendored three.js)
input/         golden benchmark specs (dimensions.com-sourced)
output/runs/   per-run artifacts: final.glb, renders/, manifest.json
```
