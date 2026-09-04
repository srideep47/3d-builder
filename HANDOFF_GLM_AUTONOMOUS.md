# HANDOFF — builder seat (GLM-5.3, ZCode)

> You write all the code. Read `PLAN_AUTONOMOUS.md` first for scope and
> architecture, then this for the work order. `docs/DESKTOP_SETUP.md` is
> step 0. `docs/VISION_CONFIG.md` has the vision numbers.
>
> `GLM_BRIEF.md` remains your standing brief; `PROGRESS.md` remains your log.
> Written 2026-09-02 by the reviewer, after round 4.

---

## 1. Read this first — your round-4 work was good

Verified independently by the reviewer: crown relief 37.7 / 37.3 mm with 33
and 25 turning points, the 17×13 grid, band structure matching the photos
within 0.006, velvet at 9.4% lightness, part count 18 → 14, 250 tests green.

You were **right when the reviewer was wrong**, twice — the quilt geometry and
the sub-texel aliasing beat. You confessed two of your own bugs unprompted and
pinned both with tests. You overrode a Gemini false negative on the carry
handles with pixel evidence instead of "fixing" a non-problem. All of that is
exactly right. Keep doing it.

Two things did not survive inspection, and §6 exists because of them.

## 2. Step 0 — the machine move

Follow `docs/DESKTOP_SETUP.md` exactly. New host: **Ryzen 9 9950X, RTX 4080
Super 16 GB, 64 GB DDR5, Gen4 SSD.** Report each of these before any other work:

1. `uv run python -m pytest tests -q` → **250 passed**
2. `uv run python -m src.cli health` → green
3. **A GPU Cycles bake actually running on the 4080.** This is new — the
   laptop's 4 GB card could never do it, so bakes were CPU-bound at ~19 min
   for 4K. Report the real 4K wall clock. It should be minutes.

Everything downstream assumes GPU baking works. Confirm it before building.

## 3. Hours 1–3 — THE BRAIN TEST. Go/no-go. Do this before anything else.

Do not build the runtime until this is answered.

> Can GLM-5.3 author a correct `ObjectSpec` for an object it has never seen,
> from a photograph and a prompt, unaided?

Every spec so far was written by you **with the reviewer correcting it over
five rounds**. Cold autonomous authoring has never been tested, and it is
exactly where *3DCodeBench* found 12 vision models fail.

**Method.** Hand-feed three photo + prompt pairs through the API. Build each
returned spec with the existing resolver. Render. Report the renders and your
own honest read.

Subjects — simple, and deliberately unlike a mattress: **a side table, a
storage crate, a floor lamp.**

Report which branch you land in:

- **Recognisable objects** → proceed to §4.
- **Rough but fixable** → proceed, add a spec-repair step, expect more iterations.
- **Garbage or floating geometry** → **STOP and report.** The fallback is
  guided authoring — the brain fills a structured template rather than
  authoring freely. Still autonomous, far more reliable. Pivoting at hour 3
  is cheap; at hour 20 it is fatal.

## 4. The build order

### 4.1 Agent runtime — build on what exists

`src/agent/` is **not empty**. It has `AgentLoop` (`loop.py:50`),
`AgentToolExecutor` (`tools.py:94`), `Verifier` (`verifier.py:56`) and
`prompts.py`. Registered tools: `build_spec`, `measure_model`, `render_model`,
`execute_blender_script`.

**Extend it. Do not start a new package.**

> ### ⚠ FIRST: DELETE `execute_blender_script`
>
> It lets the brain run arbitrary Blender Python. *3DCodeBench* (arXiv
> 2606.01057) benchmarked 12 vision models and found failures "mostly arise
> from API mismatches", with the second mode being "disconnected or floating
> 3D geometric components".
>
> Our validated-spec boundary makes both structurally impossible, and that is
> the single biggest advantage this system has over any Blender-MCP setup. An
> arbitrary-script tool throws it away.
>
> Delete it, or gate it behind a developer-only flag the runtime can never
> set. Pin the removal with a test.

**Tools to expose to the brain**, all returning measured facts:

