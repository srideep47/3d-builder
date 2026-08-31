# Client Production Pipeline Plan — MetaZtech 3D Asset Delivery

> **Status:** proposed, 2026-09-01. Supersedes the Gemini `implementation_plan.md`
> draft (see §1 for why). Companion to `PROJECT_PLAN.md` (project history +
> gotchas) and `PLAN.md` (original design rationale).
>
> **Scope decision (owner, 2026-09-01):** build the **repeatable pipeline**, not
> a one-off asset. Dimensions are **supplied by the owner as text per job** —
> the pipeline never OCRs or infers them from the job card.

---

## 1. Verdict on the Gemini plan

The high-level shape is right — vision sidecar for the eye, GLM-5.3 for the
spec, Blender for the hands, gates before delivery. **Do not execute it as
written.** It was authored without reading the client's own job card and
validator panel, and it gets six things wrong that would each cause a rejected
delivery.

| # | Gemini said | Reality (from the client screenshots) |
|---|---|---|
| 1 | Dimensions `60 × 80 × 10 in` | **Invented.** The job card reads `12 × 12 × 65 IN`. Neither is safe to assume — the owner now supplies dims explicitly. **Never let a model infer dimensions.** |
| 2 | Polycount `< 80,000` | Ceiling is **200,000** for *Medium* complexity. The limit is **tier-driven**, and this job is tagged **Simple** — whose ceiling we do not yet know. |
| 3 | `FBX < 10MB, GLB < 15MB, USDZ < 10MB` | Real gates: **FBX ≤ 10MB, LP GLB ≤ 15MB, HP GLB ≤ 50MB, LP USDZ (limit unset)**. |
| 4 | One mesh, one GLB | The validator wants **LP and HP as separate deliverables**. That means a real **high-poly → low-poly bake workflow**, which this repo has none of. This is the single largest engineering gap and Gemini missed it entirely. |
| 5 | Textures = "Substance PBR maps" | The job card has a **`UV & SPP Upload`** button. **SPP = Substance Painter Project** — a proprietary Adobe format Blender cannot write. See §4. |
| 6 | N-gon fix: triangulate then quadify | Technically produces 0 n-gons, but it is a **demolition charge**: it destroys edge flow, wrecks UV islands and shading, and inflates polycount. Correct approach is to *build* quad-clean and keep triangulation as a last-resort net. |

Two more misses worth naming: it ignored the **Orientation gate** (`Expected: Floor`),
and it proposed putting `op_package_client_delivery` inside
`src/blender/harness_script.py` — which violates the repo's hard invariant that
the harness stays self-contained and Blender-side. Export ops belong there;
naming, zipping and the QA report belong in `src/`.

Finally, its release criterion "Gemini Visual QA score ≥ 90%" is fake precision.
A VLM score is not a calibrated instrument. Treat visual comparison as
**advisory triage**, never as a pass/fail gate (§6).

---

## 2. The real client contract (decoded from the screenshots)

This is what we are actually being graded on. Every item here is machine-checked
by their validator, so every item gets a matching local gate.

**Deliverables per job code `<JOB>`:**

```
<JOB>.fbx                  ≤ 10 MB    binary FBX
<JOB>_LP.glb               ≤ 15 MB    low-poly, baked maps
<JOB>_HP.glb               ≤ 50 MB    high-poly source
<JOB>_LP.usdz              limit TBD
<JOB>_BaseColor.png        texture set, exact prefix match
<JOB>_Normal.png
<JOB>_Roughness.png
<JOB>_Metallic.png
<JOB>_AO.png
<JOB>.spp                  Substance Painter project — see §4
```

**Gates:**

| Gate | Rule | Current repo status |
|---|---|---|
| Naming | `<JOB>.fbx` + `<JOB>_*` textures, exact | ❌ none |
| N-gons | count **== 0**, strict | ❌ none |
| Polycount | tier ceiling (Medium = 200,000; Simple = TBD) | ⚠️ mesh gate has a tri budget, not tier-aware |
| Dimensions | L/W/H in **inches**, within tolerance | ✅ exists (metres) — needs unit layer |
| Orientation | `Floor` — sits on ground plane | ✅ origin convention already enforced |
| File sizes | per-format caps above | ❌ none |

