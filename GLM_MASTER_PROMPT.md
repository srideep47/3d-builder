# GLM MASTER WORK ORDER — run to completion

> You are the **BUILDER**. You write all the code. A Claude instance holds the
> review seat: it has vision, it verifies your claims by measurement, and it
> authors your prompts. It is **your eyes** — you are text-only and cannot see
> a render.
>
> This is your standing order for the **entire project**, not one task. Work
> through it phase by phase and **do not stop when you finish a phase** — start
> the next one. Stop only where §D says to stop.

---

## A. Read these first, in order

| # | File | Why |
|---|---|---|
| 1 | `PLAN_AUTONOMOUS.md` | Scope, architecture, the 24-hour plan |
| 2 | `HANDOFF_GLM_AUTONOMOUS.md` | Your work order in full detail |
| 3 | `docs/DESKTOP_SETUP.md` | Machine runbook |
| 4 | `docs/VISION_CONFIG.md` | Gemini limits, model tiering, 429 rules |
| 5 | `GLM_BRIEF.md` | Standing brief. **§5 is the written transcription of the reference photos — your only access to them.** §5.2/§5.3 carry reviewer corrections; honour them |
| 6 | `PROGRESS.md` | Your log and the gotchas ledger |

Your round-4 work verified out well — crown relief 37.7/37.3 mm, the 17×13
grid, band structure within 0.006, 250 tests. You were right when the reviewer
was wrong, twice. Keep that standard.

## B. Machine state — already verified by the reviewer, do not redo

Do **not** re-run setup. It is done and measured:

- `uv run python -m pytest tests -q` → **250 passed** (77 s)
- `uv run python -m src.cli health` → green, Blender 4.5.13
- Branch `wip/defect-fixes` at `81d626c`, textures composed, Blender present
- `config/ai.yaml` vision is **on**: `provider: gemini`,
  `model: gemini-3.5-flash-lite`. **The reviewer set that pin deliberately.**
  The branch shipped `gemini-3.6-flash`; flash-lite is the documented volume
  default (`VISION_CONFIG.md` §3). **Do not revert it** when you wire escalation.

**Working command** — the original brief had it wrong twice:

```bash
uv run python -m src.cli package --job input/jobs/TEST-QUEEN.yaml \
  --template templates/mattress.yaml --res 4096 --bake-timeout 3600
```

Bare `--template mattress` fails (needs the path). Default `--bake-timeout` is
300 s and kills a 4K bake mid-chain. **Exit code 2 is the placeholder-dimensions
refusal working correctly — not a failure.**

## C. Measured baselines — compare like with like

| Quantity | Value | Source |
|---|---|---|
| Full chain, 4K, CPU | **531 s** | wall clock |
| **Bake step alone, 4K, CPU** | **~476 s** | map mtimes, 02:59:39 → 03:07:35 |
| Six review renders | ~2 s | render mtimes |
| GPU during that bake | **0 %, 0 MiB** | `nvidia-smi` sampled throughout |
| Blender CPU burn | 1348 CPU-seconds | process counter |

**Do not divide a bake-step time by 531.** Compare bake-step to bake-step and
chain to chain. Reporting a 531-based ratio is exactly the adjacent-measurement
error §H exists to prevent.

## D. The only places you may stop

Everything else you work through without pausing.

| Stop | Condition | What to do |
|---|---|---|
| **S1 — vision** | Owner has not confirmed Gemini billing | Make **no vision call**. Build the vision code, unit-test it against recorded fixtures, leave it unexercised live |
| **S2 — brain-test photos** | `GLM_PROMPT_BRAIN_TEST.md` §4 still has `<REVIEWER FILLS FROM PHOTO>` | Do **not** invent subjects or descriptions. Skip to Phase 3 and come back |
| **S3 — brain test says GARBAGE** | Specs build into floating or unrecognisable geometry | **Halt the runtime design and report.** The pivot to guided authoring changes everything downstream and is the owner's call. Keep working on Phase 3 items that do not depend on the fork |
| **S4 — MAYA dimensions** | `dims_placeholder: true` | Never infer. Keep refusing to package |

**On any other blocker:** write `BLOCKED — needs owner` in `PROGRESS.md` with
the specific question, then **move to the next independent item and keep
working.** Do not idle waiting for an answer.

---

# THE PHASES

## Phase 0 — GPU baking ⚡ BLOCKING, ahead of everything

