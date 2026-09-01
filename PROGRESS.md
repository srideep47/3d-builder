# PROGRESS — Client Delivery Pipeline (GLM_BRIEF.md work order)

> Running log for the T0–T5 task sequence (owner amendment 4, T1 review).
> One section per task: what landed (file paths), what was verified (command +
> actual output), what could not be verified, open assumptions, and the next
> action. **This is the resume point for a fresh session** — read GLM_BRIEF.md
> first (it is the work order), then this file, then re-check the current
> task's exit criteria before writing code. Context is finite: T3 is expected
> to span sessions.

---

## T0 — Clean the tree ✅ (2026-09-01)

**Landed** (3 commits, tree clean):
- `cf65857` — TripoSR GPU backend on Windows + img3d bake-off harness
  (`services/img3d_service/providers/tripo_sr.py`, `requirements-gpu.txt`,
  `scripts/setup-img3d-gpu.ps1`, `scripts/bakeoff_img3d.py`, `input/bakeoff/`)
- `80f0105` — local Qwen-VL plug point (`src/ai/vlm.py`, `tests/test_vlm.py`,
  `src/agent/loop.py`, `src/ai/aptos.py`, `config/ai.yaml`, `web/js/app.js`)
- `0e481ce` — planning docs (`CLIENT_PIPELINE_PLAN.md`, `GLM_BRIEF.md`,
  `GLM_KICKOFF_PROMPT.md`, `AGENTS.md`/`PROJECT_PLAN.md` M4 refresh)

**Verified:** `python -m pytest tests -q` → `68 passed in 65.70s` (run on the
exact content that was then committed); `git status` → `nothing to commit,
working tree clean`.

**Not verified:** —
**Assumptions:** none. Owner decisions: GLM_BRIEF/GLM_KICKOFF stay committed;
branch is NOT pushed (owner handles pushes).
**Next:** T1 (approved with 4 amendments — all implemented, see below).

---

## T1 — Compliance spine ✅ (2026-09-01)

**Landed:**
- `src/client/` (new package, product-noun-free by design — rule 11):
  - `units.py` — metres ↔ in/cm/mm/ft, boundary-only conversion (rule 8)
  - `contract.py` — **the single shared deliverable-set definition**
    (amendment 1: check_naming and check_file_sizes both consume
    `REQUIRED_DELIVERABLES`, cannot drift) + tier ceilings + decimal-MB caps
  - `job.py` — `JobCard` (pydantic) + `load_job()`. dims + explicit unit
    REQUIRED, loud failure, never defaulted (rule 9). `axis_map` field with
    documented default L→X, W→Y, H→Z (amendment 2). `dim_tolerance` job-level
    client tolerance, default ±0.01 in with the two-decimal-display rationale
    in code, separate from the internal ±1 mm build tolerance (amendment 3)
  - `gates.py` — `GateResult {gate, passed, expected, received, message}`,
    `MeshFacts`, six pure gates (`check_naming`, `check_ngons`,
    `check_polycount`, `check_dimensions`, `check_orientation`,
    `check_file_sizes`) + `run_all_gates`. Fail-closed without mesh facts.
    Orientation: floor/tabletop = rests on z=0 (±0.5 mm); wall/ceiling
    unobserved → refuse to guess. Naming checks exact case via directory
    listing (Windows FS is case-insensitive — path stats can't see `_ao.png`
    vs `_AO.png`)
- `src/blender/harness_script.py` — new ops `op_count_ngons`,
  `op_topology_report` (tri/quad/ngon, triangle-equivalent, loose geometry,
  non-manifold edges, world bounds), registered in DISPATCH
