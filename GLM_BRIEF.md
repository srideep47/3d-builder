# GLM-5.3 WORK ORDER — Client Delivery Pipeline

> **You are the implementing agent for this repository.** This file is your
> complete brief. It is self-contained: everything you need to understand the
> target, the client's contract, the reference material, and the exact work to
> perform is here or explicitly linked.
>
> **You cannot see images.** You are a text-only model. Section 5 is a written
> transcription of every reference image, produced by a vision-capable reviewer.
> Treat §5 as your eyes. Do not attempt to open, decode, or reason about the
> image files directly — and never claim to have seen them.

---

## 1. Mission

We are a freelance 3D asset supplier. A client (**MetaZtech**) sends us
**reference photographs + measurements**; we return **production-grade 3D models
with textures** that must pass their automated validator and a human QA review.

Your job is **not** to model by hand. Your job is to **build the pipeline** in
this repository that turns a job packet into a compliant delivery package,
repeatably, for many different products.

**The first job is a mattress. The mattress is not the point.** Future jobs will
be sofas, lamps, cookware, packaging — anything in a product catalogue. Design
everything so the mattress is *data*, not *code*.

---

## 2. Read these first, in this order

| File | Why |
|---|---|
| `CLIENT_PIPELINE_PLAN.md` | The strategic plan. Explains *why* the work is sequenced this way, and records a review of a rejected earlier plan. Read fully. |
| `PROJECT_PLAN.md` §7 | **The gotchas ledger.** Hard-won failures. Violating these silently breaks things. Non-negotiable. |
| `AGENTS.md` | Operational summary + invariants. |
| `PLAN.md` | Original design rationale. Skim; consult when a design question arises. |

---

## 3. Absolute rules — never violate these

These are not style preferences. Each one encodes a bug that has already cost
real time, or a client requirement that causes rejected deliveries.

### 3.1 Correctness invariants (from `PROJECT_PLAN.md §7`)

1. **One Blender process per operation.** `measure` / `render_views` run in
   fresh processes with no shared scene. They MUST be passed `model_path`
   pointing at the exported step GLB.
2. **`matrix_world` is stale in background mode.** After setting
   `obj.location`, call `view_layer.update()` (`_update_view()`) before reading
   `obj.matrix_world`. Reading early silently collapses world-space clones.
3. **`src/blender/harness_script.py` is self-contained.** It runs *inside*
   Blender's Python. It must **never** import project code. No `from src...`.
4. **Mesh-gate checks load via `verifier.load_merged_mesh()`.** glTF export
   splits vertices per normal/UV and stores positions as node transforms. Plain
   `trimesh.util.concatenate` reports wrong bounds and false non-watertightness.
5. **After `apply_boolean`, filter object lists by identity (`is not`), never
   by `.name`.** Boolean-consumed parts have freed RNA structs.
6. **Procedural node shaders do not survive GLB export.** Bake them.
7. **The shape enum, the analyst prompt, and the harness `_build_shape`
   dispatch must stay in sync.** Change one, change all three.
8. **All lengths are metres internally.** Origin at bottom-centre `(0,0,0)`.
   Unit conversion happens only at the I/O boundary.

### 3.2 Client-contract rules

9. **NEVER infer, guess, or derive dimensions.** The owner supplies exact
   dimensions with an explicit unit for every job. If a job packet lacks
   dimensions, **stop and ask**. A previous planning attempt hallucinated
   `60 × 80 × 10 in` for this mattress; the job card actually reads
   `12 × 12 × 65 IN`. Both may be wrong. Only the owner's supplied value counts.
10. **Do not put packaging, naming, or zipping logic in `harness_script.py`.**
    Export *operations* belong in the harness; orchestration belongs in `src/`.
11. **Never let a product noun appear in the finishing layer.** No `mattress`
    in `src/client/gates.py`, `package.py`, or the UV/bake/export ops. Product
    knowledge lives only in `templates/<product_class>.yaml`.

### 3.3 Process rules

12. **Do not start work on a task whose predecessor's exit criteria are unmet.**
13. **Every task ends with a passing test or a smoke script.** "It should work"
    is not an exit criterion.
14. **Run `python -m pytest tests -q` before declaring any task done.** The
    baseline is **68 passing**. Never reduce it.
15. **Do not refactor code outside your current task's scope.**
16. **When blocked on a client fact, stop and ask.** Do not assume. §9 lists
    the currently open questions.