Cycles never touches the GPU. `scene.cycles.device` is hardcoded to `"CPU"` at
`src/blender/harness_script.py:1945` and again at `:2851`. There is **no** GPU
setup anywhere — no `compute_device_type`, no `get_devices()`, no device
enabling. Verified by grep: zero occurrences. The machine has an RTX 4080 Super
and **OptiX is available**.

Every throughput number in `PLAN_AUTONOMOUS.md` §7 assumes GPU baking.

1. **Not hardcoded either way.** Add a device parameter that flows through like
   `bake_timeout_sec` does, defaulting to auto-detect, falling back to CPU
   cleanly when no GPU is present. CI and the old laptop must still work.
2. **Enable the device properly:** set `compute_device_type` in preferences,
   call `get_devices()`, enable the devices, **then** set `scene.cycles.device`.
   Setting the scene device alone silently does nothing — which is roughly how
   this bug survived.
3. **Test OptiX AND CUDA, report both wall clocks.** OptiX is usually faster but
   has historically had limitations on some bake types. Measure, do not assume.
   Say which you chose and why.
4. **Acceptance is GPU utilisation, not elapsed time.** Sample `nvidia-smi`
   during the bake; non-zero VRAM is the proof. A CPU 4K bake here takes ~8
   minutes, which already reads as "minutes". Blender can also fall back
   silently for some bake types on OptiX without erroring — confirm the device
   is engaged rather than inferring it from the absence of an error.
5. **Watch for determinism drift.** GPU and CPU Cycles are not bit-identical.
   `tests/test_delivery_finish.py` and `tests/test_delivery_refusal.py` — 16
   tests, `pytest.mark.blender` — run a **real bake at resolution 512** (34 s
   for both files) and assert 8-bit channel values at `abs=2` tolerance (lines
   206, 208, 213) plus std/coverage floors (223, 389, 443). **That is where
   drift lands.** Run those two files explicitly before and after and diff the
   actual values. If any shift, that is a **real finding — report it, do not
   re-baseline the number.** State plainly whether GPU output is equivalent
   within tolerance or genuinely different.

**Record in `PROGRESS.md` gotchas:** the reviewer's brief said "expect minutes;
~19 min means CPU." This machine does a CPU 4K bake in ~8 minutes, which *is*
minutes. The stated test would have passed while the GPU sat idle. **Elapsed
time was the wrong signal; GPU utilisation was the right one.** Same class of
error as the round-4 ratio metric.

**Exit criteria:** GPU engaged and evidenced by utilisation; both device wall
clocks reported; 250 still green; determinism verdict stated.

## Phase 1 — two small items, straight after Phase 0

- **Per-step timings in `qa_report.json`.** `PLAN_AUTONOMOUS.md` §7 states
  per-step budgets (build ~20 s, unwrap ~20 s, bake ~45 s, renders ~60 s,
  verdict ~20 s, export ~15 s) and **we currently cannot verify a single one** —
  no timings are recorded at all. Add them per step.
- **Vision escalation path.** `VISION_CONFIG.md` §3 documents flash-lite as
  default with `gemini-3.6-flash` on escalation, but `config/ai.yaml` has a
  single `model` field and `vlm.py` has no escalation logic. **The doc described
  an intention, not the code.** Add a second configurable model id and the
  escalation trigger: one call before packaging, and whenever flash-lite
  disagrees with the measured gates. Never hardcode either id.

## Phase 2 — the brain test (hours 1–3, go/no-go)

Blocked on **S2** until the reviewer fills `GLM_PROMPT_BRAIN_TEST.md` §4 with
real photo descriptions and dimensions. **If it is still unfilled, skip to
Phase 3 and return here the moment it lands.**

When it is filled, follow that file exactly. It supersedes the older method
note in `HANDOFF_GLM_AUTONOMOUS.md` §3 in one respect: **you will not see the
photographs.** The reviewer writes the descriptions by hand. That is deliberate
— it isolates the variable, so a bad spec is unambiguously yours and not the
describer's.

One shot per subject. **Do not iterate, do not polish.** Iterating destroys the
measurement: we would learn what you can converge to, not what you can author
cold, and knowing which of those we have is the entire point.

Report which branch you land in — recognisable / rough but fixable / garbage.
On garbage, **S3 applies.**

## Phase 3 — agent runtime (hours 3–10)

### 3.0 FIRST: delete `execute_blender_script`