**Their QA loop** (from the *Zoom & Compare* screenshot): a human QA opens your
model in a Babylon viewer beside the reference photo, drags a divider, and
annotates in red — *"Match the pattern details as per the ref."* So the real
adversary is a **human comparing surface detail at zoom**, not a dimension
checker. That single fact should drive where we spend effort.

---

## 3. Honest fit assessment: is 3d-builder on the right track?

**Partly. The foundation is sound; the pipeline is aimed at the wrong axis.**

What you built is a **dimensional-accuracy machine**: analyst → parametric
primitives → measure back → gate on millimetres → correct. That is genuinely
good engineering, and the gotchas ledger in `PROJECT_PLAN.md §7` is worth real
money. Keep all of it.

But look at what this client actually pays for. The Nisien mattress is, in
geometry terms, **a rounded box with horizontal bands and a piped edge**. Your
existing `rounded_box` + `sweep` shapes can build it in an afternoon. There is
almost no interesting geometry problem here.

The entire job is **surface appearance and deliverable compliance** — quilting,
air-mesh, velvet, tape-edge, a stitched label, clean UVs, a bake, five correctly
named PNGs, four correctly sized files. **The repo currently does none of that.**
There is no UV stage, no bake stage, no LP/HP split, no FBX/USDZ export path, no
packaging, no naming, no n-gon check.

So the answer is: **you are not off track, you are half-built, and the half
that's missing is the half this client grades.** Roughly:

```
Existing  ██████████░░░░░░░░░░  analyst · spec · build · measure · dimension gate
Missing   ░░░░░░░░░░██████████  UV · HP/LP · bake · texture authoring · export · package · compliance gates
```

The M4 work already in flight (img3d neural service, VLM sidecar) is **not on
the critical path for this client** and should be parked. Neural image-to-3D
produces blobby, unwrappable, n-gon-free-by-accident meshes that will fail a
zero-n-gon quad-topology gate — it is the wrong tool for a rectilinear product
catalogue. Keep the code, deprioritise the bake-off.

### Two side questions you raised

**Blender MCP — should we switch?** No. Blender MCP drives a *live* GUI Blender
via an addon. You would trade away the thing that makes this repo good:
deterministic, headless, one-process-per-op, reproducible, batchable. Your
harness is already better for production. Keep it, and keep exposing it through
your own MCP server.

**"Download a similar model from the web and reshape it."** Legally risky and
technically unnecessary here. Reselling a downloaded model to a client requires
a licence that permits commercial redistribution — most marketplace and
Sketchfab licences do not, and "I modified it" does not clear it. For a mattress
the geometry is a rounded box; there is nothing to gain. **CC0 sources
(ambientCG, Poly Haven) are genuinely safe** and we should use them heavily —
for *textures*, which is where they actually help.

---

## 4. The Substance question, answered directly

You asked: *can AI do it, or is it easy enough to do myself?*

**Do you need Substance at all?** For this asset — **no.** Every surface here is
either procedural or a CC0 fabric scan:

| Surface | Best source | Difficulty |
|---|---|---|
| Diamond quilt pattern | Procedural in Blender (regular diamond grid → displacement → bake normal) | Easy |
| Knit fabric micro-weave | CC0 scan (ambientCG `Fabric*`) | Trivial |
| White air-mesh band | Procedural hex/dot pattern + alpha, or CC0 mesh-fabric scan | Easy |
| Dark velvet bands | CC0 velvet scan + sheen | Trivial |
| Tape-edge piping | Procedural herringbone along a swept curve | Easy |
| NISIEN brand label | **Crop it straight out of the reference photo** — you have a clean, well-lit, near-orthographic shot | Trivial |

Blender's baker writes all five maps at 4K perfectly well. **My recommendation:
build the Blender-only path first.** It is fully automatable and gets you a
deliverable.

**Is Substance Painter automatable?** Partially, and better than most people
think — Painter ships a **Python API** (`substance_painter.project`, `.baking`,
`.export`) that can open a mesh, bake mesh maps, apply smart materials, and run
export presets. So once you hand-author *one* template project per product
class, generating a per-job `.spp` is largely scriptable. What is *not*
automatable is the artistic authoring of that first template — that is a human
sitting down for an afternoon.

**Is it easy enough to do yourself?** Yes. For a mattress, Substance Painter is
maybe a two-evening learning curve to competence — you are stacking fill layers
with masks, not sculpting. The skill transfers to every future job.