---

## 4. The client contract

This is what we are graded on. Every item is machine-checked by the client's
validator, so every item needs a matching local gate.

### 4.1 Deliverables per job code `<JOB>`

```
<JOB>.fbx                ≤ 10 MB     binary FBX
<JOB>_LP.glb             ≤ 15 MB     low-poly, baked maps
<JOB>_HP.glb             ≤ 50 MB     high-poly source
<JOB>_LP.usdz            limit unknown
<JOB>_BaseColor.png      exact prefix match required
<JOB>_Normal.png
<JOB>_Roughness.png
<JOB>_Metallic.png
<JOB>_AO.png
<JOB>.spp                Substance Painter project — see §9, may not be required
```

### 4.2 Their validator's gates (observed directly)

| Gate | Rule | Notes |
|---|---|---|
| **Naming** | `<JOB>.fbx` + `<JOB>_*` textures | Exact. Observed: "Expected: MAYA00007535.fbx and MAYA00007535_* textures" |
| **N-gons** | **count == 0**, strict | Observed: "Count: 0 — No n-gons detected" |
| **Polycount** | tier ceiling | Medium = **200,000**. Our job is tagged **Simple** — ceiling unknown, see §9 |
| **Dimensions** | L/W/H in **inches**, within tolerance | Compares against *client-provided* dims when available |
| **Orientation** | `Floor` | Model sits on ground plane |
| **File sizes** | per §4.1 | Observed caps: FBX 10MB, LP GLB 15MB, HP GLB 50MB |

### 4.3 The human QA loop

Their QA opens your model in a Babylon viewer **beside the reference photo**,
drags a divider, and annotates in red. An observed annotation reads:
*"Match the pattern details as per the ref."*

**Implication:** the real adversary is a human comparing **surface detail at
zoom**, not a dimension checker. Effort should follow that.

---

## 5. Reference material — written transcription (YOUR EYES)

Twelve images live at `D:\Work\Temp\Test Images`. You cannot read them. This is
what they contain.

### 5.1 The product

**Nisien 10 Inch Gel Memory Foam Hybrid Queen Mattress, Certified Foam.**
A pillowtop hybrid mattress. **Model the mattress only** — no bed frame, no
pillows, no room, no bedding.

### 5.2 Layer structure, top to bottom (from a corner close-up and a clean side elevation)

The silhouette is a **rounded box**: a domed, quilted top over a straight-sided
border. Reading down the side face:

| # | Element | Description |
|---|---|---|
| 1 | **Pillowtop crown** | Domed, puffed quilted panel. White knit fabric. Quilting reads as a **grid of rounded, scalloped/diamond cells** — soft-edged puffs separated by stitch valleys, not sharp creases. A faint **grey zigzag/chevron micro-print** is woven into the white knit. |
| 2 | **Tape edge (top)** | Thin **black/near-black binding tape** wrapping the top perimeter edge. Visible **herringbone / diagonal twill** weave. |
| 3 | **Air-mesh band** | **White 3D spacer mesh** — a perforated, honeycomb/oval-hole pattern. Distinctly open and textural. Roughly the tallest single light band. |
| 4 | **Tape edge (mid)** | As #2. |
| 5 | **Border stack** | Alternating horizontal bands: **white knit ribs** (smooth, slightly ribbed, cream-white) separated by **dark charcoal/near-black velvet bands** (soft sheen, catches light along the length). Approximately **3 dark velvet bands** separated by white ribs. |
| 6 | **Tape edge (bottom)** | As #2, wrapping the bottom perimeter edge. |
| 7 | **Base** | Flat underside, dark non-slip fabric. Rarely visible — keep it cheap. |

### 5.3 Distinctive features

- **Brand label.** A **black rectangular patch** on the **front-left side
  border**, portrait-oriented, spanning several bands vertically. Contents, top
  to bottom: `NISIEN` (wide-tracked serif caps) / `PURE COMFORT` (smaller caps)
  / `with body support` (small lowercase) / a **blue rounded-square icon**
  showing a white mattress glyph with three downward arrows / `Perfect Night`
  in italic script. **Best source: crop this directly from the corner close-up
  photo** — it is clean, well-lit, and nearly orthographic.
