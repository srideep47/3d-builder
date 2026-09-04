# HANDOFF — reviewer / architect seat (Claude, desktop)

> You are taking the **review and architecture** seat. GLM-5.3 in ZCode
> implements; you verify, decide, and author its instructions. You are also
> **its eyes** — it is text-only and cannot see a render.
>
> Read `PLAN_AUTONOMOUS.md` first. Then this. Then `docs/DESKTOP_SETUP.md`.
> Written 2026-09-02 by the outgoing reviewer, after five verification rounds.

---

## 1. Your job, and the one rule that has made this project work

**The owner does not want you writing pipeline code.** He has said so
explicitly. Your deliverables are verdicts, decisions, and GLM's prompts.

The rule, learned the hard way:

> **Measure every claim. Never accept a report, a score, or a green gate as
> evidence of visual correctness.**

This is not distrust of GLM — its reports have been consistently honest. It
is that *proxy measurements* are where everything has gone wrong, for GLM and
for me both. Concretely, over five rounds:

- Round 1 shipped with **all six compliance gates green** and a mattress with
  protruding tape collars and textures rendering as black-and-white static.
- I twice declared a defect that wasn't there, by reading a render instead of
  measuring the mesh.
- GLM twice reported "fixed" on a proxy metric that was measuring something
  adjacent to the real question.

Every single miss — mine and its — was caught by direct measurement in
minutes. Keep that and the system holds.

## 2. Measurement recipes — use these, they work

These are the exact checks that caught real problems. Run them from the repo
root with `uv run python -c "..."`.

### Baseline, every time

```bash
uv run python -m pytest tests -q        # 250. Never let it drop.
uv run python -m src.cli health
git status --short && git log --oneline -8
```

### Crown / surface relief — is geometry actually there?

This settled the quilt question when the render lied in both directions.

```python
import trimesh, numpy as np
s = trimesh.load('output/finish/TEST-QUEEN/lp.glb')
v = np.asarray(s.geometry['crown'].vertices)
H, A, B = 1, 0, 2          # NOTE: the GLB is Y-UP. Height is axis 1, not 2.
h = v[:, H]
top = v[h > h.min() + 0.55 * (h.max() - h.min())]
def prof(ax, ot, tol=0.015):
    o = np.median(top[:, ot]); sel = top[np.abs(top[:, ot] - o) < tol]
    return sel[np.argsort(sel[:, ax])][:, H]
for lbl, (ax, ot) in {'along LENGTH': (A, B), 'across WIDTH': (B, A)}.items():
    q = prof(ax, ot); d = np.diff(q)
    turns = int(np.sum(np.diff(np.sign(d)) != 0))
    print(f'{lbl}: p2p={1000*(q.max()-q.min()):.2f}mm '
          f'std={1000*q.std():.2f}mm turns={turns}')
```

Last known good: `along LENGTH p2p=37.67mm turns=33`,
`across WIDTH p2p=37.32mm turns=25` → a real 17×13 grid.

### Render contrast at a known spatial frequency — is it *visible*?

This caught the flat-lighting regression that GLM's own metric scored as a
success, and that Gemini's 4/10 never mentioned.

```python
from PIL import Image
import numpy as np, glob, os
def amp(p):
    a = np.asarray(Image.open(p).convert('L')).astype(float)
    c = a[300:750, 250:800]; c = c - c.mean()
    out = {}
    for ax, lab in [(1, 'alongLEN'), (0, 'acrossWID')]:
        prof = c.mean(axis=ax); prof = prof - prof.mean()
        sp = np.abs(np.fft.rfft(prof)); n = len(prof); f = np.arange(len(sp))
        per = np.where(f > 0, n / np.maximum(f, 1), 1e9)
        m = (per > 35) & (per < 75)        # the quilt cell band, in px
        i = int(np.argmax(sp * m))
        out[lab] = (round(per[i], 1), round(float(sp[i] / (n / 2)), 2))
    return out, round(c.std(), 2)
for d in sorted(glob.glob('output/finish/TEST-QUEEN*/review*')):
    p = os.path.join(d, 'TEST-QUEEN_top.png')
    if os.path.exists(p):
        o, s = amp(p)
        print(f'{d:52s} std={s:5} ' +
              ' '.join(f'{k}: per={v[0]} amp={v[1]}' for k, v in o.items()))
```

Amplitude is in **grey levels**. History:

| Build | along length | across width | balance |
|---|---|---|---|
| round 2, texture only (called invisible) | 1.34 | 0.47 | 2.9 : 1 |
| round 3, real geometry, old rig | 1.19 | 0.65 | 1.8 : 1 |
| round 4, real geometry, new rig | **0.81** | **0.96** | 0.85 : 1 |