**So the decision rule:** confirm with MetaZtech whether `.spp` is a *hard*
requirement or an optional convenience. If hard, buy the licence (~$US20/mo) and
author one mattress template; the pipeline is designed so Substance slots in as
a **single swappable stage** (§5, Stage 6) with the Blender baker as fallback.
Do not let this block Milestone 1.

**On AI-generated textures (NanoBanana / ChatGPT):** I'd skip them for fabric.
Diffusion models do not produce *seamlessly tileable* output and cannot produce a
true normal map — a "normal map" from an image generator is a colourised
guess that will read wrong under moving light, which is exactly what their
Babylon viewer does. Procedural + CC0 scans beat it on quality and are free.
AI image-gen *is* useful for one thing here: reconstructing a clean, flat
version of the brand label if the photo crop proves too distorted.

---

## 5. Target architecture

Keep the four existing layers. Add a **Finishing layer** — everything between
"geometry exists" and "client package on disk". This is the new work.

```
┌ Job Intake ─────────────────────────────────────────────────────────────┐
│ job.yaml: job_code, dims (owner-supplied, explicit unit), complexity     │
│ tier, orientation, reference images, part-scope ("mattress only")        │
└────────────────────────────┬────────────────────────────────────────────┘
                             ▼
┌ Analysis (dual-brain) ──────────────────────────────────────────────────┐
│ Vision sidecar → structured part/material/decal JSON  ──►  GLM-5.3       │
│ (Qwen2.5-VL local or hosted API, behind one ABC)          → ObjectSpec   │
│ Owner-supplied dims are injected verbatim — never inferred.              │
└────────────────────────────┬────────────────────────────────────────────┘
                             ▼
┌ Build (existing harness) ───────────────────────────────────────────────┐
│ build_from_spec → measure → render_views → dimension + mesh gates        │
└────────────────────────────┬────────────────────────────────────────────┘
                             ▼
┌ ★ FINISHING LAYER (new) ────────────────────────────────────────────────┐
│  1. Topology pass    quad-clean, n-gon count == 0, tier polycount        │
│  2. HP generation    subdiv + procedural displacement (quilting, weave)  │
│  3. LP generation    banded shell, decimate to tier budget               │
│  4. UV unwrap        non-overlapping islands, normalised texel density   │
│  5. Bake             HP→LP: Normal(OpenGL) · AO · Curvature              │
│  6. Texture author   ┌ Blender procedural + CC0 scans  (default)         │
│                      └ Substance Painter .spp template (optional swap)   │
│  7. Export           FBX(bin, axis+scale verified) · LP/HP GLB · USDZ    │
│  8. Package          <JOB>.* naming, size check, qa_report.json          │
└────────────────────────────┬────────────────────────────────────────────┘
                             ▼
┌ Compliance gates (mirror their validator exactly) ──────────────────────┐
│ naming · ngons==0 · polycount(tier) · dims(inches) · orientation · sizes │
│ + advisory: VLM render-vs-reference triage (never blocks)                │
└─────────────────────────────────────────────────────────────────────────┘
```

**Design rule:** every gate above is a *local reimplementation of a gate they
actually run*. We should never learn about a failure from their validator.

---

## 6. On the vision sidecar — set expectations correctly

You already have `src/ai/vlm.py` written and tested (uncommitted). Keep its
interface; generalise it behind a `VisionProvider` ABC so the slot can be filled
by local Qwen2.5-VL **or** a hosted vision API without touching call sites.

Use it for two things, and be clear about what each is worth:

- **Reference decomposition (high value).** "List the parts, bands, materials,
  and where the label sits." This genuinely helps GLM-5.3 write a better spec,
  and it is exactly what a 7B local VLM is good at.
- **Render-vs-reference triage (moderate value, advisory only).** It will
  reliably catch gross failures — missing band, wrong colour, flipped
  orientation, absent label. It will **not** reliably judge "is the quilt
  pattern pitch right", which is precisely what their human QA marks in red.

For the triage role a hosted frontier vision model will substantially outperform
a local 7B. Given you run local-first, my suggestion: **local Qwen for
decomposition, hosted API for final triage**, and record both verdicts in
`qa_report.json`. Never gate a release on either.