- **Vertical strap elements.** At intervals along the long sides there are
  **vertical dark straps** crossing the border bands. These are almost certainly
  **carry handles**. Observed in the in-room shots at roughly the quarter
  points. *Confidence: moderate.* Confirm with the owner before modelling them
  (§9).

### 5.4 What NOT to model

One image is a **marketing cutaway** listing eight internal layers (breathable
knitted cover, anti-fire foam, polyester fibre, gel-infused memory foam, support
core high-density foam, individually wrapped pocketed springs, high-density base
support foam, anti-fire foam base), shown as an exploded stack with blue
cylindrical springs.

**None of this is visible in the finished product. Do not model any internal
layer.** It is marketing, not a specification.

Also ignore: an instructional image captioned *"How To Measure Thickness Of
Mattress?"* with tape-measure overlays and red/green tick marks — it is consumer
guidance, not a spec.

### 5.5 The two client-system screenshots

**Job card (MetaZtech dashboard):**
```
Job Code:     MAYA00053153
Job Status:   WIP
Job Type:     Primary
Assign Artist: Manaswini M
Created On:   Aug 03, 2026
Title:        Nisien 10 Inch Gel Memory Foam Hybrid Queen Mattress,
              Certified Foam, Queen Size
Dimensions (L × W × H):  12 × 12 × 65 IN     ← see §3.2 rule 9
Complexity:   Simple
Poly Count:   —
Orientation:  (unset)
Buttons:      "UV & SPP Upload" (locked until UV & SPP Upload Ready), "Asset Hub"
Notice:       "Mesh generation is disabled for Simple complexity jobs."
```

**Validator result panel** (from a different job, `MAYA00007535`, showing all
gates green) — this is the source of §4.2. It also displayed
`Polycount: 75,416` against `Max: 200,000` for a *Medium* job, and file sizes
`FBX 2.10MB / LP GLB 2.77MB / HP GLB 7.62MB / LP USDZ 6.22MB`.

---

## 6. Repository state

### 6.1 What exists and works (do not rebuild)

- **Agent loop** — `src/agent/loop.py`: analyst → build → measure → render →
  gates → corrector, with progress events, cancel, run-dir reuse.
- **Spec layer** — `src/spec/`: ObjectSpec v2 (pydantic), resolver, validation.
  Shapes: box, rounded_box, cylinder, tapered_cylinder, sphere, cone, torus,
  tapered_extrude, revolve_lathe, extrude, sweep, organic.
- **Blender harness** — `src/blender/harness_script.py` (~1500 lines,
  self-contained): build_from_spec, measure, render_views, modifiers (bevel,
  subdiv, radial/linear array, mirror, boolean), `bake_materials`, `export_any`.
- **Gates** — `src/agent/verifier.py`: dimension gate (±1 mm) + mesh gate
  (watertight, tri budget, real-world scale).
- **Interfaces** — Typer CLI (`src/cli.py`), MCP stdio server
  (`src/mcp_server.py`), FastAPI + WebSocket studio (`src/webapp/`, `web/`).
- **Materials** — `src/materials/pbr.py`: 12 Principled-BSDF presets.
- **Tests** — `tests/`, **68 passing**, ~60s.

### 6.2 What is missing (this is your work)

There is **no** UV stage, **no** high-poly/low-poly split, **no** bake stage,
**no** FBX or USDZ export path, **no** packaging, **no** naming enforcement,
**no** n-gon check, and **no** file-size gate.

The repo is a *dimensional-accuracy machine*. The client grades *surface
appearance and deliverable compliance*. That gap is the entire work order.

### 6.3 Parked — do not work on this

`src/img3d/` and `services/img3d_service/` (neural image-to-3D bake-off).
Neural output is blobby and cannot satisfy a zero-n-gon quad-topology gate.
**Leave the code in place; do not extend it, do not delete it.** It becomes
relevant only if an upholstered-organic product class arrives.

### 6.4 Uncommitted work in the tree

`src/ai/vlm.py` + `tests/test_vlm.py` (local vision-model client) plus edits to
`loop.py`, `aptos.py`, `config/ai.yaml`, `web/js/app.js`. **Task T0 is to commit
this.** Do not begin new work on a dirty tree.

---

## 7. The work — tasks in order

Do these strictly in sequence. Each has explicit exit criteria.

---

### T0 — Clean the tree
Commit the in-flight VLM work as one coherent commit.
- **Exit:** `git status` clean; `pytest tests -q` = 68 passed.

---

