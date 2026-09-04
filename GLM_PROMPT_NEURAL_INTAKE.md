# GLM WORK ORDER — Neural intake: images + prompt → constrained deliverable

> Authored by the reviewer seat, 2026-09-03, after building and measuring the
> whole TRELLIS 2 path live on this machine. **Every number in §2 is a direct
> measurement taken tonight, not an estimate.**
>
> This is a new phase. It does not replace `GLM_MASTER_PROMPT.md` — the
> standing rules, stop conditions and reporting discipline there still apply.

---

## 1. What the owner asked for

> A UI where I give images in (at specific angles) plus a prompt describing
> the model and all its constraints. The TRELLIS 2 workflow runs
> automatically, produces the 3D model, then our system uses AI to analyse
> the output and make it satisfy the constraints if it does not already.

Four stages: **intake → generate → analyse → conform.**

Stages 1, 3 and 4 are mostly built already. Stage 2 was built and measured
tonight. **Your job is the wiring, not the invention.** Read §3 before
designing anything — you will otherwise rebuild things that exist.

## 2. The environment is BUILT and MEASURED. Do not rebuild it.

### 2.1 What exists on this machine now

| Thing | Where |
|---|---|
| ComfyUI | `D:\Work\AI_Tools\ComfyUI` (v0.34.2) |
| **Its Python** | `D:\Work\AI_Tools\ComfyUI\venv311\Scripts\python.exe` — **3.11.0** |
| Launch | `venv311\Scripts\python.exe main.py --port 8189` |
| Models (25.6 GB) | `models\visualbruno\TRELLIS.2-4B-FP8`, `models\microsoft\TRELLIS.2-4B`, `models\facebook\dinov3-vitl16-pretrain-lvd1689m` |
| Node packs | `ComfyUI-Trellis2`, `ComfyUI-Easy-Use`, `rgthree-comfy` |
| Workflow | `user\default\workflows\Trellis2_MV_Combined.json` |

**The server is currently DOWN** (my background task ended; it exited
cleanly after a successful job, no crash). Restart it with the command above.

### 2.2 Environment landmines — I hit every one of these, do not repeat them

1. **The pack ships NO cp310 wheels.** Only cp311/312/313. ComfyUI's original
   Python 3.10 install cannot run TRELLIS 2 at all. That is why venv311 exists.
2. **`torch` must stay at 2.8.0+cu128.** The compiled wheels (`cumesh`,
   `o_voxel`, `flex_gemm`, `nvdiffrast`) are built against it. **Installing
   plain `xformers` silently upgrades torch to 2.11 and breaks all of them.**
   Pin `xformers==0.0.32.post2` and install with `--no-deps` if needed.
3. **`torchaudio` must be pinned to 2.8.0.** ComfyUI's `requirements.txt`
   pulls 2.11, which fails at import with `OSError: [WinError 127]` and looks
   like a wheel problem but is not.
4. **`flash_attn` is NOT installed** and is painful on Windows. Use
   `backend="sdpa"`. The saved workflow says `flash_attn` — it would crash.
5. **`ComfyUI-StableXWrapper` fails to import.** It does not matter — those
   nodes are bypassed (`mode: 4`). Do not spend time fixing it.
6. **DINOv3 is NOT gated.** The wrapper pulls a non-gated mirror
   (`visualbruno/dinov3-...`). No HuggingFace licence acceptance needed.

### 2.3 The node settings that actually work

`Trellis2LoadModel`:
```
modelname      = "visualbruno/TRELLIS.2-4B-FP8"   # fp8 — fits 16 GB
backend        = "sdpa"                            # NOT flash_attn
device         = "cuda"
low_vram       = True
conv_backend   = "flex_gemm"
sparse_backend = "xformers"
use_reconviagen= False                             # incompatible with fp8
```

### 2.4 The working graph, measured end to end

```
LoadImageWithTransparency ×4  (front/back/left/right)
  → PreProcessImage(remove_background=True) ×4
  → MeshWithVoxelMultiViewGenerator(pipeline_type="512", front_axis="z")
  → RemeshWithQuad
  → SimplifyMesh(target_face_num=50000)
  → FillHolesWithMeshlib
  → MeshWithVoxelToTrimesh
  → MeshTexturingMultiView(resolution=1024, texture_size=4096)
  → ExportMesh(file_format="glb")
```

