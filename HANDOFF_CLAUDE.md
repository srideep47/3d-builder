# HANDOFF — for the next Claude (reviewer / architect role)

> You are taking over the **review and architecture** seat on this project, not
> the implementation seat. GLM-5.3 implements in ZCode; you verify, decide, and
> author its instructions. Read this fully before responding to the owner.
> Written 2026-09-01, end of the overnight T0–T5 run.

---

## 1. The division of labour — this is the whole point

| Seat | Who | Does |
|---|---|---|
| Implementation | **GLM-5.3 in ZCode** | Writes all code. Text-only, **blind**. 256k context, full model (verified `zai-org/GLM-5.3`, not Flash). |
| Review / architecture | **You** | Verify claims, make decisions, author GLM's prompts, and **act as its eyes**. |
| Owner | srideep | Supplies dimensions, judges final visual quality, talks to the client. |

**The owner does not want you writing pipeline code.** He said so explicitly:
*"no you will build nothing — GLM 5.3 is working on things right now in ZCode."*
Your deliverables are verdicts, decisions, and prompts.

## 2. Two things you must do that GLM structurally cannot

### 2.1 You are the eyes
GLM is text-only. It cannot see renders, reference photos, or textures. **You
can.** This is the single most valuable thing you contribute.

It has already been demonstrated: overnight GLM certified texel density at
1.0004, all six client gates green, 238 tests passing — and the mattress had
**protruding tape-edge collars and band textures rendering as black-and-white
static.** Mechanically perfect, visually broken.

So: **whenever GLM reports a visual result, open the renders and look.** Do not
accept "all gates green" as evidence of visual correctness. They are different
claims.

### 2.2 Verify, do not trust
GLM's reports have been accurate — but only because they were checked. Re-run
the suite yourself. Test its factual claims. Example from this session: GLM
claimed `gemini-2.5-flash` was retired and pinned `gemini-3.6-flash`. I doubted
it, tested live, and **GLM was right** — 2.5-flash 404s with a message naming
3.6-flash as replacement. Verifying cost 30 seconds and settled it.

Cheap standing checks:
```bash
python -m pytest tests -q                    # baseline 238 — never let it drop
git status --short && git log --oneline -8
git grep -I "AQ\.Ab8RN6" $(git rev-list --all)   # must return nothing
```

## 3. Current state, verified 2026-09-01

**Pushed:** 9 commits, `origin/main` at `5447d99`. Tests **238 passing**
(independently re-run, not just claimed). No API key in any commit.

**T0–T5 complete.** Compliance spine, export/packaging with an independent FBX
parser, UV+bake finishing chain, mattress template, VisionProvider ABC + Gemini
provider, web delivery view.

**In flight at handoff:** GLM had 7 uncommitted files (defect fixes, 3/8 done).
Those were NOT in the push. Confirm with the owner whether they landed.

### The two open defects (found by looking, not by testing)
1. **Tape edges protrude as thick flanges** — should be thin binding tape
   hugging the perimeter edge per `GLM_BRIEF.md` §5.2. Cross-section too large
   and/or offset outward.
2. **Band textures render as chaotic black/white blotches** — not fabric. The
   coffee-table showcase is clean, so this is **mattress-template-specific**
   (texture composition or band UV scale), not a bake-pipeline fault.

The mattress also renders as a tall tower. **That is not a bug** — it is
12×12×65 IN, the placeholder, built faithfully. It is proof the dashboard
dimensions are the shipping box.

## 4. Decisions already made — do not relitigate without cause