- `src/cli.py` — `validate <package_dir> --job job.yaml` (rich panel
  mirroring the client's validator, `--json` option, exit 1 on any failure)
- `tests/test_client_gates.py` (40 pure: unit layer, job card, every gate
  pass + independent break, transposed-L/W axis test, shared-contract
  regression guard), `tests/test_client_ops.py` (6 blender-marked)
- `AGENTS.md` — architecture + verification sections synced

**Verified (commands + actual output):**
- `python -m pytest tests -q` → `114 passed in 85.63s` (baseline 68 intact,
  +46 new)
- `python -m src.cli validate output/t1_smoke/packages/SMOKE0000001 --job
  output/t1_smoke/job.yaml` → all six gates PASS, `ALL GATES PASSED`,
  exit 0 (real package: box 12×34×5 in built by build_from_spec, GLB→FBX via
  convert op, smoke fixture lives under gitignored `output/t1_smoke/`)
- Same command after deleting `SMOKE0000001_AO.png` →
  `✗ Naming: Missing: SMOKE0000001_AO.png`, `1 GATE(S) FAILED`, exit 1

**Empirical findings (input for T2):**
1. **Blender 4.5 FBX export preserves n-gons.** An 8-segment cylinder
   (2 eight-gon caps) exported to FBX and re-imported still reports
   `ngon_count == 2` (`test_count_ngons_detects_ngons_in_fbx`). If Blender
   ever triangulates FBX output, that test is the early warning.
2. **GLB→FBX round trip preserves metric bounds** to Δ±0.000 in on the smoke
   box — well inside the ±0.01 in client tolerance. The full up-axis/rotation
   verification is still T2's job (this was one axis-aligned box).
3. **glTF triangulates everything** — a shipped GLB can never carry n-gons;
   the n-gon gate's meaningful target is the FBX (which does carry them).

**Not verified:**
- What the client's "polycount" counts (tris vs faces). We use
  triangle-equivalent (conservative: ≥ face count).
- Whether their MB caps are decimal or binary. We chose decimal
  (1 MB = 1,000,000 — stricter; local pass can never overshoot their cap).
- wall/ceiling orientation semantics (no client observation exists).

**Open assumptions (flagged until the client answers; brief §9):**
- Simple-tier polycount ceiling = 50,000 PROVISIONAL
  (`contract.TIER_TRI_CEILINGS`, one number to change)
- complex tier ceiling UNKNOWN → check_polycount fails closed on complex jobs
- USDZ size cap unknown → presence-only check
- `.spp` optionality unknown → never required by the naming gate
- Client dimension tolerance default ±0.01 in (owner-set; rationale in
  `src/client/job.py`; job-overridable via `dim_tolerance`)

**Next:** T2 — export & packaging. `op_export_fbx` (binary FBX 2020) +
`op_export_usdz` (verify empirically whether Blender 4.5 writes .usdz
directly — if not, add USD→USDZ conversion), FBX axis/up round-trip
verification beyond the axis-aligned box, `src/client/package.py` assembling
`output/packages/<JOB>/` + `qa_report.json` from every gate result, exit
criterion: a golden benchmark model exports as a complete package passing
every T1 gate.

---

## T2 — Export & packaging ✅ (2026-09-01)

**Landed:**
- `src/client/fbx_inspect.py` (new) — **the independent binary-FBX reader**
  (owner amendment 1: a Blender round trip is self-consistent even when the
  file is wrong for a third party, so this module IS the third party).
  Stdlib + numpy only, never Blender. Parses: 23-byte magic, FBX version,
  creator, GlobalSettings (UpAxis/FrontAxis/CoordAxis + signs +
  UnitScaleFactor), Geometry (Vertices + PolygonVertexIndex, bit-inverted
  last indices), Model nodes (LclTranslation/LclRotation/LclScaling),
  Connections (OO links). Resolves `world_vertices()`: geometry × Model
  chain (T·R·S, InheritType RrSs), normalised to metres via UnitScaleFactor
  (FBX native unit is the cm: metres = value × USF / 100). Also the
  chirality machinery: `box_corner_cloud`, `find_axis_mapping` (all 48
  signed permutations, uniform-scale + translation, reports determinant —
  a mirrored asset only matches det==−1), and `build_minimal_fbx` (writer
  for pure tests + stubs, round-trip is itself a parser test)
- `src/client/package.py` (new) — `package_delivery()`: exports FBX + USDZ
  via harness ops, independently parses the FBX, writes placeholder
  textures, runs all six gates, writes `qa_report.json`. Cross-checks
  harness topology vs independent parse (ngons / triangle-equivalent /
  world extents sorted multiset). LP/HP + textures are PLACEHOLDERS until
  T3 (amendment 3): logged + `placeholders` block + per-file flags, never
  silent. `usdz_structure_report()` (zip validity, stored entries, USD layer)
- `src/client/contract.py` — added `OPEN_QUESTIONS` (7 flagged items with
  handling, embedded into every qa_report)
- `src/blender/harness_script.py` — `op_export_fbx` (axis_up/axis_forward,
  apply_unit_scale, bake_space_transform; docstring warns Blender round
  trip is self-consistent, caller must verify via fbx_inspect) +
  `op_export_usdz` (direct `wm.usd_export` try, stored-zip fallback,
  reports method) + DISPATCH entries
- `src/cli.py` — `package <source_glb> --job --out-root` command (gates
  panel + Placeholder Warning panel + qa_report path, exit 1 on failure);
  extracted shared `_print_gate_results` / `_load_job_or_exit` helpers
- `input/fixtures/chiral_test.spec.json` (new) — **permanent chiral
  fixture** (amendment 2): base 20×12×9 in + boss 4×4×2 in at (+5, +3, 10)
  in, overall 20×12×11 in, all exact inch multiples. Distinct extents on
  all 3 axes + off-centre boss with no mirror symmetry → a handedness flip
  or L/W transposition cannot pass silently. T3/T4 reuse it
- `tests/test_fbx_inspect.py` (13 pure), `tests/test_package.py` (12 pure,
  stub runner writes genuine auditable artefacts), `tests/test_client_export.py`
  (9 blender-marked: pinned GlobalSettings, chirality, USDZ, golden e2e)
- `AGENTS.md` — verification section synced (148 tests, package command)

**Verified (commands + actual output):**
- `python -m pytest tests -q` → `148 passed in 69.14s` (T1 baseline 114
  intact, +34 new)
- `python -m pytest tests/test_client_export.py -q` → `9 passed` — the
  chiral/handedness + golden e2e suite against real Blender 4.5.13 output
- T2 exit criterion: coffee_table golden benchmark through the full
  `package` CLI → `ALL GATES PASSED`, `Placeholder Warning` panel shown,
  qa_report.json written with `all_passed: true`, `cross_checks.agree:
  true`; the assembled package then independently passes `validate`
  (exit 0, `ALL GATES PASSED`)

**FBX GlobalSettings ACTUALLY written by Blender 4.5.13 (binary FBX 7.4,
axis_up=Y, axis_forward=-Z) — recorded verbatim per amendment 1, pinned by
`test_fbx_global_settings_declared_values`:**
- `UpAxis = 1, UpAxisSign = 1` → **+Y up**
- `FrontAxis = 2, FrontAxisSign = 1` → **+Z front**
- `CoordAxis = 0, CoordAxisSign = 1` → **+X right**
- `UnitScaleFactor = 1.0` → FBX native unit (centimetres)
- version `7400`, creator contains "Blender"
- The mapping DISCOVERED from raw geometry (independent parse, no
  convention assumed) is `blender(x,y,z) = file(x, z, -y)`, det = +1 —
  and it agrees with every declared axis above (Blender +Z→file +Y up,
  Blender −Y→file +Z front). Chiral boss lands at +X +Y through this
  mapping in both the FBX read and the trimesh GLB cross-load

**Empirical findings (input for T3):**
1. **Blender's FBX export bakes the axis conversion into mesh data, writes
   positions as Model LclTranslation, values in centimetres.** Geometry
   local verts are already in the file's Y-up frame (x, z_b, −y_b) at ×100
   (cm, UnitScaleFactor 1.0); object positions live in Model nodes linked
   via Connections; no Model rotations (identity). An independent reader
   must resolve Models + Connections + cm→m — `world_vertices()` does.
2. **trimesh reads GLB in glTF's NATIVE Y-up space** (extents come back
   L, H, W — height on Y), not the pipeline's internal Z-up. What Babylon
   (glTF consumer) sees is this Y-up space. Our internal Z-up convention
   applies to the build/measure path only.
