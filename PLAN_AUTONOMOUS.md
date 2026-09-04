# PLAN — Autonomous Model Creation System

> The master plan. Read this first, then your seat's handoff:
> reviewer → `HANDOFF_CLAUDE_DESKTOP.md`, builder → `HANDOFF_GLM_AUTONOMOUS.md`.
> Machine setup → `docs/DESKTOP_SETUP.md`. Vision config → `docs/VISION_CONFIG.md`.
>
> Written 2026-09-02 on the laptop, immediately before the move to the desktop.
> Owner: srideep. Target: working system producing assets 24 hours from the move.

---

## 1. The goal, in the owner's words

A system where:

1. A reference image is supplied.
2. A detailed prompt describes what to build — **including measurements**, and
   **every** output constraint (polycount ceiling, size caps, formats, axis
   convention). All of it dynamic, supplied per job, never hardcoded.
3. Prompt + images go into the system.
4. A main AI model drives it **over an API** — GLM-5.3 (fast, no vision) with
   Gemini for the eyes.
5. **No ZCode and no Claude Code in the runtime.** Those are build-time tools only.
6. The AI has tools to build 3D models in Blender.
7. It analyses the images and the prompt.
8. Its only job, at any cost, over any number of turns: make the model match
   the reference accurately.
9. It has both creation tools **and** verification tools so it can see its own work.
10. It uses them in a loop — create, check, fix, repeat.
11. The owner supplies textures.
12. The AI uses them to reach the highest quality.
13. It tests the model from all sides against the job's QA constraints.
14. Results come out.

Throughput target: **15 models in a 10–16 hour day.**

## 2. Scope — what is in and what is not

### IN for the first 24 hours

**Static hard-surface objects.** Bed, bar, table, cabinet, shelf, box, crate,
lamp base, appliance, packaging, and soft goods that are band-structured
(mattress, cushion, pillow). Anything expressible as primitives, booleans,
sweeps, lathes and extrusions.

### OUT, and not negotiable inside 24 hours

**Organics — bird, animal, plant, person — and vehicles.** These need
image-to-3D generation plus a **retopology** stage. There is no retopology
anywhere in this repo (verified: no `retopo`, `quadriflow` or `remesh` in
`src/` or `services/`). Every gate we own — quad-clean, zero n-gons,
polycount — passes today only because parametric output is *born* clean. Feed
in a dense triangulated organic mesh and it fails all of them.

Retopology is weeks of work, not hours. Adding TRELLIS 2 or Hunyuan3D without
it produces a path that looks like it works and ships unusable assets. See §9
for the staged plan.

**Do not let scope drift here.** "Ready for any model" is the roadmap, not the
24-hour deliverable.

## 3. Verified state at handoff

Branch `wip/defect-fixes`, commit `c8e1e18` plus this documentation commit.

- **250 tests passing.** Re-run independently on the laptop, not merely reported.
- `python -m src.cli health` green. Blender 4.5.13.
- No API key in any commit.
- Mattress asset at ~95% on structure. Remaining items are detail/polish and
  are listed in §8.

### What already exists and works

| Capability | Where |
|---|---|
| Deterministic Blender harness, one process per op | `src/blender/harness_script.py` |
| 12 shape primitives + spec schema | `src/spec/schema.py` (`ShapeType`) |
| Spec → build params resolver | `src/spec/resolver.py:155` |
| Product template compiler (band-structured goods) | `src/spec/template.py:547` |
| UV atlas, per-island packing, per-object texel density | `harness_script.py` `_uv_diagnostics` |
| HP→LP bake chain, `bake_timeout_sec` parameter | `src/client/package.py:294` `finish_delivery` |
| GLB + FBX export, independent binary FBX verification | `src/client/` |
| Compliance gates: polycount, n-gons, closed solids, bounds, file size | `src/client/` |
| Texture composition from CC0 scans + procedural layers | `src/textures/compose.py` |
| Six review views incl. label and border close-ups, cross-key lighting | `finish_delivery` |
| VisionProvider ABC + Gemini provider | `src/ai/vlm.py` |
| Job card intake with dynamic constraints + placeholder-dims refusal | `src/client/job.py` |
| Packaging with loud refusal | `src/client/package.py:194` |
| **Agent skeleton — loop, tools, verifier, prompts** | `src/agent/` |

### The agent skeleton already present

`src/agent/` is **not empty**. It holds `AgentLoop` (`loop.py:50`),
`AgentToolExecutor` (`tools.py:94`), `Verifier` (`verifier.py:56`) and
`prompts.py`. Four tools are registered: `build_spec`, `measure_model`,
`render_model`, `execute_blender_script`.