### T1 — Compliance spine
*Build the gates before the geometry, so every later step is measurable.*

**Create `src/client/job.py`** — job intake model (pydantic):
```
job_code: str                     # e.g. "MAYA00053153"
dims: {length, width, height, unit}   # unit is REQUIRED and explicit
complexity: Literal["simple","medium","complex"]
orientation: Literal["floor","wall","ceiling","tabletop"]
product_class: str                # selects templates/<class>.yaml
part_scope: str                   # free text, e.g. "mattress only, no bed frame"
reference_dir: Path
```
Load from a `job.yaml`. **Fail loudly if `dims` or `unit` is absent** — never
default a unit.

**Create `src/client/gates.py`** — pure functions over a package directory,
one per §4.2 row: `check_naming`, `check_ngons`, `check_polycount`,
`check_dimensions`, `check_orientation`, `check_file_sizes`. Each returns a
structured result `{gate, passed, expected, received, message}` mirroring the
client's panel layout.

**Add harness ops** in `harness_script.py`: `op_count_ngons` (count faces with
`len(poly.vertices) > 4`), `op_topology_report` (tri/quad/ngon counts, loose
geometry, non-manifold edges).

**Add a unit layer** — metres ↔ inches at the boundary only. Internals stay
metric (rule 8).

**Add `python -m src.cli validate <package_dir> --job job.yaml`.**

- **Tests:** a synthetic package that passes every gate, plus one deliberately
  broken fixture per gate proving each fails independently.
- **Exit:** the CLI reproduces the client's validator panel locally, and
  `pytest tests -q` ≥ 68 + new tests.

---

### T2 — Export & packaging

**Add harness ops:** `op_export_fbx` (binary, FBX 2020) and `op_export_usdz`.

**Verify empirically — do not assume:**
- **FBX axis and unit scale.** Export, re-import into a fresh Blender process,
  assert bounds and up-axis survive a round trip. Their `Orientation: Floor`
  gate implies a specific convention; discover it, encode it, test it.
- **Whether Blender 4.5's USD exporter writes `.usdz` directly.** If it does
  not, add a USD → USDZ conversion step. Report what you find.

**Create `src/client/package.py`** — assembles `<JOB>.*` per §4.1 into
`output/packages/<JOB>/`, writes `qa_report.json` containing every gate result.

- **Exit:** an existing golden benchmark model (`input/benchmarks/`) exports as
  a complete package and passes every T1 gate. Round-trip axis test passes.

---

### T3 — UV + high-poly/low-poly bake pipeline
*The real engineering. Do not rush this.*

**Add harness ops:**
- `op_uv_unwrap` — smart project + island packing + **normalised texel
  density**. Non-overlapping islands.
- `op_bake_maps` — HP → LP bake of **Normal (OpenGL convention)**, **AO**,
  **Curvature**. Configurable resolution, default 4096.
- `op_decimate_to_budget` — reduce to a target triangle count while preserving
  UVs and silhouette.

**Extend ObjectSpec v2** with a per-part `detail` block: displacement pattern,
tiling scale, material assignment. Remember rule 7 — enum, prompt, and dispatch
move together.

**Zero-n-gon strategy — in this order:**
1. **Build quad-clean.** Loop cuts and controlled topology, not boolean soup.
2. **Verify** with `op_count_ngons`.
3. **Only if verification fails**, triangulate as a last-resort net — and
   **log loudly** when it fires. Triangulating by default destroys edge flow,
   wrecks UV islands and shading, and inflates polycount. It is a net, not a
   strategy.

- **Exit:** a test cube with procedural displacement bakes a correct normal map;
  a round trip through FBX preserves UVs and tangents; n-gon count is 0 by
  construction (the net never fires on the test asset).

---

### T4 — Reference implementation: the mattress

Build `templates/mattress.yaml` from the §5 transcription and run
`MAYA00053153` end-to-end with **owner-supplied dimensions**.

**Geometry approach:** one contiguous **rounded-box shell** with **horizontal
loop cuts** defining the bands from §5.2. This keeps the mesh quad-clean and
n-gon-free *by construction*. Tape edges are separate **swept curves** along the
perimeter. Quilting lives in the **HP as displacement** and reaches the LP only
as a **baked normal map**.