3. **USDZ: Blender 4.5.13 writes .usdz DIRECTLY** via `wm.usd_export`
   (method "direct", no zip fallback needed): single `.usdc` layer,
   entries stored (uncompressed) — `usdz_structure_report` confirms
   valid zip / not compressed / has layer.
4. **FBX exported FROM a GLB is fully triangulated** (glTF triangulates
   upstream) — 0 n-gons by construction. Blender FBX from a LIVE scene
   preserves quads AND n-gons (T1 finding 1). T3 decision needed: where
   does the deliverable FBX come from — live quad-clean scene (preserves
   topology, must guarantee 0 n-gons ourselves) vs GLB (triangles only,
   trivially n-gon-free but no quad topology)?
5. Blender re-import of the chiral FBX reports exact bounds
   [0.508, 0.3048, 0.2794] m, min z = 0, 24 triangles, 0 quads, 0 n-gons.

**Not verified:**
- What MetaZtech's validator ACTUALLY does — every verification here is a
  local proxy (independent binary parse + trimesh). Their Babylon viewer is
  Y-up for glTF and honours FBX GlobalSettings in principle, but we have no
  observation of their pipeline reading one of OUR files. First real
  delivery (T4) is the live test.
- LP/HP split and real textures (T3): placeholders, flagged everywhere.

**Open questions (NEW per owner instruction — recorded as questions, not
settled facts):**
- **polycount-semantics**: does the client's polycount gate count triangles,
  faces, or triangle-equivalent? We use triangle-equivalent (conservative).
  Affects tier ceilings' real headroom.
- **MB-basis**: are the client's MB caps decimal (1,000,000) or binary
  (1,048,576)? We enforce decimal (stricter). Affects files near a cap.
- (carried from T1) simple/complex ceilings, USDZ cap, .spp optionality —
  all still in `contract.OPEN_QUESTIONS` and every qa_report.

**Open assumptions (flagged until the client answers):**
- Y-up / −Z-forward is the right FBX convention for their viewer (Babylon
  default + FBX-standard declaration; recorded per-file in qa_report)
- `axis_map` default L→X, W→Y, H→Z (amendment 2, unchanged)
- chiral fixture's uniform split-vertex multiplicity (3 per corner) — the
  mapping mean-translation reconstruction relies on it; residual check
  would catch a violation

**Next:** T3 — UV + HP/LP bake pipeline: `op_uv_unwrap` (smart project +
texel density), `op_bake_maps` (Normal OpenGL / AO / Curvature, 4096
default), `op_decimate_to_budget`, per-part detail block in ObjectSpec v2
(rule 7: enum/prompt/dispatch sync), zero-n-gon strategy (build quad-clean
→ verify → triangulate only as last resort, loud logging), decide the FBX
source (finding 4 above).

**OWNER DECISION (T2 review, recorded 2026-09-01):** the deliverable FBX
is exported from the **LIVE QUAD-CLEAN SCENE**, not the triangulated GLB.
Rationale (owner): their validator checks n-gon count at all, which only
makes sense if they expect a non-triangulated mesh — on a triangulated
mesh that gate is trivially zero and pointless; triangulating also doubles
polycount against a tier ceiling, and a human QA judges artist-quality
topology. N-gon correctness is ours, which is what T3's "quad-clean by
construction" already requires. Implementation: the finishing pipeline
saves the quad-clean scene as an intermediate .blend; `export_fbx` loads
the .blend (preserves quads) instead of importing the GLB (triangulates).
T2's GLB-sourced FBX path remains for the legacy placeholder flow but the
T3 pipeline (`finish_delivery`) uses the live scene.