**Build on this. Do not start a new agent package.**

> ### ⚠ `execute_blender_script` MUST BE REMOVED
>
> It lets the brain run arbitrary Blender Python. That is precisely the
> dominant failure mode identified in *3DCodeBench* (arXiv 2606.01057), which
> benchmarked 12 vision models on procedural 3D modelling and found failures
> "mostly arise from API mismatches", with the second mode being
> "disconnected or floating 3D geometric components".
>
> Our validated-spec boundary makes both structurally impossible. That
> boundary is the single biggest advantage this system has over every
> Blender-MCP-style setup. An arbitrary-script tool destroys it.
>
> Delete it, or gate it behind an explicit developer flag that the agent
> runtime can never set.

## 4. Architecture

```
prompt + reference images + constraints
              │
              ▼
        ┌───────────┐
        │  INTAKE   │  → JobCard (all constraints dynamic)
        └───────────┘
              │
              ▼
   ┌──────────────────────┐        ┌──────────────────────┐
   │  BRAIN (over API)    │◄──────►│  TOOLS               │
   │  GLM-5.3, no vision  │        │  build_spec          │
   │  plans, authors spec │        │  finish              │
   │  reads measurements  │        │  inspect  (measured) │
   │  decides next move   │        │  review   (renders)  │
   └──────────────────────┘        │  package             │
              │                    └──────────────────────┘
              │                               │
              ▼                               ▼
   ┌──────────────────────┐        ┌──────────────────────┐
   │  EYES (over API)     │        │  VALIDATED SPEC      │
   │  gemini-3.5-flash-   │        │  ↓                   │
   │  lite per iteration  │        │  resolver → Blender  │
   │  gemini-3.6-flash    │        │  (deterministic,     │
   │  on escalation       │        │   one process per op)│
   └──────────────────────┘        └──────────────────────┘
```

### The four architectural rules

1. **The brain never writes raw Blender Python.** It emits a validated
   `ObjectSpec`. The resolver executes. See the warning in §3.
2. **Tools return measured facts, not prose.** Numbers the brain can reason
   over: polycount, n-gon count, closed-solid per part, bounds, texel density
   per object, gate pass/fail with values.
3. **Gates before eyes.** Measured gates run every iteration — they are free
   and deterministic. Vision runs only when gates are green. This is both a
   quality and a cost decision (see `docs/VISION_CONFIG.md`).
4. **Vision is advisory and never gates a release.** A VLM score is not
   calibrated. Its documented misses on this project are in
   `HANDOFF_CLAUDE_DESKTOP.md` §4.

## 5. The 24-hour schedule

| Hours | Owner | Deliverable |
|---|---|---|
| 0–1 | owner + reviewer | Desktop up. `docs/DESKTOP_SETUP.md` complete, 250 tests green, **GPU Cycles bake confirmed working on the 4080** |
| **1–3** | **builder** | **BRAIN TEST — go/no-go. See §6.** |
| 3–10 | builder | Agent runtime: tools, loop, iteration control |
| 10–13 | builder | Intake: prompt + images + owner textures + dynamic constraints |
| 13–17 | builder | Closed verify-fix loop, iteration cap, honest failure reporting |
| 17–21 | builder + reviewer | End-to-end on 3 objects never seen before |
| 21–24 | builder | Batch of 5 in parallel; measure and report real wall clock |

### Definition of done at hour 24

You point the system at a photograph and a prompt. It returns a verified,
packaged asset with no human in the loop, for hard-surface objects, at
roughly 20–30 minutes each, and it reports honestly when it cannot.

You do **not** have organics, and you do **not** have a system that never
needs a human when it gets stuck.

## 6. The go/no-go test — hours 1–3

Everything in §5 after hour 3 is plumbing the reviewer is confident about.
**This is the part nobody has tested.**

> Can an API model author a correct `ObjectSpec` for an object it has never
> seen, from a photograph and a prompt, unaided?

Every spec so far was written by GLM **with the reviewer correcting it over
five rounds**. Cold autonomous authoring is a different task, and it is
exactly where the benchmark says models fail.

**Method.** Hand-feed GLM-5.3 three photo+prompt pairs through the API. Build
each returned spec. Look at the renders.

Suggested subjects — deliberately simple, deliberately unlike a mattress:
a side table, a storage crate, a floor lamp.

**Branches:**

| Outcome | Action |
|---|---|
| Specs build into recognisable objects | Build the full runtime. Confidence high. |
| Rough but fixable | Build it, add a spec-repair step, budget more iterations |
| Garbage / floating geometry | **Pivot at hour 3, not hour 20.** Fall back to guided authoring: the brain fills a structured template rather than authoring freely. Far more reliable, still autonomous. |