| Decision | Rationale |
|---|---|
| **Keep GLM-5.3 + Gemini vision.** Do not switch to a local Qwen 27B. | GLM-5.3 scores 60 on Artificial Analysis — highest of the options (Qwen3.8 Max 2.4T = 58, and a local 27B is ~90× smaller than that). A 27B at 4-bit eats ~16GB VRAM, killing GPU baking. 10–15 tok/s on a thinking model would dominate job time. |
| **FBX exports from the live quad-clean scene**, not the triangulated GLB. | Their validator checks n-gons at all, which only makes sense if they expect quads; on a triangulated mesh that gate is trivially zero. Triangulating also doubles polycount against a tier ceiling. |
| **Vision is advisory and must never gate a release.** | A VLM score is not calibrated. It catches gross failures, not quilt pitch. |
| **No AI-generated fabric textures.** CC0 scans only (ambientCG, Poly Haven). | Diffusion output does not tile seamlessly and cannot produce a true normal map. |
| **No web-sourced base meshes.** | Reselling needs a licence permitting commercial redistribution; modification does not clear it. |
| **Do not switch to Blender MCP.** | Would trade away deterministic headless one-process-per-op reproducibility. |
| **img3d / neural image-to-3D stays parked.** | Blobby output cannot satisfy a zero-n-gon quad gate. VRAM was never the blocker. Revisit only for upholstered-organic products. |

## 5. Six questions still outstanding with the client (MetaZtech)

Nothing overnight moved these. Questions 3 and 4 affect whether the gates are
correctly calibrated.

1. Is `.spp` (Substance Painter Project) mandatory, or are baked PNG sets acceptable?
2. What is the **Simple** tier polycount ceiling? (Medium = 200,000; we use 50k provisional)
3. What **FBX axis/unit convention** does the validator expect?
4. Is polycount **triangles or faces**? (we use triangle-equivalent, the conservative reading)
5. File-size caps **decimal MB or binary MiB**? (we chose decimal, the stricter)
6. Are the vertical side straps **carry handles**, and should they be modelled?

**And from the owner: the real MAYA00053153 dimensions.** Never infer them. A
prior planning pass hallucinated `60 × 80 × 10 in`; the dashboard says
`12 × 12 × 65 IN`. Both are suspect. Only owner-supplied values count.

## 6. Machine move — the owner is switching to his laptop

**Laptop ("Scout"): Ryzen 5 4600H, 40 GB RAM, GTX 1650 Ti 4 GB.**

Consequences you must factor into every instruction you write:

- **4 GB VRAM cannot do meaningful GPU Cycles baking.** T3's 4K bakes become
  CPU work and will be slow. Advise a low-res bake (1K) for iteration and 4K
  only for delivery. This is the biggest practical change.
- **No local models of any size.** Gemini vision goes from optional to
  **essential** — good thing it is already wired.
- Blender 4.5 still runs fine (CPU-bound). `tools/blender-*` is gitignored, so
  the laptop needs its own copy — see `PROJECT_PLAN.md` §12 runbook.
- `output/` is gitignored: **the review renders and packages do not transfer.**
  Anything you needed to look at must be re-generated or copied manually.
- The img3d GPU service cannot run there. Already parked; keep it parked.

## 7. How the owner works

- Asks short, direct questions and wants short, direct answers. He will say
  "answer in simple english, short" — respect that.
- Wants honest assessment over encouragement. He asked "is it doable?" and
  valued the caveats more than the yes.
- Makes the calls himself once given a clear recommendation. Give one.
- Reaffirms when he means it. He asked twice for the API key to be placed
  directly; that is a decision, not an invitation to re-argue.

## 8. Key documents

| File | Contents |
|---|---|
| `GLM_BRIEF.md` | GLM's work order. §5 is the written transcription of the reference images — **that is GLM's only access to them**. |
| `HANDOFF_GLM.md` | Continuation brief for GLM after the machine move. |
| `PROGRESS.md` | GLM's own task-by-task log. Read this first for current state. |
| `CLIENT_PIPELINE_PLAN.md` | Strategy, and the review of a rejected Gemini plan. |
| `PROJECT_PLAN.md` §7 | The gotchas ledger. Non-negotiable invariants. |

## 9. Credentials

Gemini API key is in env vars `THREED_VLM_API_KEY` and `GEMINI_API_KEY` (same
value, user scope). **It is not in the repo** and must never be — `.gitignore`
covers `.env` at line 42. The owner pasted it in chat and intends to rotate it;
the indirection means rotation needs no code change. On the laptop these vars
must be set again — they do not travel with git.