**The honest bottom line:** no VLM currently closes the loop to "top-notch
quality" on its own. What closes it is your eye on a side-by-side, once, before
you upload — the pipeline's job is to make sure that's the *only* manual step
left, and that everything mechanical is already provably green.

---

## 7. Milestones

Each milestone ends in something verifiable. Nothing is "done" without a test
or a smoke script.

### M5 — Compliance spine *(do this first; it is cheap and it de-risks everything)*
Build the gates before the geometry, so every later step is measured.
- `src/client/job.py` — job intake model: `job_code`, `dims{l,w,h,unit}`,
  `complexity`, `orientation`, `part_scope`, `reference_dir`.
- `src/client/gates.py` — naming, n-gon, tier polycount, inch-dimension,
  orientation, file-size gates. Pure functions over a package directory.
- Harness ops: `op_count_ngons`, `op_topology_report`.
- Unit layer: metres ↔ inches at the boundary only; internals stay metric.
- Tests: a synthetic package that passes, and one deliberately broken per gate.
- **Exit:** `python -m src.cli validate <package_dir>` reproduces their
  validator panel locally.

### M6 — Export & packaging
- Harness ops: `op_export_fbx` (binary, FBX 2020, axis + unit scale
  **verified against a round-trip import**), `op_export_usdz`.
- `src/client/package.py` — assembles `<JOB>.*`, writes `qa_report.json`.
- **Verify explicitly:** Blender 4.5's USD exporter writing `.usdz`, and FBX
  axis convention (their `Floor` orientation gate implies a specific up-axis —
  confirm empirically, do not assume Z-up survives).
- **Exit:** an existing golden benchmark model exports as a fully compliant
  package and passes every M5 gate.

### M7 — UV + HP/LP bake pipeline *(the real engineering)*
- Harness ops: `op_uv_unwrap` (smart project + island packing + texel-density
  normalisation), `op_bake_maps` (HP→LP Normal/AO/Curvature), `op_decimate_to_budget`.
- ObjectSpec v2 extension: `detail` block per part — displacement pattern,
  tiling scale, material assignment.
- Zero-n-gon strategy: build quad-clean; verify with `op_count_ngons`; triangulate
  **only** as a final net if verification fails, and log loudly when it fires.
- **Exit:** a test cube with procedural displacement bakes a correct normal map;
  round-trip through FBX preserves UVs and tangents.

### M8 — Reference implementation: the Nisien mattress
Full end-to-end on `MAYA00053153` with owner-supplied dims. Part breakdown in §8.
- **Exit:** package passes all local gates; you are personally satisfied with a
  side-by-side against the reference photos.

### M9 — Generalise
- `templates/<product_class>.yaml` presets (§8.1) as reusable spec + detail data.
- Vision decomposition wired into analyst context.
- Web UI: job-intake form, compliance panel mirroring their validator, package download.
- **Exit:** a product from a **different taxonomy row** (§8.1) goes through with
  no new code in the finishing layer. If it needs new code there, the walls
  leaked — fix them before adding a third class.

**Parked, not cancelled:** img3d neural bake-off (M4 track 1). It is the wrong
tool for rectilinear, quad-topology, zero-n-gon products, which is what the
first job is. It becomes the *right* tool the day an upholstered-organic job
arrives (§8.1) — at which point the open question is whether neural output can
be retopologised to quad-clean within budget, which the bake-off should measure
rather than assume.

---

## 8. Reference implementation — Nisien mattress part breakdown

> **The mattress is the first job, not the template.** Real jobs will be sofas,
> lamps, cookware, packaging — whatever the catalogue holds. Everything in §5–§7
> is deliberately product-agnostic: the compliance spine, export, packaging, UV
> and bake stages know nothing about mattresses. This section is worked in
> detail only because the first asset is where a pipeline's assumptions get
> tested. Treat it as a case study, and resist letting mattress-specific logic
> leak upward out of §8 — see §8.1 for where product knowledge is allowed to live.

From the photographs, top to bottom. Model the **mattress only** — no bed frame,
pillows, or environment.