Report the outcome and the renders before proceeding to hour 3.

## 7. Throughput arithmetic

Measured components on the laptop, projected to the desktop (RTX 4080 Super,
Ryzen 9 9950X, 64 GB DDR5):

| Step | Desktop estimate |
|---|---|
| Parametric build | ~20 s |
| UV unwrap + pack | ~20 s |
| Bake at 1K on GPU | ~45 s |
| Six review renders | ~60 s |
| Vision verdict | ~20 s |
| Export + gates | ~15 s |
| **One iteration** | **~3 min** |

Five iterations plus a 4K bake and package ≈ **20–30 min per model**.
15 models sequential ≈ **5–7.5 hours**. Inside the target.

With 3 jobs concurrent (32 threads; CPU bakes at 1K for iteration, GPU
reserved for finals) ≈ **2–3 hours** for 15.

**The variable that decides this is iteration count, not hardware.** Five
iterations is comfortable; twenty collapses the day. Hence the hard cap.

## 8. Mattress: what is left

The mattress pauses at ~95% on structure. It is **not** abandoned — both
remaining items are fixed by work the new system needs anyway.

1. **Label illegible.** Measured cause: the atlas gives every surface the same
   texel density (an even 96.5 tex/m across all 14 parts, ratio 1.000). Velvet
   needs almost no detail; printed text needs many times more. **Fix: a
   per-surface detail priority so the packer can allocate texels unevenly on
   purpose.** This generalises to every future product with a logo.
2. **Review lighting too flat.** Axis bias and clipping were fixed in round 4,
   but absolute form contrast fell to **under one grey level** — worse than the
   version condemned as invisible in round 2. Cause: fill light added to kill
   clipping also killed the form. **Fix: a raking key, reduced fill, and a
   metric that measures absolute contrast rather than a ratio.**

Full evidence and the measurement recipes are in `HANDOFF_CLAUDE_DESKTOP.md`.

## 9. Roadmap beyond 24 hours

In order. Do not reorder without the owner's decision.

1. **Per-surface texel priority** — unblocks the label, and every branded asset.
2. **Raking key + absolute contrast metric** — makes the review rig trustworthy.
3. **Mesh-source interface.** Make the mesh source pluggable behind one
   contract so parametric, template, neural image-to-3D, imported assets and
   scans all satisfy it. Cheap now; expensive after the DSL calcifies.
4. **RETOPOLOGY.** The universal adapter and the single highest-value item on
   this project. It is the difference between a furniture pipeline and a 3D
   asset pipeline. Scope it in `docs/MESH_SOURCES.md` (not yet written).
5. **Neural image-to-3D behind retopology** — the organics path. TRELLIS 2
   (MIT licence, 16 GB minimum at 512³, 5–10 s per asset on a 4080) or
   Hunyuan3D 2.1 (12–16 GB). Both fit the desktop, one at a time.
6. **Generality hygiene** — move mattress-tuned schema defaults into
   `templates/mattress.yaml`, and write `templates/pillow.yaml` as a second
   instance with zero code changes. If a pillow cannot be expressed in YAML
   alone, that failure tells us exactly where the mattress leaked into the
   supposedly generic layer.

## 10. Standing risks

| Risk | Mitigation |
|---|---|
| Brain cannot author specs cold | §6 go/no-go at hour 3, guided-authoring fallback |
| Iteration count blows the day | Hard cap (start at 8), honest give-up, human override |
| Gemini daily quota | Billing enabled; gate-first ordering; verdict cache; local Qwen fallback |
| Reviewer cannot check 90 renders/day | Gates + vision carry it; reviewer spot-checks. **This is a deliberate, owner-approved reduction in safety.** |
| Scope drift into organics | §2. Retopology first, no exceptions |
| Proxy metrics passing as proof | See `HANDOFF_GLM_AUTONOMOUS.md` §6 — all three known misses were proxy measurements |

## 11. Still blocked on the owner

1. **Real MAYA00053153 dimensions** — L × W × H with explicit unit. Never
   inferred. The dashboard's `12 × 12 × 65 IN` is the shipping carton.
   `TEST-QUEEN` (80 × 60 × 12 IN) is a deliberate stand-in for quality work
   and carries `dims_placeholder: true` so it can never be packaged.
2. **Five questions for MetaZtech** — `.spp` mandatory or baked PNG sets;
   Simple-tier polycount ceiling; FBX axis/unit convention; polycount measured
   as triangles or faces; file-size caps decimal MB or binary MiB.
   The sixth — whether the side straps are carry handles — **the reference
   photos answered: yes, they are, and they are now modelled.**