| Tool | Wraps | Returns |
|---|---|---|
| `build_spec` | `resolve_spec_to_build_params` → Blender | success, glb path, tri count |
| `finish` | `finish_delivery` (`src/client/package.py:294`) | maps, review renders, bake time |
| `inspect` | gates + `_uv_diagnostics` | polycount, n-gons, closed-solid per part, bounds, texel density per object, every gate pass/fail **with its value** |
| `review` | six views + vision verdict | render paths, verdict JSON |
| `package` | `package_delivery` (`:194`) | deliverable, or the loud refusal |

**Brain:** GLM-5.3 over the existing aptos endpoint in `config/ai.yaml`.
**Eyes:** Gemini, per `docs/VISION_CONFIG.md`.
**No ZCode and no Claude Code in the runtime.** They are build-time only.

### 4.2 Intake

Prompt + reference images in, `JobCard` out. Extend the existing card in
`src/client/job.py` — do not invent a parallel structure.

**Every constraint dynamic from the prompt. Nothing hardcoded:** dimensions
and unit, polycount ceiling, file-size cap and its basis (MB or MiB), required
formats, axis convention, texture resolution, tri-vs-face semantics.

Keep the placeholder-dimension refusal exactly as it is. It works and it is
valuable.

Also intake **owner-supplied textures**: a directory the owner drops files
into, indexed so the brain can select from it. If a required surface has no
supplied texture, compose one from CC0 scans as now — never generate one with
a diffusion model (see §7).

### 4.3 The closed loop

```
build → inspect (gates)  →  green? → review (vision) → decide → fix → repeat
                         └→ red?   → skip vision, fix, repeat
```

**Gates before eyes**, always. Gates are free and deterministic; vision is
slow, quota-limited and has documented misses.

**Hard iteration cap, start at 8.** On cap: stop, and report exactly what
failed with the evidence. Never loop forever. Never claim a success you cannot
evidence.

### 4.4 Throughput

Run 3 jobs concurrently (32 threads, 64 GB). CPU bakes at 1K for iteration;
GPU reserved for final 4K. Then run a batch of 5 and report **real wall clock
per model and total**, not an estimate.

## 5. Vision configuration

Full detail in `docs/VISION_CONFIG.md`. The essentials:

> **Make no vision call until the owner confirms billing is enabled.**
> Free-tier submissions are used for training and may be human-reviewed.
> Client NDA photos have already gone through the free tier. Record this as a
> standing note in `PROGRESS.md`.

- `gemini-3.5-flash-lite` every iteration. `gemini-3.6-flash` for **one**
  escalation before packaging, and whenever flash-lite disagrees with the
  measured gates. Both model ids configurable in `config/ai.yaml`; never
  hardcode.
- **Image sizing — do not downscale uniformly.** Overview views (iso, top,
  front, side) at 768×768. **Close-ups (label, border) stay at 1024 or
  higher — fine detail is their entire purpose.** Reference photos down to
  768×768. Blanket downscaling would have hidden the label defect.
- **Context caching does not apply.** Explicit caching needs 32,768 tokens
  minimum; our request is ~11,600. Do not build it.
- **429 branching:** `RATE_LIMIT_EXCEEDED` → exponential backoff with jitter,
  2 s → 60 s. `QUOTA_EXCEEDED` / `RESOURCE_EXHAUSTED` → stop retrying, load
  local Qwen 27B, take the verdict, **unload, return the GPU to Blender**.
  There is no pre-flight quota endpoint; handle it reactively.
- **Cache verdicts by image hash** and use gate-first ordering. Both are free
  and correct regardless of quota.
- **Batch API** (50% off, separate queue, 15–45 min) is **not** for the loop —
  that latency breaks the per-model budget. Use it for an end-of-day audit
  pass over the day's finished models. Build it last.

## 6. Reporting discipline — read this twice

Your two failures were **overstatement, not fabrication**, and both had the
same shape: **you measured something adjacent to the real question.**

1. *"At 327×717 px the label is clearly readable."* It is an illegible blur.
   Your chroma figures described the **source artwork**, not the render.