**MAYA00053153 dimensions: NOT SUPPLIED** (owner message carried the blank
placeholder). T4 therefore runs the placeholder-dimension refusal path
(`dims_placeholder: true` in the job card; `package_delivery` refuses to
emit a deliverable package; loud logging) and ends with
"T4 BLOCKED — owner must supply dimensions". A standard queen size is
never inferred (rule 9).

---

## T3 — UV + HP/LP bake pipeline ✅ (2026-09-01)

**Landed:**
- `src/blender/harness_script.py` — three new ops + support machinery:
  - `prepare_delivery_scene`: build → n-gon verify (raise unless zero;
    triangulate only with `allow_triangulate`, recorded loudly) → smart-project
    UVs per object → **per-ISLAND atlas pack** (uv_area ∝ world area per
    island, shelf-packed, uniform overflow shrink) → mechanical UV
    diagnostics → saves the live quad-clean `.blend` (FBX source, owner
    decision above)
  - `bake_maps`: AO **self-scene** FIRST (cross-part contact shadows; HP
    shells would occlude — empirical), then per-part HP shells (bevel +
    subsurf + micro-lift, or `hp_mode="script"` for caller-authored HPs),
    then tangent NORMAL selected-to-active **through a custom cage**, then
    BaseColor/Roughness/Metallic via the Emission-channel trick; wires the
    baked set into the delivery materials; exports the HP GLB; deletes
    HPs+**cages**; re-saves the .blend. `detail` param gives per-part
    bevel/subsurf/displacement overrides (PartSpec.detail)
  - `decimate_to_budget`: iterative collapse to the tier ceiling, fail-loud
  - `_apply_pattern_displacement` + `_pattern_value`: deterministic
    displacement patterns (grid_diamond, grid_square, bumps, waves, noise) —
    pure math, same input ⇒ same output across processes
  - `_uv_islands_report` / `_uv_diagnostics`: islands via UV-continuity
    union-find; **exact rasterized island-overlap test** (texel claims, not
    bbox intersection); texel-density min/max/ratio per island
- `src/spec/schema.py` + `src/spec/resolver.py` — `DetailSpec` +
  `DisplacementSpec` (detail shapes the HP only; LP dimensions can never
  move); resolver converts spec units → metres (verified: mm 1.5 →
  0.0015 m) and passes `restrict` through
- `src/agent/prompts.py` — DETAIL section added to the analyst prompt
  (rule 7: shape enum, prompt, and harness stay in sync)
- `src/client/package.py` — `finish_delivery()`: the full T3 chain
  (prepare → bake → decimate → FBX from the LIVE QUAD-CLEAN SCENE → USDZ
  from the LP GLB → real 5-map texture set → gates → qa_report) with the
  `finish` evidence section (bake stats, UV diagnostics, HP/LP counts,
  review renders). `package_delivery()` (T2 placeholder flow) refactored
  onto the shared `_assemble_and_audit` core — behaviour unchanged, tests
  unchanged
- `src/cli.py` — `package --spec <spec.json>` runs `finish_delivery`;
  prints the finish summary panel
- Tests: `tests/test_delivery_finish.py` (9 blender-marked) +
  `tests/test_spec_detail.py` (7 pure). **Suite: 164 passed** (baseline 148
  + 16, nothing skipped except blender-without-Blender)

**Mechanical evidence per map (the operator is text-only — numbers, not
eyeballs; all pinned in tests):**
- **Normal**: the ramp proof — flat LP (UVs u=x+0.5, v=y+0.5, so +V is +Y
  world) under a bent HP (z = 0.6·y² for y>0, normal tilts −Y). Measured G
  matches the analytic prediction `0.5 − 0.5·s/√(1+s²)` to **1 LSB** at
  y=0.15/0.30/0.45 (e.g. y=0.45: predicted 67/255, measured 68/255). G < 0.5
  for a −Y tilt with +V = +Y ⇒ **Blender's default `normal_g="POS_Y"` IS
  the OpenGL convention (glTF bitangent +V)** — the delivered default stays
  POS, no flip. R pinned at 128 (no X tilt), flat region pinned at
  (128,128,255). Distribution-level: std > 0.02, blue-dominant > 0.9,
  coverage 1.0 (chiral: mean (0.503, 0.503, 0.982), edge gradients to
  R/G 0.16/0.85 ≈ the 45° bevel prediction)
- **AO**: cavity proof — plane with a box standing on it; texel under the
  box bakes < 0.3, exposed texel > 0.6, contrast > 0.4. Range spans
  (min 0 / max 1). Coffee showcase: coverage 0.678, min 0, max 1
- **UV**: zero overlapping islands by **exact rasterization** (bbox
  intersection is only a prefilter — the coffee atlas has concave islands
  whose bboxes intersect with zero real overlap), all islands in 0-1,
  texel-density ratio **1.00006** on the coffee table (122 islands)
- **FBX**: from the live quad scene — independent parse sees polygon sizes
  {4: 12} (pure quads, zero triangles) and extents 20 × 12 × 11 in exactly