It lets the brain run arbitrary Blender Python — the dominant failure mode
measured in *3DCodeBench* (arXiv 2606.01057) across 12 models: failures "mostly
arise from API mismatches", second mode "disconnected or floating 3D geometric
components". Our validated-spec boundary makes both structurally impossible and
**that boundary is this system's single biggest advantage** over any
Blender-MCP setup.

Delete it, or gate it behind a developer-only flag the runtime can never set.
**Pin the removal with a test.**

This does not depend on the brain-test fork — **do it even while S2/S3 block.**

### 3.1 Extend, do not replace

`src/agent/` already has `AgentLoop` (`loop.py:50`), `AgentToolExecutor`
(`tools.py:94`), `Verifier` (`verifier.py:56`) and `prompts.py`. **Build on
them. Do not start a new package.**

Tools to expose, **all returning measured facts, never prose**:

| Tool | Wraps | Returns |
|---|---|---|
| `build_spec` | `resolve_spec_to_build_params` → Blender | success, glb path, tri count |
| `finish` | `finish_delivery` (`src/client/package.py:294`) | maps, review renders, bake time |
| `inspect` | gates + `_uv_diagnostics` | polycount, n-gons, closed-solid per part, bounds, texel density per object, every gate pass/fail **with its value** |
| `review` | six views + vision verdict | render paths, verdict JSON |
| `package` | `package_delivery` (`:194`) | deliverable, or the loud refusal |

**Brain:** GLM-5.3 over the aptos endpoint in `config/ai.yaml`.
**Eyes:** Gemini per `docs/VISION_CONFIG.md`.
**No ZCode and no Claude Code in the runtime** — build-time tools only.

## Phase 4 — intake (hours 10–13)

Prompt + reference images in, `JobCard` out. **Extend** `src/client/job.py`;
do not invent a parallel structure.

**Every constraint dynamic from the prompt, nothing hardcoded:** dimensions and
unit, polycount ceiling, file-size cap and its basis (MB or MiB), required
formats, axis convention, texture resolution, tri-vs-face semantics.

Keep the placeholder-dimension refusal exactly as it is. It works.

Intake **owner-supplied textures**: a directory the owner drops files into,
indexed so the brain can select from it. If a required surface has no supplied
file, compose from CC0 scans as now. **Never generate one with a diffusion
model** — it does not tile seamlessly and cannot produce a true normal map.

## Phase 5 — the closed loop (hours 13–17)

```
build → inspect (gates) → green? → review (vision) → decide → fix → repeat
                        └→ red?  → skip vision, fix, repeat
```

**Gates before eyes, always.** Gates are free and deterministic; vision is slow,
quota-limited and has documented misses.

**Hard iteration cap, start at 8.** On cap: stop, report exactly what failed
with the evidence. Never loop forever. Never claim a success you cannot evidence.

Vision rules (full detail in `docs/VISION_CONFIG.md`):

- **Do not downscale uniformly.** Overview views (iso/top/front/side) 768×768;
  **close-ups (label, border) stay 1024+** — fine detail is their entire
  purpose. Reference photos down to 768×768. Blanket downscaling would have
  hidden the label defect completely. Never `media_resolution: LOW` on a close-up.
- **429 branching:** `RATE_LIMIT_EXCEEDED` → exponential backoff with jitter,
  2 s → 60 s. `QUOTA_EXCEEDED` / `RESOURCE_EXHAUSTED` → stop retrying, load
  local Qwen 27B, take the verdict, **unload and return the GPU to Blender.**
  There is no pre-flight quota endpoint; handle it reactively.
- **Cache verdicts by image hash.** Free, and correct regardless of quota.
- **Do not build context caching** — needs 32,768 tokens minimum, ours is ~11,600.
- **Vision is advisory and never gates a release.**

## Phase 6 — end-to-end (hours 17–21)

Three objects never seen before, start to finish. Report renders and your
honest read. **The reviewer is the visual judge** — you cannot see them.

## Phase 7 — batch throughput (hours 21–24)

Run 3 jobs concurrently (32 threads, 64 GB). CPU bakes at 1K for iteration;
**GPU reserved for final 4K.** Then run a batch of 5 and report **real measured
wall clock per model and total — not an estimate.**

**Definition of done:** point the system at a photograph and a prompt; it
returns a verified, packaged asset with no human in the loop, for hard-surface
objects, at roughly 20–30 min each, and it reports honestly when it cannot.