Balance is fixed. **Absolute contrast is worse than the version we
condemned.** Anything under ~2 grey levels is invisible to a human. Target 6+.

### Is a feature present in a baked map at all?

Contrast-stretch the normal map's green channel. A ×12 stretch revealed the
diamond quilt lattice sitting in the map at ~1 grey level — present, correct,
and completely buried.

```python
from PIL import Image
import numpy as np
a = np.asarray(Image.open('output/finish/TEST-QUEEN/maps/normal.png')
               .convert('RGB')).astype(np.float32)
g = a[..., 1].copy()
g[(np.abs(a[..., 0] - 128) < 2) & (np.abs(g - 128) < 2)] = 128.0
s = np.clip((g - 128.0) * 12.0 + 128.0, 0, 255).astype(np.uint8)
Image.fromarray(s).resize((1024, 1024), Image.BOX).save('output/nrm_stretch.png')
```

### Reading reference photos properly

Crop and upscale before judging band structure. Guessing from a full-frame
photo is how the written §5.2 transcription accumulated errors for a week.

```python
from PIL import Image
im = Image.open('input/references/MAYA00053153/'
                'WhatsApp Image 2026-08-31 at 9.29.19 PM.jpeg').convert('RGB')
c = im.crop((250, 1025, 900, 1245))
c.resize((c.width * 2, c.height * 4), Image.LANCZOS).save('output/ref_border.png')
```

**Clean up any scratch PNGs you write into `output/` when you're done.**

## 3. Corrections to the written reference transcription

`GLM_BRIEF.md` §5.2/§5.3 is GLM's **only** access to the reference photos. It
had real errors. These came from the reviewer's eyes and are now marked as
reviewer-sourced in the brief. Do not let them regress.

| Item | Truth, measured off the photos |
|---|---|
| Quilt pattern | **Square grid**, not diamond. Photos `9.28.22` and `9.28.35` both read square. Marketing render `9.29.19` shows diamond — flagged as a possible second variant, both wired, one YAML line to switch |
| Quilt cells | ~100–130 mm. Landed at `cells_across: 17` → 17×13 = 119.0 × 116.5 mm |
| Quilt must be | **Real low-poly geometry.** The puffs break the silhouette in the photos. A normal map carries no silhouette — measured at 3.7° of tilt, ~1 grey level, invisible at any bake resolution |
| Border stack | **One** dark velvet mass with two faint stitched creases, bounded by exactly **one** white knit rib above and **one** below. No white ribs between velvet bands |
| Band fractions | crown .28 / air_mesh .15 / knit_top .09 / velvet ×3 at .09 / knit_bottom .11 / base .10 |
| Carry handles | **They exist.** Visible in `9.28.35` as vertical straps at the quarter points. Open question since session one — now closed, and modelled, two per long side |
| Label | Tall and narrow. Measured off the close-up: aspect 0.46, height 0.34 of H, centred at 0.33 |

## 4. Gemini's documented misses — vision is advisory, never a gate

Keep this list. It is the evidence for why vision cannot gate a release.

- **False negative:** reported the carry straps as missing. GLM produced pixel
  evidence showing them at both quarter points. GLM was right to override it
  and record the miss rather than "fix" a non-problem. Good judgement — back it.
- **Missed the flat lighting entirely:** scored 4/10 across two rounds without
  ever mentioning that form contrast had collapsed below one grey level.
- **Score history is not a baseline.** Every verdict before round 4 was scored
  against renders we now know were misleading — axis-biased lighting and
  clipping highlights. Treat the 4/10 history as void, not as a trend.
- Quota: free tier is ~20–50 requests/day. We exhausted it. See
  `docs/VISION_CONFIG.md`.

## 5. How GLM performs — an honest assessment

**Trust its numbers. Check what those numbers measure.**

Verified correct on independent measurement, every time: crown relief
37.7/37.3 mm, the 17×13 grid, FFT axis symmetry, band fractions within 0.006,
velvet at 9.4% lightness, part count 18 → 14, 250 tests.

It has also been **right when I was wrong**, twice — the quilt geometry and
the sub-texel aliasing beat. Both times it had the correct diagnosis with
evidence while I was reading a render. It confessed two of its own bugs
unprompted and pinned both with tests.

Its two failures were **overstatement, never fabrication**:

1. "The label is clearly readable at 327×717 px." It is an illegible blur. The
   chroma figures it quoted described the **source artwork**, not the render.
2. "Axis symmetry 12.0 → 0.87, fixed." True — but a **ratio** reaches 1.0 when
   both terms go to zero. Fill light had flattened the form. The metric scored
   symmetric invisibility as success.

**The pattern in every miss, mine included: measuring something adjacent to
the real question.** That is the thing to police, and it is why
`HANDOFF_GLM_AUTONOMOUS.md` §6 forbids proxy metrics as proof.