Drive it over ComfyUI's HTTP API: `POST /prompt` with an **API-format** graph
(nodes keyed by id, `class_type` + `inputs`; links are `[node_id, slot]`),
poll `GET /history/{prompt_id}`, read schemas from `GET /object_info`.
**Build node inputs from `/object_info` defaults** — the saved UI workflow has
fewer widget values than the current nodes have inputs, and positional
mapping will silently mis-assign.

### 2.5 Measured results — four objects, this machine

| Object | Wall | Tris | Open edges | Generated ratio | Real ratio |
|---|---|---|---|---|---|
| Doormat | 364 s | 49,546 | 0 | 1 : 0.733 : 0.051 | 1 : 0.600 : 0.027 |
| **Cup** | 512 s | 48,452 | 0 | **1 : 0.778 : 0.556** | **1 : 0.778 : 0.556** |
| Desk | 678 s + 220 s tex | 48,386 | 0 | 1 : 0.589 : 0.578 | 1 : 0.625 : 0.500 |
| Mattress | 280 s | 47,790 | 6 | **1 : 0.999 : 0.255** | 1 : 0.750 : 0.150 |

**Read that table carefully — it contains the single most important finding.**

## 3. The findings your design must respect

### 3.1 Dimensional accuracy tracks INPUT VIEW SPREAD, not model quality

The cup came out **exact to three decimal places.** It had two genuinely
opposite side views.

The mattress came out **square** — 1 : 0.999 where the truth is 1 : 0.750.
All four of its images were variations of the same front/three-quarter view
of the long side. With no end-on view, nothing tells the model the bed is
longer than it is wide, so it defaults toward square.

**Therefore the UI must measure how different the four viewpoints actually are,
and WARN — loudly and visibly — when they are near-duplicates. It must NOT
refuse.** The owner's call: the run still goes ahead. The point is that a
square mattress is never a surprise afterwards.

Cheap and deterministic: compare the four uploads pairwise (downscaled
greyscale correlation, or perceptual-hash distance). Emit a **view-diversity
score** and show it before the run starts. **Measure the threshold on the four
sets in `Test Images/` before choosing it — do not guess a number.** The cup
set (two genuinely opposite views) and the mattress set (four near-identical
front views) are your calibration pair: whatever metric you pick must separate
those two clearly, or it is the wrong metric.

Record the score in the run manifest so a bad result can always be traced back
to its input, and so §4.0.5's router can use it.

### 3.2 TRELLIS gives 3 of the 5 required maps. Blender must supply 2.

Measured on the mattress GLB:

| Map | Present? |
|---|---|
| BaseColor | ✅ 4096² RGBA |
| Roughness | ✅ green channel of `metallicRoughnessTexture` |
| Metallic | ✅ blue channel |
| **Normal** | ❌ `normalTexture` ABSENT |
| **AO** | ❌ `occlusionTexture` ABSENT, red channel all zeros |

**There is no ComfyUI path to the missing two.** `BakeNormalMapFromMesh` and
`BakeAmbientOcclusion` exist as core nodes but consume `MESH`, TRELLIS emits
`TRIMESH`, and **no converter node exists** — I checked every node that
outputs `MESH` and every node that consumes `TRIMESH`.

`Trellis2ProjectHighPolyToLowPoly` sounds like the answer and is not: it
returns base colour and metallic-roughness, the same two we already have.

**So normal and AO come from `finish_delivery`'s existing HP→LP bake.** That is
also the better answer, because the normal map is where the quilt relief
lives, and a measured finding on this project is that a neural texture carries
no silhouette.

Splitting the packed map is trivial and already proven — glTF convention is
G = roughness, B = metallic. I wrote the three PNGs to
`output/cmp/t2_mattress/maps/` as a reference.

### 3.3 GLB is triangles-only, so the quads are lost at export

The graph really does quad-remesh, but **glTF has no quad primitive.** Every
exported GLB is triangulated. The client's n-gon gate therefore passes for the
wrong reason — everything is a triangle.

Repo rule already covers this: **FBX exports from the live quad-clean scene,
not the triangulated GLB.** Conforming must re-quad or preserve quads through
Blender before FBX export.

### 3.4 TRELLIS 2 accepts NO text prompt

There is no prompt, text or caption input on any of the 80 TRELLIS2 nodes.
fal.ai's own guide states it: *"Unlike text-to-image systems, Trellis 2
requires no prompts."* It conditions on DINOv3 vision features, not language.

