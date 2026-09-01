# HANDOFF — GLM-5.3 continuation brief (post machine move)

> Read this together with `GLM_BRIEF.md` (your standing work order) and
> `PROGRESS.md` (your own task log). This file covers only what changed:
> current state, the machine move, and what to do next.
> Written 2026-09-01 after the overnight T0–T5 run.

---

## 1. Where the work stands

**T0–T5 are implemented and pushed.** `origin/main` is at `5447d99`, 9 commits,
**238 tests passing** — independently re-verified by the reviewer, not merely
reported. No API key in any commit.

Landed: compliance spine (T1), export/packaging with an independent binary-FBX
parser (T2), UV + HP/LP bake finishing chain (T3), mattress product template
(T4, blocked on dimensions), VisionProvider ABC + Gemini v1beta provider and
the web delivery view (T5).

**Verified by the reviewer, not just claimed:** your `gemini-3.6-flash` finding
was correct — `gemini-2.5-flash` returns 404 with a message naming 3.6-flash as
its replacement. Your placeholder-dimension refusal fires correctly and exits 1.
Good work; the evidence discipline in PROGRESS.md is why it could be checked.

**One correction:** review renders are at `output/finish/<JOB>/review/`, not
`output/blocked/<JOB>/` as your report stated. Fix that pointer in PROGRESS.md.

**In flight when the handoff was taken:** you had 7 uncommitted files — the
defect fixes, 3/8 through. `harness_script.py`, `src/spec/template.py`,
`templates/mattress.yaml`, and four test files. **Those were not pushed.** If
they are gone, redo them from §2. If they survived, commit them first.

---

## 2. The two open defects — found by looking, which you cannot do

The reviewer has vision; you do not. They opened your renders. Both six-gate-green
assets were inspected. Findings:

**The coffee-table showcase is clean.** Correct geometry, clean surfaces. So the
finishing pipeline itself is sound — do not go hunting there.

**DEFECT 1 — tape edges protrude as thick flanges.** They stand well proud of
the side face like collars. Per `GLM_BRIEF.md` §5.2 the tape edge is *thin
binding tape that wraps and hugs the perimeter edge*. The swept cross-section is
far too large and/or offset outward. Fix profile scale and seating.

**DEFECT 2 — band textures render as chaotic black-and-white blotches**, not
fabric. Since the coffee table is clean, this is **specific to the mattress
texture composition or its band UV scale** — not the bake pipeline. Diagnose
which before changing anything: inspect the composed maps in
`output/finish/MAYA00053153/maps/` for garbage, and check the band islands' UV
scale. Report the faulty layer, then fix.

**Not a defect:** the mattress rendering as a tall tower is *correct* — it is
12×12×65 IN, the placeholder, built faithfully. It is evidence the dashboard
dimensions are the shipping box, not the product. Do not "fix" it.

**The NISIEN decal placeholder and unmodelled carry handles are known** and
remain open owner questions.

---

## 3. Use your eye now — this is the important change

The Gemini vision provider you built in T5 has **never been pointed at the
mattress.** Switch it on and make it your feedback loop:

```
Run the Gemini visual gate for MAYA00053153 against your renders plus the
client reference photos in D:\Work\Temp\Test Images. Report the verdict
verbatim, including the raw response.
```

Then: **after every visual fix, re-render, re-run the verdict, report
before/after.** Never declare a visual defect fixed on reasoning alone. Both
defects above are gross differences — exactly what a vision model reliably
catches. It would have caught them overnight.

It stays **advisory. It must never gate a release.** It will not catch fine
pattern fidelity — quilt pitch, band proportions. That remains the owner's
review.

Key: env vars `THREED_VLM_API_KEY` / `GEMINI_API_KEY`. **These do not travel
with git** — they must be set again on the new machine. Never hardcode the value.

---

## 4. The machine has changed — this affects how you work

**New host: Ryzen 5 4600H, 40 GB RAM, GTX 1650 Ti 4 GB.**

| Constraint | What you must do |
|---|---|
| **4 GB VRAM cannot do meaningful GPU Cycles baking.** | Bakes are now CPU-bound and slow. **Use 1K bake resolution while iterating; 4K only for a real delivery.** Make it a parameter, not a code edit. This is the biggest change to your loop. |
| **No local model can run here.** | Gemini vision is now essential, not optional. Keep the fail-soft path anyway. |
| `tools/` is gitignored | Blender does not transfer. See `PROJECT_PLAN.md` §12 runbook. Verify with `python -m src.cli health` before anything else. |
| `output/` is gitignored | **Prior renders, packages and bakes did not transfer.** Regenerate what you need. |
| img3d GPU service | Cannot run here. Stays parked. Do not touch. |

**First action on the new machine:** run `python -m src.cli health`, then
`python -m pytest tests -q` and confirm **238 passing** before any new work.
Blender-marked tests skip if Blender is absent — that is expected until you
install it, but say so rather than reporting a smaller number as green.

---

## 5. Order of work

1. Confirm environment: `health`, then 238 tests green.
2. Commit or redo the in-flight defect fixes.
3. Switch the Gemini visual gate on; report its verdict on the current renders.
4. Fix DEFECT 1 and DEFECT 2, using the visual gate as before/after evidence.
5. Fix the PROGRESS.md render pointer.
6. **Stop and wait** for the owner's dimensions before producing any
   MAYA00053153 deliverable package.

---

## 6. Rules that have not changed and are not negotiable

All of `GLM_BRIEF.md` §3 still applies. The ones most likely to bite next:

- **NEVER infer, guess or derive dimensions.** A prior planning pass
  hallucinated `60 × 80 × 10 in`; the dashboard says `12 × 12 × 65 IN`. Both are
  suspect. Only owner-supplied values count. Keep refusing to package on
  `dims_placeholder: true` — that refusal is working correctly and is valuable.
- **`harness_script.py` never imports project code.** It runs inside Blender.
- **One Blender process per op**, `model_path` always passed explicitly.
- **`view_layer.update()` before reading `matrix_world`** in background mode.
- **No product nouns in the finishing layer.** Mattress knowledge lives only in
  `templates/mattress.yaml`.
- **FBX exports from the live quad-clean scene**, not the triangulated GLB
  (decision already made — rationale: their n-gon gate only makes sense if they
  expect quads, and triangulating doubles polycount).
- **Never reduce the test baseline.** It is 238.
- **Do not push.** The owner handles pushes.
- **On a genuine blocker:** write "BLOCKED — needs owner" in PROGRESS.md with
  the specific question, then move to the next independent work. Never guess.

---

## 7. Still blocked on the owner

1. **MAYA00053153 real dimensions** — L × W × H with explicit unit.
2. Is `.spp` mandatory, or are baked PNG sets acceptable?
3. Simple-tier polycount ceiling (Medium = 200,000; we use 50k provisional).
4. FBX axis/unit convention the client's validator expects.
5. Polycount semantics: triangles or faces?
6. File-size caps: decimal MB or binary MiB?
7. Are the vertical side straps carry handles, and should they be modelled?

Keep all seven visible at the top of PROGRESS.md until answered.