| # | Part | Geometry | Surface treatment |
|---|---|---|---|
| 1 | Pillowtop panel | Rounded box top, subdivided | Diamond quilt displacement → baked normal; white knit micro-weave; subtle grey zigzag print |
| 2 | Top tape-edge | Swept profile along perimeter curve | Dark charcoal herringbone binding tape |
| 3 | Upper air-mesh band | Side band segment | White 3D spacer mesh — honeycomb/dot pattern, high roughness |
| 4 | Mid tape-edge | Swept profile | as #2 |
| 5 | Side border | Alternating horizontal ribs | White knit ribs + dark charcoal velvet bands (sheen, low roughness) |
| 6 | Brand label | Flat inset patch, front-left | Decal cropped from reference: `NISIEN / PURE COMFORT / with body support` + blue icon + `Perfect Night` |
| 7 | Bottom tape-edge | Swept profile | as #2 |
| 8 | Base | Flat underside | Non-slip dark fabric; barely visible, keep cheap |

**Geometry approach:** one contiguous rounded-box shell with horizontal loop
cuts defining the bands — this keeps it quad-clean and n-gon-free *by
construction*. Piping is separate swept curves. Quilting lives in the HP as
displacement and reaches the LP only as a baked normal map.

**Do not model the internal foam/spring layers** shown in the cutaway diagram —
that image is marketing, not a spec. Nothing internal is visible in the product.

**Open item:** the *Simple* complexity tier's polycount ceiling is unknown
(Medium = 200,000). Ask MetaZtech, or budget conservatively to ~50k tris LP
until told otherwise.

### 8.1 Where product-specific knowledge is allowed to live

Since jobs will vary widely, the pipeline needs exactly one place for
per-product knowledge, and hard walls everywhere else.

- **Allowed:** `templates/<product_class>.yaml` — a *product-class preset*
  holding the part decomposition, detail/displacement patterns, material
  assignments, and a sensible LP budget. The mattress becomes
  `templates/mattress.yaml`. A sofa becomes `templates/sofa.yaml`. These are
  data, authored once, reused per job.
- **Allowed:** the vision decomposition output, which is per-job by nature.
- **Forbidden:** any product noun in `src/client/gates.py`, `package.py`, the
  export ops, or the UV/bake ops. If a mattress-shaped `if` appears in the
  finishing layer, the abstraction is wrong.

**Rough product-class taxonomy to design against**, so the finishing layer is
exercised by more than one shape early:

| Class | Examples | Dominant challenge |
|---|---|---|
| Rectilinear soft goods | mattress, cushion, packaging | Surface pattern + bake (this job) |
| Hard-surface manufactured | appliances, cookware, furniture frames | Exact dims, bevels, hard edges, metal PBR |
| Turned / radial | lamps, vases, bottles | Lathe profiles, seamless radial UVs |
| Upholstered organic | sofas, plush, cushions with folds | Genuine sculpting — **the one class where neural img3d may earn its place** |
| Assemblies | multi-part products, sets | Hierarchy, naming, per-part materials |

Pick the **second** job from a *different* row before generalising in M9 — that
is what proves the walls hold.

---

## 9. Risks & open items

| Risk | Mitigation |
|---|---|
| `.spp` is a hard requirement | Confirm with client. Blender path ships first regardless; Substance is one swappable stage. |
| FBX axis/unit mismatch fails their Orientation gate | Round-trip verification test in M6 — export, re-import, assert bounds and up-axis. Do not assume. |
| *Simple* tier polycount unknown | Ask; budget ~50k until answered. |
| USDZ export unsupported/limited in Blender 4.5 | Verify in M6 smoke test; fallback is a USD→USDZ conversion step. |
| Human QA rejects on pattern fidelity | Highest-fidelity path is procedural quilting authored to match reference *pitch and scale*, measured against the photo — not eyeballed. Plan a calibration step. |
| Web-sourced base meshes | Avoid. Textures from CC0 sources only (ambientCG, Poly Haven). |
| Dimension ambiguity (box vs product) | Resolved: owner supplies dims explicitly per job. Pipeline still flags implausible L/W/H ratios as a sanity net. |

---

## 10. Immediate next actions

1. **Commit the in-flight VLM work** (`src/ai/vlm.py`, `tests/test_vlm.py`, loop
   and config changes) so the tree is clean before new work starts.
2. **Ask MetaZtech three questions:** is `.spp` mandatory? what is the *Simple*
   tier polycount ceiling? what FBX axis/unit convention does their validator expect?
3. **Start M5** — the compliance spine. It is a day or two of work, needs no
   artistic judgement, and makes every subsequent step measurable.