**So the owner's prompt is consumed entirely by OUR side** — it populates the
JobCard constraints and steers the conform loop. Do not attempt to pass it to
TRELLIS. Do not add a fake prompt field that goes nowhere.

### 3.5 Two traps that produce false measurements

1. **UV seams inflate open-edge counts.** The textured desk measured 11,484
   open edges and looked broken. Merging vertices by position gave **0**. UV
   unwrapping duplicates vertices along seams. **Always merge by position
   before counting open edges**, or you will report a defect that is not there.
2. **`trimesh`'s `geometry[name].vertices` are LOCAL coordinates.** Without
   applying the scene-graph transform every part reads as sitting at the
   origin. I nearly reported a missing drawer because of this. Use the
   flattened graph transforms.

### 3.6 Material defect worth a gate

The mattress came back with **metallic averaging 34%** across fabric. Fabric
should be ~0%. The desk was correct (1% metallic, wood). So the metalness
channel is unreliable on soft goods and needs either an override from the
JobCard or a sanity gate.

### 3.7 texture_size has headroom

`texture_size` accepts **512 → 16384**; we ran 4096. `resolution` is
`[512, 1024, 1536]`; we ran 1024. Untested above that. Do not assume higher is
better — measure detail gain against wall clock before defaulting it up.

---

## 4. THE BUILD

### 4.0 FIRST — GPU sequencing. Smaller than it sounds; the hooks exist.

TRELLIS holds the card, Blender baking wants the card, a local vision model
wants the card. Only one fits in 16 GB. Today **nothing coordinates them** —
tonight I hand-worked around it by forcing Blender onto CPU.

**Correction to an earlier draft of this order:** this does not need a broker
built from scratch. Two unload hooks already exist and I verified both:

1. **`POST /free`** on the ComfyUI server —
   `{"unload_models": true, "free_memory": true}`. One HTTP call, frees the
   models, leaves the server up. (`server.py:1192`)
2. **`keep_models_loaded`** on `Trellis2LoadModel` — the pipeline already
   honours it and unloads each stage as it completes (`nodes.py` 1489, 4000,
   4005, 4101, 4119).

So the sequence is simply:

```
load TRELLIS → generate → POST /free → Blender bakes → (unload) → verdict
```

**What you actually have to build is small:**

- **A machine-wide lock** so two processes can never hold the card at once.
  ComfyUI, Blender and LM Studio are separate processes; nothing today
  prevents overlap. A file lock or semaphore is enough — do not over-engineer.
- **`unload()` on the `NeuralBackend` ABC**, which today has `load()` and no
  counterpart. For the ComfyUI provider it is the `/free` call.
- **Measure both `keep_models_loaded` settings.** `True` is faster but hogs
  the card; `False` frees per stage but pays an ~8 GB reload. Which is right
  depends on whether the verify-fix loop revisits generation. **Measure, do
  not assume.**

**Note:** if vision goes to **Gemini** it is an API call and consumes no VRAM
at all — only TRELLIS and Blender contend, and this gets easier. Local Qwen is
the documented fallback tier only: load, take the verdict, unload, return the
GPU to Blender.

**Exit criteria — and this is the one that matters:** PyTorch does not always
return VRAM to the OS even after `empty_cache()`, so `nvidia-smi` reading zero
proves nothing. The acceptance test is **a Blender GPU bake that actually
succeeds immediately after a TRELLIS generation**, run back to back, repeated
three times without failure.

### 4.0.5 THE ROUTER — decide neural vs template per job

**Owner's decision: the system chooses the path from the input. Build this.**

Not every job should go to TRELLIS. Measured tonight, on the same machine:

| | Parametric template | TRELLIS 2 |
|---|---|---|
| Mattress dimensions | **exact 80 × 60 × 12 in** | square, 1 : 0.999 |
| Topology | quad-clean, born valid | triangulated at GLB export |
| Polycount | 374–15,420 tri | ~48,000 tri |
| Fabric metallic | correct | **34% — wrong** |
| Label | real photo crop | smeared |
| Time | seconds | 5–10 minutes |

For a product a template covers, the template wins on **every axis that the
client validator checks**. Sending it to TRELLIS would be a downgrade.

**Routing order — try cheapest first, escalate on failure:**

1. **A `templates/*.yaml` exists for the job's `product_class`** → use the
   template. This is the mattress, and soon the pillow.