**Empirical findings banked (do not regress):**
1. **Blender's selected-to-active bake casts rays INWARD.**
   `bake.cc: calc_point_from_barycentric_extrusion` ends with
   `negate_v3(dir)` — without a cage, every ray leaves the LP surface along
   **−N**. Consequences (all reproduced with tilted-plane probes): (a) any
   HP geometry above the LP is INVISIBLE to the bake; (b) an enclosing HP
   shell bakes its **far** side with the hit normal flipped to face the
   ray (our chiral bake "worked" this way — symmetric bevels made it look
   right, but an asymmetric HP e.g. top-only quilt would bake the WRONG
   side's detail); (c) a ray miss writes the LP's own flat normal
   (**neutral 128,128,255 — NOT black**), so a dead bake looks "clean".
   Fix: a **custom cage** — `calc_point_from_barycentric_cage` sets ray
   dir = low_point − cage_point, so a cage shrunk slightly INSIDE the LP
   shoots outward rays that hit the NEAR side of the HP shell. The bake op
   builds cages automatically (vertex-normal inset; must exceed the bevel
   shell's inward dip near edges, hence inset ≈ 1.2·bevel + lift), creates
   them only AFTER the AO pass (a visible cage would occlude), and deletes
   them before the scene is re-saved
2. **Blender resolves RELATIVE image paths against the .blend file, not
   the process CWD.** `img.save()` with a relative out_dir silently writes
   nowhere (no error, no file — the chiral CLI smoke caught it because
   finish_delivery refuses to package a missing map). All bake outputs and
   package paths are `abspath`/`resolve`d at every layer now
3. **Bakes need an ACTIVE TexImage node** in every baked material — with
   none, Blender silently bakes nothing (kept from earlier finding;
   `_with_active_image` owns this)
4. **Tangent-space selected-to-active with a shared-UV HP cancels out**:
   a bent sheet whose UVs parametrize its own geometry bakes neutral even
   when rays connect — this is a red herring once the cage fix is in (the
   real issue was the inward rays), but explains why early ramp probes
   looked "flat yet covered"
5. **Smart-project stretches unevenly within an object** (bevel strips vs
   large faces): per-OBJECT atlas scaling left the coffee tabletop at a
   1.46× island texel-density ratio. Per-ISLAND scaling (seam-safe by
   definition) converges it to 1.00006
6. AO selected-to-active from HP shells covers only ~19% of texels (ray
   correspondence unreliable for lifted closed shells) — AO stays
   self-scene per object with all parts render-visible (contact shadows
   included), baked BEFORE any HP exists
7. `bpy.ops.object.bake` normal_r/g/b enums want POS_X/NEG_X-style axis
   names (4.5); semantic normal_g "POS"/"NEG" maps to POS_Y/NEG_Y

**Verified:** `python -m pytest tests -q` → **164 passed** (94.8s, full
suite including blender-marked). Chiral CLI smoke: `package --spec
input/fixtures/chiral_test.spec.json --job <20/12/11 in>` → ALL GATES
PASSED, zero placeholders. Coffee showcase (durable, for owner review):
`output/packages/COFFEE0001/` (all six gates green, qa_report with the
finish evidence section) + **review renders awaiting owner review** at
`output/finish/COFFEE0001/review/COFFEE0001_{front,side,top,iso}.png`
(LP 236 tri-eq, HP 15360 tri-eq, textures 1024²).

**Not verified:** perceived visual quality of the baked maps (operator is
text-only — that is exactly what the review renders are for; nothing in
the map set is hand-tuned yet). The USDZ still exports from the LP GLB
(triangulated; no quad requirement there).

## T4 — Reference implementation (mattress MAYA00053153) ✅ pipeline complete / ⛔ DELIVERY BLOCKED — owner must supply dimensions

> **T4 BLOCKED — owner must supply dimensions.** The dimension line for
> MAYA00053153 arrived blank, so the job card carries the dashboard's own
> stand-in numbers (12 × 12 × 65 IN, `dims_placeholder: true`). The full
> chain runs and produces structural review renders, but **NO deliverable
> package is emitted** — dimensions are never inferred and a standard
> queen size is never guessed (rule 9). Evidence:
> `output/blocked/MAYA00053153/qa_report.json`. **To unblock:** put the
> real L × W × H (explicit unit) into `input/jobs/MAYA00053153.yaml`,
> delete the `dims_placeholder` line, re-run
> `python -m src.cli package --job input/jobs/MAYA00053153.yaml --template templates/mattress.yaml`.
> Everything else in T4 is DONE and tested (222 tests).

**Landed:**
- **Template layer** `src/spec/template.py` — the only place product
  knowledge lives (rule 11): `TemplateSpec` (stacked bands + optional
  domed crown + quilt + perimeter tape sweeps + side decal + texture
  recipes, all PROPORTIONS of the job's owner-supplied L×W×H);
  `footprint_outline` (superellipse, shared by bands/dome/tape paths so
  junctions are flush); self-contained `_DOME_SCRIPT` (radial rings,
  quads + pole/cap fans, runs inside Blender via `method: custom_script`);
  `compile_spec(template, job)` → plain ObjectSpec + warnings. Validation:
  band fractions must sum to 1.0, unique names, material refs must exist,
  tape boundaries must name a band
- **The geometry contract** (load-bearing, pinned in tests): band bodies
  are INSET by the largest tape protrusion (a_body = a − p_max) so every
  tape's outer face lands exactly on nominal L/W → overall bounds are
  exactly the job card's L×W×H at ANY template scale; the decal is proud
  of the band wall by 0.3·p_max but recessed behind the tape plane so it
  can never widen the silhouette; decal width clamped to ¼ of the wall
  span (warning, never silent), corner-region + tape-z-overlap warnings;
  quilt amplitude references one quilt CELL (footprint/cells — scale-
  invariant puff depth), `restrict: up` so LP bounds never move;
  protrusion ≥ half-footprint raises at compile time
- `templates/mattress.yaml` — §5.2 structure: 8 bands (crown 0.28 /
  air_mesh 0.17 / velvet-knit border stack / base 0.10), 3 tape edges,
  grid_diamond quilt, front-left portrait decal (aspect 0.7, clears
  tape_2/3 z-spans), carry handles declared but `enabled: false` (§9.4)
- **Texture composition** `src/textures/` — `patterns.py` (pure-numpy
  tileable generators: oval_holes, herringbone, chevron; PNG IO row 0 =
  v = 0), `compose.py` (CC0 scans + procedural layers → canonical
  albedo/roughness/height per surface, metre-params → per-tile integers,
  provenance manifest.json, AO-fallback when a scan's displacement map is
  a flat placeholder), `decal.py` (`generate_placeholder_decal` —
  deterministic magenta-bordered stand-in). Fetcher
  `scripts/fetch_cc0_textures.py` (Poly Haven API, licence recorded),
  generator `scripts/gen_template_textures.py` (+ `--placeholder-decal`)
- **CC0 sources used** (structure only, tinted to §5.2 colours; no
  marketplace, no AI-generated): Poly Haven `knitted_fleece` (knit_white,
  tile 2.68 m), `velour_velvet` (velvet_charcoal, tile 2.79 m);
  mesh_white/tape_black/base_dark are procedural. Both scans' disp maps
  were flat 1.0 → height from AO (recorded in each manifest.json)
- **Harness** (`src/blender/harness_script.py`): `bevel_mode="OBJECT"`
  fix (see findings), `custom_script` build dispatch, name-keyed material
  cache (kills .001/.002 suffix leakage), triplanar Mapping
  `Location=(0.5,0.5,0.5)` (see findings), `texture_size` plumbed through
  `PBRMaterial` → one exact tile across the decal patch
- **Refusal machinery** (owner's overnight order): `JobCard.dims_placeholder`
  + loud `load_job` banner; `package_delivery` refuses at entry;
  `finish_delivery` §3b — chain runs (build → atlas → bake → decimate →
  4 review renders), writes `output/blocked/<JOB>/qa_report.json`
  (refused / refusal_reason / placeholder_dims with source note /
  gates: None / finish evidence incl. detail_parts / unblock
  instructions), logs REFUSED ×3, raises `PlaceholderDimensionsError`;
  CLI catches it → BLOCKED panel → **exit code 2**
- Decal label `input/decals/MAYA00053153/albedo.png` is the procedural
  PLACEHOLDER — see "Not verified" below

**The e2e run (placeholder dims, structural review only):**
`python -m src.cli package --job ... --template templates/mattress.yaml`
→ compiled 12 parts (8 bands + 3 tapes + decal), atlas pack_scale 0.3164,
**2118 islands, 0 overlaps (exact rasterization), texel-density ratio
1.0004**; 5-map bake at 1024² (basecolor std 0.35, roughness std 0.18, AO
std 0.44, metallic flat 0; quilt displacement reached the normal map via
the detail-normal whiteout pass: texels_where_maps_differed 0.96, mean
deviation 0.052); LP **3468 tri-eq** (budget 50000, not decimated) / HP
201600 tri-eq; 4 review renders at
`output/finish/MAYA00053153/review/MAYA00053153_{front,side,top,iso}.png`;
then **REFUSED** (exit 2) with the blocked report. Review renders are
valid owner-review output: band order, materials, tape and label
placement are dimension-independent (fractions of H); silhouette review
needs the real dims.

**Empirical findings banked (do not regress):**
1. **`curve.bevel_object` is IGNORED unless `curve.bevel_mode = "OBJECT"`**
   (Blender 2.90+; harness `_build_sweep`). Symptom: convert() yields a
   bare polyline with no faces and glTF exports the tapes as EMPTY
   transform-only nodes — "the object exists" proves nothing, faces do.
   Pinned: tapes are real closed tubes, 0 boundary/non-manifold edges
2. **Triplanar BOX mapping anchors its tile grid at the object-local
   origin.** With Object tex-coords spanning [−tile/2, +tile/2] the
   sampled u wraps (one-tile textures show TWICE, mirrored). The Mapping
   node needs `Location = (0.5, 0.5, 0.5)`. With the offset, a
   −Y-normal face renders a one-tile-across label UPRIGHT and unmirrored
   — NCC probe: identity **+0.9924** vs flipud −0.071, fliplr −0.071,
   both −0.557 (pinned in `tests/test_template_harness.py`)
3. **The glTF exporter converts object-coordinate/BOX-projection
   materials to UV-mapped ones** (Mapping+UVMap nodes, possible texture
   transform) — a render from a pre-bake GLB does NOT show the triplanar
   result. The bake path (scene.blend) is the real path; the label
   orientation must be probed on the live scene, not a GLB round trip
4. **glTF triangulates and splits vertices per normal/UV attribute**: the
   192-quad tape exports as 384 triangles / 416 verts. NOT a bug — the
   FBX carries the live-scene quads (owner decision). Consequence: n-gon
   gates are only meaningful on .blend/FBX, never on GLB
5. **trimesh reads glTF in Y-up**: Blender bakes the Z-up→Y-up conversion
   into the root node tree shared by ALL objects, so a single object
   looking "rotated" in trimesh world coords is usually the convention,
   not corruption. All screen/world math must be done Blender-side
   (`matrix_world`); and the ortho screen projection is
   `row = 1024·(0.5 + (z − cz)/S)` from the top — the (z−cz)/S term is a
   fraction of the FULL span (a 512-vs-1024 factor error here halved the
   mapped span and manufactured a phantom "displaced label")
6. **PNG IO convention**: `save_png` writes row 0 = v = 0 (bottom);
   `load_rgb`/`load_gray` flip back — roundtrip identity. Compose layers
   preserve scan orientation only with BOTH halves of the pair
7. **Material name clashes create .001/.002 datablocks**: per-part
   creation of same-named materials leaks suffix variants into the
   delivery scene — a name-keyed cache threaded through `apply_material`
   creates each material once
8. **Poly Haven fabric displacement maps can be constant-1.0
   placeholders** (knitted_fleece, velour_velvet): fall back to the AO
   map for height and record the substitution in manifest.json — never
   ship a silently-flat bump
9. **Tileability is provable discretely**: `np.roll` by an exact period
   must be identity (axis 0 = u with `indexing="ij"`); bbox-style
   "looks shifted" checks are meaningless
10. **The dome script + base-mode placement are idempotent**: the script
    does `obj.location.z += Z0` and `_place_part`'s base mode shifts by a
    delta from the CURRENT bbox → no double placement (crown z-span
    pinned exact in tests)

**Verified:** `python -m pytest tests -q` → **222 passed** (97.6s; T3's
164 + 58 new: test_textures 22, test_template 21, test_delivery_refusal
6, test_template_harness 9 — blender-marked ones auto-skip without
Blender). New coverage: pattern generators (analytic coverage + period
invariance), compose (manifest/AO-fallback/layer emboss), template
validation + compile math (inset contract, decal clamps/warnings, quilt
cell reference), refusal path (flag, entry refusal, chain-runs-then-
refuses with a mock runner, CLI exit 2), harness geometry (0 n-gons,
closed solids, dome face counts, tape outer faces EXACTLY nominal,
overall bounds exactly 12×12×65 IN, decal recessed, label orientation
NCC), and the CONTROL: a real-dims minimal template through
`finish_delivery` emits a full package with **all gates green**
(`test_finish_delivery_real_dims_emits_package`) — same entry point,
flag flipped, opposite outcome.

**Not verified / flagged for the owner:**
- **Dimensions** — the blocker above. Everything downstream of the job
  card is done; nothing about the mattress geometry depends on which
  dims arrive
- **The NISIEN label decal is a procedural placeholder** (magenta border,
  deterministic). Mechanical limits of blind sourcing (rule: the operator
  is text-only): I cannot produce the real photo crop from §5.3. Replace
  `input/decals/MAYA00053153/albedo.png` with the photo crop (portrait,
  ~0.7 aspect) — the compile picks it up automatically (aspect/height
  fractions stay in the template)
- **Band proportions are a FIRST PASS** from the §5.2 reading — review
  the four renders and tune `height_fraction`s in
  `templates/mattress.yaml` (they are owner-tunable numbers, no code
  change needed)
- Carry handles remain unmodelled pending §9.4 (declared `enabled:
  false` in the template's `features:` block)

## T5 — Generalise ✅ (2026-09-01)

**Landed:**
- **`src/ai/vlm.py` behind a `VisionProvider` ABC** — the two integration
  points (reference description for the text-only analyst, advisory
  render-vs-reference verdict) are CONCRETE on the ABC and speak only
  `chat_vision`, so every wire protocol gets them for free:
  - `OpenAICompatibleVisionProvider` (historical alias `LocalVLMClient`):
    the local Qwen2.5-VL/vLLM path, unchanged wire behaviour
  - `GeminiVisionProvider`: Google Generative Language **v1beta** — a
    deliberately SECOND provider class (the API is not OpenAI-shaped):
    `POST {base}/v1beta/models/<model>:generateContent`, auth via the
    **`x-goog-api-key` header** (never Bearer), body
    `contents:[{role, parts:[{text}|{inline_data:{mime_type,data}}]}]` +
    `systemInstruction` + `generationConfig{maxOutputTokens,temperature}`;
    response parsed from `candidates[0].content.parts[*].text`
  - Selection: `config/ai.yaml` → `vision.vlm.provider: local|gemini`
    (factory `get_vision_provider()`, never raises; broken config →
    stderr warning + no vision). The agent loop now calls the factory
    (`get_local_vlm` kept as a back-compat alias)
  - **Key handling**: the API key lives ONLY in the environment —
    `api_key_env` (default `THREED_VLM_API_KEY`), with `GEMINI_API_KEY`
    as fallback (same value in the owner's setup). Never in config files,
    never in code, never in the repo
  - **Pinned models only**: `-latest` aliases raise at construction (a
    floating model can never pass review); the Gemini default pin is
    `gemini-3.6-flash`
- **Web UI Delivery view** (`python -m src.cli ui` → nav "Delivery"):
  - **Job intake form** → `POST /api/jobs` writes a validated job card to
    `input/jobs/<code>.yaml` (JobCard/JobDims pydantic validation → 400
    with the reason; existing codes never overwritten → 409). Rule 9 is
    structural: dims + explicit unit are REQUIRED; the "dims are
    stand-ins" checkbox sets `dims_placeholder: true` with a red warning
    and a confirm() — the card carries the rule-9 comment header
  - **Compliance panel** mirroring the client validator:
    `GET /api/packages` (packages + blocked, verdict chips),
    `GET /api/packages/<JOB>` (full qa_report), and
    `POST /api/packages/<JOB>/validate` — a LIVE re-run of all six gates
    (fresh Blender process for mesh facts; placeholder-dims jobs are
    REFUSED with 409, rule 9)
  - Supporting endpoints: `GET /api/templates`, `GET /api/jobs`
  - `input/jobs/COFFEE0001.yaml` made durable (values copied from the
    package's own audit record) so the showcase is re-validatable from
    the panel
- Tests: `tests/test_vlm.py` (12: the 4 original + 8 new — ABC contract,
  Gemini wire format against a mock v1beta server that mirrors the REAL
  response shape, key env/fallback, `-latest` rejection, dead-endpoint
  fail-soft, shared integration points over the Gemini wire, factory
  selection) and `tests/test_webapp_api.py` (+8: templates/jobs/packages
  endpoints, intake validation never infers, no-overwrite, refusal paths,
  live-validate wiring with gates mocked)

**The ONE live Gemini smoke call** (single `:generateContent` round trip,
image included to validate `inline_data`; key read from the environment,
redacted below):
- Request: `POST https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent`,
  headers `{x-goog-api-key: AQ.…, Content-Type: application/json}`, body
  keys `contents`, `generationConfig`, `systemInstruction` — all accepted
  (200). The 64×64 red test PNG was read correctly (reply
  `{"color": "red", "count_corners": 4}`; usage shows IMAGE 1089 tokens)
- **Response shape (recorded for future parsing work):**
  ```
  candidates[0].content.parts[0].text        # the content (join across parts)
  candidates[0].content.parts[0].thoughtSignature   # opaque ~468-char thinking signature
  candidates[0].content.role = "model"
  candidates[0].finishReason = "STOP"        # other: MAX_TOKENS
  candidates[0].index = 0
  usageMetadata: promptTokenCount, candidatesTokenCount, totalTokenCount,
    promptTokensDetails[{modality: IMAGE|TEXT, tokenCount}],
    thoughtsTokenCount, serviceTier
  modelVersion: "gemini-3.6-flash", responseId
  ```

**Empirical findings banked (do not regress):**
1. **`gemini-2.5-flash` is retired for new API keys** (HTTP 404: "no
   longer available to new users… use models/gemini-3.6-flash") — it
   still appears in `GET /v1beta/models` but `:generateContent` refuses
   it. Pin `gemini-3.6-flash`. A models-list 200 proves the key, NOT the
   model's availability
2. **v1beta is not OpenAI-shaped in either direction**: auth is the
   `x-goog-api-key` header (no Authorization), images are
   `inline_data{mime_type, data}` (not `image_url` data URLs), the
   system prompt is `systemInstruction{parts}` (not a system message),
   and generation params go in `generationConfig{maxOutputTokens}` (not
   `max_tokens`)
3. **Gemini 3.6-flash is a thinking model** (`thoughtsTokenCount` 66 on a
   trivial call, `thoughtSignature` on parts): budget `maxOutputTokens`
   generously or `finishReason: MAX_TOKENS` can arrive with empty text —
   the same lesson as GLM-5.3's reasoning tokens

**Verified:** `python -m pytest tests -q` → **238 passed** (full suite
incl. blender-marked; T4's 222 + 16 new). Web UI browser pass (repo
convention, evidence in `docs/gui-screenshots/08-delivery-intake-refused.png`
+ `09-delivery-compliance-gates.png`): intake form creates a job card and
lists it (demo card removed afterwards); the blocked MAYA00053153 package
renders the REFUSED box with the finish evidence and unblock
instructions; COFFEE0001 renders the six-gate table; the panel's
"Re-run validator" button performed a live re-validation — all six gates
PASS with fresh Blender mesh facts.

**Not verified / flagged for the owner:**
- The Gemini provider has had exactly ONE live call (the smoke above, by
  order). The shared `describe_reference_images`/`visual_verdict`
  integration points are mock-tested on the Gemini wire but have not run
  against real reference photos — that happens on the first real client
  job with `vision.vlm.provider: gemini`
- The local Qwen-VL path remains unexercised on this machine (no vLLM
  server running); its wire behaviour is unchanged and still covered by
  the mock-server tests
- The health dot in the web UI sidebar reflects the GLM endpoint's
  vision support, not the VLM provider (pre-existing behaviour, left
  alone)