## Phase 8 — the queued roadmap. Do not start before Phase 7 is done.

In order. Do not reorder without the owner's decision.

1. **Per-surface texel priority.** The atlas gives every surface the same
   density — an even 96.5 tex/m across all 14 parts, ratio 1.000. That is the
   bug: velvet needs almost no detail, printed text needs many times more.
   Fixes the illegible label and generalises to every branded asset.
2. **Raking key + absolute-contrast metric.** Add a key that reveals form,
   reduce fill until form returns without reintroducing clipping, and pin a
   floor on **absolute grey-level amplitude** at the quilt pitch — suggest 6+ —
   alongside the existing balance and clipping checks. **A ratio alone must
   never gate this again.**
3. **Mesh-source interface.** One contract behind which parametric, template,
   neural image-to-3D, imported assets and scans all satisfy. Cheap now,
   expensive after the DSL calcifies.
4. **RETOPOLOGY.** The universal adapter and the highest-value item on this
   project — the difference between a furniture pipeline and a 3D asset
   pipeline. Scope it in `docs/MESH_SOURCES.md` (not yet written).
5. **Neural image-to-3D behind retopology** — the organics path. TRELLIS 2 (MIT,
   16 GB at 512³) or Hunyuan3D 2.1 (12–16 GB). Both fit; one at a time.
6. **Generality hygiene.** Move mattress-tuned schema defaults into
   `templates/mattress.yaml`, then write `templates/pillow.yaml` as a second
   instance **with zero code changes.** If a pillow cannot be expressed in YAML
   alone, that failure tells us exactly where the mattress leaked into the
   supposedly generic layer.

**Organics stay out** until retopology exists. Nothing in the repo does
retopology today; every gate passes only because parametric output is *born*
clean. Feed in a dense triangulated organic mesh and it fails all of them. Do
not let scope drift here.

---

## H. Reporting discipline — read this twice

Your two failures were **overstatement, not fabrication**, and both had the
same shape: **you measured something adjacent to the real question.**

1. *"At 327×717 px the label is clearly readable."* It is an illegible blur.
   Your chroma figures described the **source artwork**, not the render.
2. *"Quilt axis symmetry 12.0 → 0.87."* True — but **a ratio reaches 1.0 when
   both terms go to zero.** Fill light had flattened the form; absolute contrast
   fell to 0.81 / 0.96 grey levels, **worse than the round-2 version we
   condemned as invisible.** The metric scored symmetric invisibility as success.

The reviewer made the same class of error twice, by reading renders instead of
measuring meshes. This is not a criticism of you — it is the failure mode of
this whole project.

- **A ratio, a score, or a histogram of a source file is never proof.** Report
  the direct, absolute measurement of the thing actually in question.
- **Every visual claim needs a number with a unit** — millimetres, grey levels,
  texels per mm, pixels — plus what a human threshold for it is.
- **When you cannot verify something directly, say so plainly:** *"I cannot
  verify this — you look."* That is a completely acceptable answer and far more
  useful than a proxy.
- **Legibility is a yes/no a human confirms**, never a chroma histogram.
- **Keep confessing your own bugs.** It is why your numbers can be trusted.

## I. Rules that never change

- **NEVER infer, guess or derive dimensions.** A prior planning pass
  hallucinated `60 × 80 × 10 in`; the dashboard's `12 × 12 × 65 IN` is the
  shipping carton. Only owner-supplied values count.
- **`harness_script.py` never imports project code.** It runs inside Blender.
  The Phase 0 device parameter must arrive as an **op parameter threaded from
  the caller**, exactly like `bake_timeout_sec`. Do not have the harness read
  config.
- **One Blender process per op**, `model_path` always passed explicitly.
- **`view_layer.update()` before reading `matrix_world`** in background mode.
- **No product nouns in the finishing layer.** Product knowledge lives only in
  `templates/*.yaml`.
- **FBX exports from the live quad-clean scene**, not the triangulated GLB.
- **No AI-generated textures.** CC0 scans or owner-supplied files only.
- **The brain never writes raw Blender Python.** It emits a validated
  `ObjectSpec`; the resolver executes.
- **Never reduce the test baseline. It is 250.**
- **Do not push.** The owner handles pushes.

## J. Cadence

After **every phase**: run the full suite, update `PROGRESS.md`, report in the
§H style — measured numbers with units, uncertainties named — then **start the
next phase without waiting for permission.** Pause only at S1–S4.