2. **No template, but the object is expressible in the 12 `ShapeType`
   primitives** and dimensions are exact-critical → **parametric spec**
   (the brain authors an `ObjectSpec`; proven working in the brain test —
   desk, cup and doormat all built dimensionally exact).
3. **Neither fits** — organic, sculpted, freeform, or the spec route failed
   its gates → **TRELLIS 2**.

**Inputs the router should weigh:**

- `product_class` matching a template — decisive, route 1
- Whether the prompt's shape description maps to available primitives
- **The §3.1 view-diversity score** — low diversity is a strong signal that
  neural will get proportions wrong, so prefer the spec route when it is an
  option, and warn hard when it is not
- Whether dimensional exactness is critical (it nearly always is)

**Three hard requirements:**

- **Record the decision and its reason in the run manifest**, every time. A
  disputed asset must show which path built it and why. Never let the choice
  be invisible.
- **Expose the choice in the UI as a control** — see §4.1.6. Auto is the
  default; the owner can force any path.
- **A forced path that cannot run must REFUSE, never silently fall back.**
  If the owner picks Template for a product class with no
  `templates/*.yaml`, say so and stop. A silent downgrade to another route is
  exactly the class of defect this project keeps shipping — the asset would
  look built and be built by something else entirely.

This is the `PLAN_AUTONOMOUS.md` §9 item 3 mesh-source interface finally
earning its keep: one contract, three sources, chosen per job.

### 4.1 Intake — extend, do not invent

`src/webapp/server.py` already has **14 endpoints** including `POST
/api/uploads`, `POST /api/build`, `GET /api/runs`, `POST /api/jobs`. Extend it.

`src/client/job.py` already parses a JobCard with dynamic constraints and the
placeholder-dimensions refusal. Extend it.

New UI needs:

1. **Four labelled image slots** — front, back, left, right. Labelled, not a
   bag of files: the graph binds them positionally and `front_axis` matters.
2. **The view-diversity check from §3.1**, with a visible warning.
3. **A prompt box** whose text is parsed into JobCard constraints —
   dimensions with unit, polycount ceiling, formats, axis convention, size
   caps and basis, texture resolution, tri-vs-face semantics.
4. **Explicit dimensions are mandatory.** Rule 9 does not bend for neural
   input: never infer them from the images. If the prompt has no dimensions,
   refuse exactly as `dims_placeholder` does today.
5. Live progress, and the generated model shown next to the reference images.

6. **A build-route control.** A single selector, defaulting to Auto:

   | Option | Behaviour |
   |---|---|
   | **Auto (recommended)** | §4.0.5 router decides |
   | Template | force `templates/<product_class>.yaml` |
   | Parametric spec | force brain-authored `ObjectSpec` |
   | Neural (TRELLIS 2) | force the ComfyUI path |

   **On Auto, show which route it would take and the one-line reason,
   before the run starts** — e.g. *"Template — `mattress.yaml` matches
   product_class"*, or *"Neural — no template, shape not expressible in
   primitives, view diversity 0.71"*. The owner sees the decision and can
   override it in the same place, without editing a job card.

   A forced route that cannot run refuses with a named reason (§4.0.5). It
   never silently falls back.

   Neural is the only route that needs the four images. When another route is
   selected or chosen, the image slots stay available — the references are
   still used by the analyst and the visual gate — but they stop being
   mandatory.

### 4.2 Generate — a ComfyUI backend behind the existing ABC

`services/img3d_service/providers/` already defines `NeuralBackend` with
`is_available()` / `load()` / `generate()`, and has working `tripo_sr`,
placeholder `trellis`, placeholder `hunyuan3d`.

**Add a `comfy_trellis2` provider that satisfies that same contract** and
drives the §2.4 graph over HTTP. Do not create a parallel path. Do not import
torch into the main environment — ComfyUI stays a separate process with its
own venv311, exactly like the existing service split.

Multi-view means `GenerateParams` needs more than one image. Extend it
additively (`image_paths` / a view dict) so `tripo_sr` keeps working.

**Exit criteria:** `img3d` CLI generates the cup from four views through the
provider, and the result matches tonight's numbers within noise.

### 4.3 Analyse — measured facts, then vision, in that order

The loop exists: `AgentLoop`, `AgentToolExecutor`, `Verifier`, iteration cap 8.

Measure first, and cheaply:

| Check | Threshold |
|---|---|
| Triangles | JobCard ceiling |
| Open edges (**after position merge**) | 0 |
| Bodies | 1 preferred; record the count |
| Aspect ratio vs JobCard | flag > 5% on any axis |
| n-gons | 0 |
| Metallic on declared-fabric surfaces | ~0 (§3.6) |
| The 5 maps present | all |

**Gates before eyes, always.** Vision runs only when gates are green, and
**vision never gates a release** — it is advisory, with documented misses.

The ratio check is the one that would have caught the square mattress. It is
absolute, not a proxy. Keep it that way.

### 4.4 Conform — the adapter that makes it a deliverable

In order, all inside Blender, all steps that already exist except where noted:

1. **Import** the GLB — weld-on-import already ships (Phase 8.5 R1).
2. **Scale** to the JobCard's L×W×H on the declared axis map. **If the source
   aspect ratio is off by more than the tolerance, non-uniform scaling will
   visibly smear texture and geometry — refuse and report, do not quietly
   distort.** That is the mattress case.
3. **Consolidate bodies** — voxel remesh, which GLM already measured
   collapsing 133 bodies to 1 watertight body. QuadriFlow does not consolidate.
4. **Re-quad / preserve quads** for the FBX (§3.3).
5. **Split** the packed metallic-roughness into the named PNGs.
6. **Bake normal + AO** HP→LP (§3.2).
7. **Package** via `package_delivery` — the nine-file contract, naming, size
   caps and gates already exist in `src/client/contract.py`.

Steps 1, 3, 6 and 7 are built. 2, 4 and 5 are the new work.

### 4.5 Band segmentation — do this only after 4.4 works

For band-structured goods the material regions are deterministic horizontal
slices. `templates/mattress.yaml` already carries the fractions (crown 0.28,
air_mesh 0.15, knit_top 0.09, velvet ×3 at 0.09, knit_bottom 0.11, base 0.10)
and `assets/textures/mattress/` already holds the five composed surfaces.

Select faces by height fraction, assign the matching material, use triplanar
at real-world `texture_size` so the neural UVs are irrelevant. `PBRMaterial`
already supports `texture_dir`, `texture_size` in metres, and `triplanar`.

**This only generalises to stratified products.** Do not present it as general
segmentation.

---

## 5. Where to stop

| Stop | Condition |
|---|---|
| **S1** | Aspect ratio off beyond tolerance → refuse, report, do not distort |
| **S2** | No explicit dimensions in the prompt → refuse (rule 9) |
| **S3** | GPU lock unobtainable → fail loudly, never proceed unsynchronised |
| **S4** | Gemini billing unconfirmed → no vision call; build against fixtures |

**Not a stop:** low view diversity. Warn loudly, record the score, run anyway.
That is the owner's explicit decision — see §3.1.

On any other blocker: write `BLOCKED — needs owner` in `PROGRESS.md` with the
specific question, then move to the next independent item and keep working.

## 6. Reporting discipline

Unchanged, and it earned its place tonight — I made three measurement errors
in one session and each was caught only by measuring again:

- A ratio, a score, or a histogram of a source file is never proof. Report the
  direct, absolute measurement of the thing in question.
- Every visual claim needs a number with a unit, plus the human threshold.
- **"I cannot verify this — you look"** is a good answer.
- Keep confessing your own bugs.

Specifically here: **do not report "textures working" because a GLB got
bigger.** Report which of the five maps exist, at what resolution, and which
are absent.

## 7. Rules that do not change

- **Never infer dimensions.** Neural input does not create an exception.
- `harness_script.py` never imports project code.
- One Blender process per op; `model_path` always explicit.
- No product nouns in the finishing layer.
- FBX from the quad-clean scene, never the triangulated GLB.
- No AI-generated textures — CC0 scans or owner-supplied only. (TRELLIS output
  is a **mesh source**, not a texture library.)
- The test baseline only goes up. It is 471.
- **Do not push.** The owner handles pushes.

## 8. The honest framing to keep in view

TRELLIS 2 is a **mesh source for objects the parametric templates cannot
express.** It is not a replacement for them.

For the mattress the template still wins outright — exact 80 × 60 × 12 in,
quad-clean, correct band structure, real carry handles, the real label crop.
Neural gave a square approximation with 34% metallic fabric.

Build this feature for the long tail. Do not let it eat the products that
already work.