2. *"Quilt axis symmetry 12.0 → 0.87."* True — but a **ratio reaches 1.0 when
   both terms go to zero.** Fill light had flattened the form; absolute
   contrast fell to 0.81 / 0.96 grey levels, **worse than the round-2 version
   we condemned as invisible**. The metric scored symmetric invisibility as
   success.

The reviewer made the same class of error twice by reading renders instead of
measuring meshes. This is not a criticism of you — it is the failure mode of
this whole project.

**Therefore, from now on:**

- **A ratio, a score, or a histogram of a source file is never proof.** Report
  the direct, absolute measurement of the thing actually in question.
- **Every visual claim needs a number with a unit** — millimetres, grey
  levels, texels per mm, pixels — plus what a human threshold for it is.
- **When you cannot verify something directly, say so plainly:** *"I cannot
  verify this — you look."* That is a completely acceptable answer and far
  more useful than a proxy.
- **Legibility is a yes/no a human confirms**, never a chroma histogram.
- Keep confessing your own bugs. It is why your numbers can be trusted.

## 7. Rules that have not changed

- **NEVER infer, guess or derive dimensions.** A prior planning pass
  hallucinated `60 × 80 × 10 in`; the dashboard says `12 × 12 × 65 IN` (the
  shipping carton). Only owner-supplied values count. Keep refusing to package
  on `dims_placeholder: true`.
- **`harness_script.py` never imports project code.** It runs inside Blender.
- **One Blender process per op**, `model_path` always passed explicitly.
- **`view_layer.update()` before reading `matrix_world`** in background mode.
- **No product nouns in the finishing layer.** Product knowledge lives only in
  `templates/*.yaml`.
- **FBX exports from the live quad-clean scene**, not the triangulated GLB.
- **No AI-generated textures.** CC0 scans or owner-supplied files only.
- **Never reduce the test baseline. It is 250.**
- **Do not push.** The owner handles pushes.
- **On a genuine blocker:** write `BLOCKED — needs owner` in `PROGRESS.md`
  with the specific question, then move to the next independent work.

## 8. Queued behind the 24-hour work — do not start these yet

In order, from `PLAN_AUTONOMOUS.md` §9:

1. **Per-surface texel priority.** The atlas currently gives every surface the
   same density — an even 96.5 tex/m across all 14 parts, ratio 1.000. That is
   the bug: velvet needs almost no detail, printed text needs many times more.
   Fixes the illegible label, and generalises to every branded asset.
2. **Raking key + absolute-contrast metric.** Add a key that reveals form,
   reduce fill until form returns without reintroducing clipping, and pin a
   floor on absolute grey-level amplitude at the quilt pitch — suggest 6+ —
   alongside the existing balance and clipping checks. **A ratio alone must
   never gate this again.**
3. **Mesh-source interface** → `docs/MESH_SOURCES.md`, a document, no code.
4. **Retopology.** The universal adapter; the highest-value item on the
   project. Nothing in the repo does it today.
5. **Neural image-to-3D behind retopology** — the organics path.
6. **Generality hygiene:** move mattress-tuned schema defaults
   (`cells_across` 17, `pattern` grid_square, `exponent` 1.6,
   `profile_exponent` 3.5, `restrict_z` 0.85, carry-handle
   `count_per_side` 2 / `width_fraction` 0.08 / `protrusion_fraction` 0.92)
   out of `template.py` and into `templates/mattress.yaml`, and make the
   schema require them. Then write `templates/pillow.yaml` as a second
   instance with **zero code changes**. If a pillow cannot be expressed in
   YAML alone, stop and report which spec type blocked you — that answer is
   the real deliverable.

## 9. Still blocked on the owner

1. **Real MAYA00053153 dimensions** — L × W × H with explicit unit.
2. Is `.spp` mandatory, or are baked PNG sets acceptable?
3. Simple-tier polycount ceiling (Medium = 200,000; we use 50k provisional).
4. FBX axis/unit convention the client's validator expects.
5. Polycount semantics: triangles or faces?
6. File-size caps: decimal MB or binary MiB?

The seventh — whether the side straps are carry handles — **is closed. The
reference photos show them; they are modelled, two per long side.**

Keep the remaining six visible at the top of `PROGRESS.md` until answered.