## 6. Decisions already made — do not relitigate without cause

| Decision | Rationale |
|---|---|
| **The brain never writes raw Blender Python.** Remove `execute_blender_script` | *3DCodeBench* (arXiv 2606.01057), 12 models: failures "mostly arise from API mismatches", second mode "disconnected or floating" geometry. The validated-spec boundary makes both impossible. This is the system's main advantage over Blender-MCP setups |
| **Keep GLM-5.3 as the builder.** Do not switch to Sonnet | Its work has verified out over five rounds, and it holds every gotcha in `PROGRESS.md`. Switching costs context re-establishment we cannot afford in 24 h |
| **GLM-5.3 over API is the runtime brain, not local Qwen** | Owner measured local 27B at 10–15 tok/s with CPU offload. That puts ~90 min of thinking per model → ~22 h for 15 models. Misses the target by half a day |
| **Local Qwen 27B is the vision *fallback* tier** | A 1–2 min verdict is fine a few times a day. It also owns the GPU while loaded, so Blender cannot bake — never put it in the hot loop |
| **Gates before eyes** | Gates are free and deterministic; vision is slow, quota-limited and has documented misses |
| **`gemini-3.5-flash-lite` default, `gemini-3.6-flash` on escalation** | Owner's call on the volume path, and correct. Flash is documented as materially better at spatial alignment and subtle artifacts — worth a couple of dollars a month on the final check |
| **Do NOT build a Gemini CLI bridge** | An API model cannot route to a local CLI; headless image input is undocumented; the CLI is an agent with its own loop, not an inference endpoint; paid API is ~$1.40–6.20/month, which removes the quota outright and is contractually clean |
| **No AI-generated fabric textures.** CC0 scans only | Diffusion output does not tile seamlessly and cannot produce a true normal map |
| **FBX exports from the live quad-clean scene**, not the triangulated GLB | Their validator checks n-gons at all, which only makes sense if they expect quads; triangulating also doubles polycount against a tier ceiling |
| **No web-sourced base meshes** | Reselling needs a licence permitting commercial redistribution; modification does not clear it |
| **Organics stay parked until retopology exists** | Nothing in the repo does retopology. Without it, generated meshes fail every gate |

## 7. Volume changes your job

At 15 models a day that is ~90 renders. **You cannot be the gate any more.**

Measured gates plus the vision verdict carry it; you spot-check. This is a
real reduction in safety and **the owner has approved it deliberately** — see
`PLAN_AUTONOMOUS.md` §10. Things will get through.

Spend your attention where the history says defects hide:

1. **Any "fixed" claim resting on a ratio, a score, or a histogram of a source
   file.** Demand the direct measurement.
2. **The first asset of any new object class.** That is where cold spec
   authoring fails.
3. **Close-ups.** Every detail defect on this project was invisible in
   overview renders and obvious in a close-up. The overview views can be
   downscaled for cost; the close-ups must not be.

## 8. How the owner works

- Short, direct questions; wants short, direct answers. He will say "answer in
  simple english, short" — respect it.
- Wants honest assessment over encouragement. He valued the caveats on "is it
  doable?" more than the yes.
- Makes the calls himself once given a clear recommendation. **Give one.**
- Reaffirms when he means it. Treat a repeat as a decision, not an invitation
  to re-argue.
- Asks for progress visually. A published page with the actual renders side by
  side with the reference photos has worked well — he is the visual judge, so
  put the images in front of him rather than describing them.

## 9. Key documents

| File | Contents |
|---|---|
| `PLAN_AUTONOMOUS.md` | The master plan, scope, 24-hour schedule, go/no-go |
| `HANDOFF_GLM_AUTONOMOUS.md` | The builder's work order |
| `docs/DESKTOP_SETUP.md` | Machine runbook — do this first |
| `docs/VISION_CONFIG.md` | Gemini limits, pricing, tiering, image sizing, 429 handling |
| `GLM_BRIEF.md` §5 | The written transcription of the reference photos — GLM's only access to them. §5.2/§5.3 carry the reviewer's corrections |
| `PROGRESS.md` | GLM's task log and the gotchas ledger. Read for current state |
| `PROJECT_PLAN.md` §7 | Non-negotiable invariants |

## 10. Credentials

`THREED_VLM_API_KEY` (Gemini provider also falls back to `GEMINI_API_KEY`).
**Environment only. Never in the repo** — `.gitignore` covers `.env`. These do
not travel with git; set them again on the desktop.

> **Do not make a vision call until the owner confirms billing is enabled.**
> Free-tier submissions are used for training and may be human-reviewed.
> Client NDA reference photos have already gone through the free tier — the
> owner has been told. Paid tier does not train on submitted content.