**Surface sources** (all free and licence-safe):
| Surface | Source |
|---|---|
| Diamond quilt | Procedural — regular grid → displacement → baked normal |
| Knit micro-weave | CC0 fabric scan (ambientCG / Poly Haven) |
| Air-mesh band | Procedural hex/dot pattern, or CC0 mesh-fabric scan |
| Velvet bands | CC0 velvet scan, sheen + low roughness |
| Tape edge | Procedural herringbone swept along the curve |
| Brand label | Crop from the reference photo (§5.3) |

**Do not** use AI-generated fabric textures. Diffusion output does not tile
seamlessly and cannot produce a true normal map — a colourised guess reads wrong
under moving light, which is exactly what their Babylon viewer does.

**Do not** download a base mesh from the web. Reselling requires a licence
permitting commercial redistribution; most marketplace licences do not, and
modification does not clear it. The geometry here is a rounded box.

- **Exit:** package passes every local gate; `qa_report.json` all-green; renders
  produced for owner side-by-side review.

---

### T5 — Generalise

- Extract `templates/<product_class>.yaml` as the **only** place product
  knowledge lives (rule 11).
- Wire vision decomposition into analyst context (§8).
- Web UI: job-intake form, compliance panel mirroring the client's validator,
  package download.
- **Exit:** a product from a **different taxonomy row** (see
  `CLIENT_PIPELINE_PLAN.md §8.1`) passes with **no new code in the finishing
  layer**. If it needs new code there, the abstraction leaked — fix it before
  adding a third class.

---

## 8. The vision sidecar — scope it honestly

`src/ai/vlm.py` already exists. Generalise it behind a `VisionProvider` ABC so
the slot can be filled by a local model or a hosted API without touching call
sites.

Two roles, with different worth:

- **Reference decomposition (high value).** "List the parts, bands, materials,
  and where the label sits." Genuinely improves the spec.
- **Render-vs-reference triage (advisory only).** Reliably catches gross
  failures — missing band, wrong colour, flipped orientation, absent label. It
  will **not** reliably judge quilt-pattern pitch, which is exactly what the
  human QA marks in red.

**Never gate a release on a VLM score.** Record both verdicts in
`qa_report.json` as advisory. A numeric "visual QA ≥ 90%" release criterion is
fake precision; do not implement one.

---

## 9. Open questions — stop and ask, do not assume

| # | Question | Blocks |
|---|---|---|
| 1 | Is `.spp` (Substance Painter Project) a **hard** requirement, or are baked PNG sets acceptable? | The texture-authoring stage. Build the Blender bake path first regardless; Substance is designed as one swappable stage. |
| 2 | What is the **Simple** complexity tier's polycount ceiling? (Medium = 200,000) | T1 `check_polycount`. Budget ~50k tris LP until answered. |
| 3 | What FBX **axis and unit convention** does their validator expect? | T2. Discover empirically if unanswered, and document what you chose. |
| 4 | Are the **vertical strap elements** on the side border carry handles, and should they be modelled? (§5.3, moderate confidence) | T4 part list. |
| 5 | Exact dimensions for `MAYA00053153` | T4. **The owner supplies these.** Never infer. |

---

## 10. Verification

```bash
python -m pytest tests -q          # baseline 68 passed — never reduce
python -m src.cli health           # endpoint + Blender + vision status
python scripts/benchmark_golden.py # AI benchmark against golden specs
```

Blender-marked tests auto-skip when no Blender is found; that is expected on
machines without `tools/blender-*`.

---

## 11. Definition of done for the whole work order

1. `python -m src.cli validate <package_dir>` reproduces the client's validator
   panel locally, and every gate is independently tested.
2. A job packet (`job.yaml` + references + owner dimensions) produces a complete
   `output/packages/<JOB>/` with all §4.1 files, correctly named and sized.
3. N-gon count is **0 by construction** on the reference asset — the
   triangulation net never fires.
4. FBX axis/unit convention is verified by round trip, not assumed.
5. A second product class passes with no new finishing-layer code.
6. `pytest tests -q` ≥ 68 passed, and the gotchas in `PROJECT_PLAN.md §7` are
   all still respected.

---

## 12. How to report progress

After each task, state plainly:
- What you changed, by file path.
- What you verified, with the command and its actual output.
- What you could not verify, and why.
- Any assumption you were forced to make, flagged explicitly.

If a test fails, say so with the output. If you skipped part of a task, say
which part and why. Do not report a task complete unless its exit criteria are
actually met.
