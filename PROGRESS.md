# PROGRESS — Client Delivery Pipeline (GLM_BRIEF.md work order)

> Running log for the T0–T5 task sequence (owner amendment 4, T1 review).
> One section per task: what landed (file paths), what was verified (command +
> actual output), what could not be verified, open assumptions, and the next
> action. **This is the resume point for a fresh session** — read GLM_BRIEF.md
> first (it is the work order), then this file, then re-check the current
> task's exit criteria before writing code. Context is finite: T3 is expected
> to span sessions.

## ⛔ OPEN OWNER QUESTIONS (HANDOFF_GLM §7 — keep visible until answered)

1. **MAYA00053153 real dimensions** — L × W × H with explicit unit. Blocks
   every deliverable package (rule 9; the refusal machinery is active and
   tested).
2. Is `.spp` mandatory, or are baked PNG sets acceptable?
3. Simple-tier polycount ceiling (Medium = 200,000; we use 50k provisional).
4. FBX axis/unit convention the client's validator expects.
5. Polycount semantics: triangles or faces? (we use triangle-equivalent,
   the conservative reading)
6. File-size caps: decimal MB or binary MiB? (we enforce decimal, stricter)
7. ~~Are the vertical side straps carry handles, and should they be
   modelled?~~ **CLOSED round 3** (owner's eyes + photo 9.28.35): yes, they
   are carry handles and they ARE modelled (2 per long side, quarter
   points). The owner independently confirmed seeing them in the round-3
   render — the Gemini "missing straps" read was a VLM miss.

~~Replace the NISIEN label placeholder~~ **DONE round 4**:
`input/decals/MAYA00053153/albedo.png` is now the real photo crop from
9.28.22 (see `output/make_decal_crop.py`); the synthetic stand-in and its
magenta-noise purple cast are gone.

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
> queen size is never guessed (rule 9). Evidence (two different places —
> do not conflate them): refusal report at
> `output/blocked/MAYA00053153/qa_report.json`; review renders at
> `output/finish/MAYA00053153/review/MAYA00053153_{front,side,top,iso}.png`
> (HANDOFF_GLM correction, 2026-09-01: the renders are NOT in
> `output/blocked/`). **To unblock:** put the
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


---

## Session 2026-09-01 (afternoon, post-machine-move) — defect fixes verified via the Gemini visual gate ✅

> HANDOFF_GLM.md order of work executed on Scout (Ryzen 5 4600H, GTX 1650
> Ti 4 GB, CPU-bound Blender). Branch: `wip/defect-fixes` (the in-flight
> snapshot survived on a branch + origin; the working-tree copy was lost
> in the move — nothing needed redoing). All work committed there; main
> untouched at `75e847f`; **nothing pushed** (owner handles pushes).

**Environment confirmed (step 1):**
- `python -m src.cli health` → green (Blender 4.5.13 portable found, AI
  endpoint reachable). `THREED_VLM_API_KEY` present at Windows User scope.
- `python -m pytest tests -q` on main → **238 passed** (228 s — ~2× the
  old machine's wall time; CPU-bound, expected on Scout).

**In-flight defect fixes recovered and completed (step 2):**
- The 7-file WIP survived as commit `b874a6a` on `wip/defect-fixes`
  ("UNTESTED SNAPSHOT"). First full suite against it: 239 passed,
  1 failed — `test_chiral_uv_diagnostics` expected 6 islands, got 12.
- **The WIP's test expectation was wrong, not the code**: the chiral
  fixture is TWO boxes × six single-quad sides — no coplanar adjacency
  exists in it, so 12 islands (one per face) is the correct count. The
  "2 coplanar quads merge" premise described a fixture that doesn't exist.
  Restored the expectation to 12 with a corrected comment; the REAL merge
  regression is now pinned where merging happens — the mattress.
- **Empirical probe** (`scripts/probe_uv_islands.py`, keeper):
  `prepare_delivery_scene` on the compiled mattress → 2118 faces collapse
  to **84 islands** at MAYA dims (**80** at queen), pack_scale **0.75**
  (was 0.3164), texel-density ratio 1.0000, 0 overlaps. The vertex-keyed
  `_uv_face_groups` matching WORKS; the old one-corner-per-face match
  reported 2118 one-face islands.
- New/updated tests: `test_uv_contiguous_faces_merge_into_few_islands`
  (mattress, 84 islands pinned), chiral expectation 12, refusal stub
  updated to the real observed values (80 / 0.75).
- **Suite: 241 passed** (baseline 238 + tape-scale pin + tape-section pin
  + island-merge pin). Committed `cfd937a`.

**Gemini visual gate switched on and CALIBRATED (step 3):**
- `config/ai.yaml`: `vision.vlm.provider: gemini`, model pinned
  `gemini-3.6-flash` (local Qwen-VL path stays available for Forge).
  Provider constructs, key validates against the live endpoint.
- **The 12 client reference photos were found in `Temp/`** (repo root;
  the owner dropped them in during this session — the old
  `D:\Work\Temp\Test Images` path does not exist on Scout). Describe-mode
  over all 12 returns a full structured reading that agrees with
  GLM_BRIEF §5 (pillowtop quilt, mesh band, velvet band stack, black
  perimeter piping, NISIEN label, vertical straps). Durable copy made at
  `input/references/MAYA00053153/`; both job cards' `reference_dir`
  updated to point there.
- New evidence collector `scripts/run_visual_gate.py` (inspection mode /
  describe mode / full verdict with `--refs`; prints raw responses
  verbatim; advisory only — never gates).

**DEFECT 1 + DEFECT 2 fixed and proven with before/after gate evidence
(step 4)** — same prompt, same view set, only the code changed:

| | BEFORE (main `75e847f`, renders preserved at `output/finish/MAYA00053153/review_BEFORE_defects/`) | AFTER (`cfd937a`) |
|---|---|---|
| tape_edges | "protrude heavily as thick collar flanges… roughly 4-5% of total height" | "thin black binding strips hug the side surface snugly… roughly 1% of total height" |
| band_textures | "broken, chaotic black-and-white pixelated blotches and static" | "coherent fabrics… white knit patterns, mesh, dark charcoal velvet stripes" |
| other_issues | UV corruption, pinched apex, floating rings | none |
| verdict | **FAIL** | **PASS** |

(Gemini's fractions are accurate: old thickness was exactly 4.5% of H,
new is 0.83%.) Queen-proportioned inspection also PASS. All three chain
runs REFUSED as designed (placeholder dims — exit 2, no package emitted).

**Advisory render-vs-reference verdict (queen renders vs the 12 refs):**
score **4/10**, `matches_reference: false` — "captures the general side
band layout" with three concrete issues, recorded verbatim:
1. top lacks distinct tufting/pillowtop depth — flat, blurry wavy
   textures at the 1K iteration bake
2. texture distortion / black artifacting along tape edges and corners
3. side pattern + branding label lack crispness
Analysis (not yet fixed — owner review + next iteration): the tape strip
is ~13.7 mm on a ~2 m product, so at 1K the area-proportional atlas gives
it only a handful of texel rows (thin-feature texel starvation; the 4K
delivery bake is 4× better); the quilt reads through the normal map only
(detail-normal blend, 1K); the label is the known procedural placeholder.
The gate is advisory and per GLM_BRIEF §8 does not judge quilt pitch —
these are quality-tuning items, not the gross defects above.

**Other findings this session:**
- `describe_reference_images` truncated mid-sentence at max_tokens 1600
  on the real 12-photo set (gemini-3.6-flash thinking budget) — budgets
  bumped 1600→4096 (describe) and 1200→3072 (verdict) in `src/ai/vlm.py`.
- Chain wall time on Scout: ~2-4 min per full mattress run at 1024²
  (build → atlas → 5-map bake → decimate → FBX → 4 renders) — 1K
  iteration bakes are comfortably cheap on CPU.

**Not verified / still blocked:**
- All seven owner questions at the top of this file (dims first).
- No MAYA00053153 deliverable package emitted (rule 9 — correct).
- The queen verdict issues above are recorded, not fixed.

**Next:** owner supplies dimensions → replace in
`input/jobs/MAYA00053153.yaml`, delete `dims_placeholder`, re-run
`python -m src.cli package --job input/jobs/MAYA00053153.yaml --template
templates/mattress.yaml` (4K bake for the real delivery); owner reviews
the four queen renders at `output/finish/TEST-QUEEN/review/` and tunes
`height_fraction`s in `templates/mattress.yaml` if the band proportions
need adjusting (owner-tunable numbers, no code change).

---

## Session log — 2026-09-01 (round 2: reviewer follow-ups on the quilt)

Reviewer follow-ups answered in order. **No quilt-path code was changed**
(report-first, per instruction); the only code change is the standing
per-object texel diagnostic (follow-up #3).

### 1. THE ABSENT QUILT — cause identified: neither (a) nor (b)

The reviewer's decision tree assumed texel starvation (a) or pattern
collapse (b). It is **(c): the quilt is present, correctly oriented, and
correctly wired end-to-end — but its rendered contrast is ~1 grey level**,
and what the reviewer saw as "6 soft horizontal stripes" was never quilt
content at all. Evidence chain (TEST-QUEEN, 1K before-state preserved at
`output/finish/TEST-QUEEN/maps_1K/` + `review_1K_quilt_absent/`):

- **(b) excluded:** the baked normal map's crown island carries the exact
  diamond signature — FFT peaks at the corner (6 cycles along world Y,
  8 along world X — `frequency_y=6`/`frequency=8` as designed, confirmed
  by a mesh probe: island UV-U ↔ world Y at 0.096 UV/m, UV-V ↔ world X).
  The second harmonic (16 along X) is present too. A one-axis collapse
  cannot produce this.
- **(a) excluded:** the quilt corner amplitude is **±0.032 G at 1K and
  ±0.029 G at 4K** — resolution-independent. 25 texels/cell at 1K, ~98 at
  4K. Re-baking at `--res 4096` did NOT make the quilt appear (render
  quilt-corner power 2.5e9 at 4K vs 2.9e9 at 1K).
- **amplitude chain:** analytic synthesis of the exact pattern
  (`grid_diamond`, exponent 1.6, 17.8 mm over 254 mm cells) on the exact
  island grid puts only **±0.057 G** in the fundamental — the 1.6
  exponent concentrates the geometry into sharp crests whose energy sits
  in harmonics. The bake delivers 55% of even that (±0.032) because the
  HP vertex grid (~7 vertices per cell after subsurf-2) smooths the
  crests. A ±0.03 G tilt is ~3.7° → ~±1.2 grey levels under the studio
  sun rig — literally invisible next to the fabric weave.
- **the "6 stripes" were basecolor aliasing:** the baked basecolor's crown
  island is dominated by 8 soft bands along world Y (~190 mm period, ±16
  grey levels). The source albedo is flat at that scale (row-mean ±2) —
  the bands are a bake-time aliasing beat of the sub-texel fabric detail:
  the knit weft (9.6 mm period) and the chevron print lines (4 mm thick)
  against the crown's 10.2 mm bake texel. At 4K the fabric resolves (weft
  at 3.8 texels/cycle now renders; the 190 mm beat band dropped to ~43%
  amplitude and is no longer dominant) — and the chevron print itself is
  now in the map, though still sub-pixel in a 1024 render.
- **render path exonerated:** with basecolor flattened to grey, the
  normal-map-only render's crown power is 31% concentrated at the quilt
  diamond corner — the normal map renders correctly; the quilt is simply
  ~13× fainter than the albedo stripes were.

**Gemini verdicts (advisory, verbatim recorded):** BEFORE (1K renders):
defect inspection PASS (tape ~1/35 H thin binding, bands coherent);
render-vs-reference **4/10** — "Missing detailed top tufting and diamond
quilting pattern". AFTER (4K renders): defect inspection PASS but flags
"Minor dark edge artifacts along top perimeter boundary" (see #2);
render-vs-reference still **4/10** — "Missing top diamond quilted
pattern", "Flat top surface detailing". Independent confirmation that 4K
did not fix the quilt — consistent with the amplitude diagnosis.

**Fix direction (NOT implemented — for owner review):** raise the quilt's
fundamental amplitude — lower `exponent` (1.6 → ~1.0 puts the energy back
in the fundamental), and/or raise `amplitude_fraction` (0.07), and/or add
HP subdivision for the crown (`subdivision_levels` in the part detail).
Separately, the basecolor aliasing says the fabric detail needs to be
pre-filtered to the bake texel size (or the basecolor baked at higher
resolution than the geometric maps). 1K stays the iteration default.

### 2. Residual perimeter speckles — NOT the old bleed

Mechanical figures: smallest island-to-island gap is exactly the packer's
0.01 UV margin (10.2 px at 1K / 41 px at 4K) vs the bake margin max(4,
res//128) = 8 px at 1K / 32 px at 4K — margins stay under the gaps at
both resolutions; islands sit 10.2 px off the atlas border; the maps'
crown rim content is mild (AO 227-244 vs 255 inner, basecolor 224 vs 231)
— nothing black to bleed.

The speckles (210-380 dark blobs hugging the silhouette, 3-15 px inside)
**grow with render resolution**: 441 dark px per 1000 rim px at a 1024
render vs 3775 at 4096 — the opposite of mip-bleed behaviour. They are
grazing-angle shading of the micro-weave normal detail on the dome's
near-vertical rim (normal tilts blow up as cos θ → 0), deepened by the
rim's AO. Same at 1K and 4K bakes (proportional margins). New
phenomenon, not the old root cause; also independently flagged by Gemini
("dark edge artifacts along top perimeter"). Fix direction if wanted:
suppress the detail-normal blend near silhouette grazing angles, or
soften the weave bump strength — cosmetic, owner's call.

### 3. Standing per-object texel-density diagnostic — implemented

`_uv_diagnostics` now emits `texel_density_per_object` (worst density
first): per object — islands, uv_area, atlas_share, world_area_m2,
world_area_share, texels_per_m (area-weighted) plus island min/max. The
whole-model ratio can read a healthy 1.000 while a surface starves;
now the per-object line shows it directly. Pinned by
`test_chiral_uv_diagnostics` (shares sum to 1, per-object figures match
the island list, worst-first ordering). Suite: **241 passed**.

Queen figures (reference resolution 1024 — the 1K iteration default):
every object 96.5 tex/m, ratio 1.000; crown 2 islands, 12.17% atlas
share, 12.17% world share, 24.5 texels per 254 mm quilt cell. The
mattress's hidden band caps inflate total world area to 48.7 m² (~8 m²
visible) — the atlas share is spent proportionally, so nothing starves
on texels; the quilt starves on amplitude (see #1).

### Operational finding — 4K bake timeout

`bake_maps` inherits the generic 300 s op timeout; a 4096² mattress bake
takes ~19 min on Scout's CPU. The one-off diagnostic used a
`_LongBakeRunner` subclass (`output/run_4k_diagnostic.py`, gitignored).
**If 4K becomes a delivery path, the bake timeout must become a real
parameter of `finish_delivery`** — not fixed in this round (1K stays the
default; run_4k_diagnostic.py is the workaround).

**Committed** (wip/defect-fixes, no push): per-object diagnostic + test.
Quilt amplitude/aliasing fixes await owner/reviewer decision on the
direction above. MAYA00053153 dims still blocked on the owner (rule 9).

## Session log — 2026-09-01 (round 3: visual quality vs the reference photos)

Owner's directive: NOT the submission phase — optimise for visual quality
against the reference photos, not the client's unknown validator. The owner
read the photos directly and corrected §5.2 in seven places (all
reviewer-sourced; GLM_BRIEF.md §5.2/§5.3 corrected and marked as such).
Work order: quilt geometry (corrections 3+1+2 as one crown change), band
order, handles, label, materials, bake timeout — Gemini verdict after each
group. TEST-QUEEN 80×60×12 IN stays the standing target; 1K stays the
iteration default. Suite: **243 passed**.

### Group A — the quilt is REAL LP geometry, square grid, ~119mm cells

`_DOME_SCRIPT` is replaced by `_QUILT_DOME_SCRIPT` (template.py). The FG
superellipse map carries a grid-topology cap: cell-commensurate grid lines
(spacing exactly 2/(cells·DIV) anchored at u=−1, so every DIV-th line IS a
cell boundary → stitch valleys land on grid lines), quadratic shoulder
clustering toward the boundary, boundary loop closed with a triangle fan.
Displacement is along vertex normals gated `normal.z ≥ restrict_z` (0.85) —
the steep shoulder stays on the footprint — and `H = h_band − AMP` so puff
peaks land exactly on the nominal band top. The crown HP is bevel-only
(`DetailSpec(subdivision_levels=0)`): subsurf would smooth the puffs away
in the baked normal (the round-2 failure mode).

Chosen numbers (queen): `cells_across: 17` → 17×13 cells = **119.0 ×
116.5mm** (reviewer target 100-130mm; 12-14 cells across the width → 13
cross-cells derived); `amplitude_fraction: 0.12` of one cell = **14.3mm**
(0.15 = 17.8mm read as "sharp, exaggerated zigzag peaks" in the Group-A
render check → softened, exponent 1.6 → 1.3 with it); divisions 4.

Counts: crown **5,040 quads + 288 fan tris = 10,368 tri-eq** (the grid is
100% quads; the FG boundary fan is the only tri content; zero n-gons).
Whole LP **15,612 tri-eq** (after Groups C/E added handles + cord tapes)
against the provisional 50k Simple ceiling — **decimation is a no-op, the
quilt geometry survives into the delivered FBX** (3.2× headroom). HP
200,016 tri-eq. Measured: peaks exactly on the nominal 85.34mm band top
(0.000mm overshoot); nothing beyond the L/W footprint; silhouette relief
in the front render **1px → 9px** at a 67px period (was 1px when the quilt
was a baked normal map); height-field autocorrelation peaks at 119mm (X) /
116mm (Y); ASCII height field shows straight rows and columns — SQUARE.

**Square vs diamond:** the product photos (9.28.22 / 9.28.35) read SQUARE;
the marketing render (9.29.19) shows diamond. Two-variant possibility
flagged in §5.2; switching is one YAML line (`pattern: grid_diamond`).
Gemini: 3 (before) → 3 (after; the zigzag complaint → fixed by softening)
→ **4** (after-soft).

### Group B — band order off by one at both ends

Ten bands: crown .28 / air_mesh .162 / knit_1 .062 / velvet_1 .072 /
knit_2 .062 / velvet_2 .072 / knit_3 .062 / velvet_3 .072 / white_band
.056 / base .10 — the white knit RIB comes first below the air-mesh, and a
plain WHITE band sits above the base. New `plain_white` flat surface for
white_band (smooth — not the ribbed knit). Render luminance walk verifies
light-dark alternation in the corrected order with white_band light (124)
above the dark base; knit_1's window reads dark because tape_2 straddles
its top boundary (perspective, not a band error). Gemini **4** (no
band-order complaint).

### Group C — carry handles (§9.4 open question CLOSED)

Photo 9.28.35: vertical dark straps crossing the full border stack at
intervals along the long side. Modelled as `CarryHandleSpec`: **2 per long
side at the quarter points** (count/spacing read from the photos;
owner-tunable), width_fraction 0.08 (~24mm webbing), outer face at 0.92×
the tape protrusion — just behind the tape plane, no z-fighting where the
straps cross the tapes — inner face buried 4mm past the local curved wall,
spanning white_band → crown (bottom tape line to top tape line).
Verified in the LP GLB (world bounds) and in render ASCII crops (dark
vertical bands at both quarter points crossing the full stack). Gemini
said "missing vertical strap accents" — contradicted by the pixel
evidence; recorded as a VLM miss, not acted on. Score **4**. Pinned by
`test_carry_handles_cross_the_full_stack_inside_nominal`.

### Group D — label taller and narrower

Measured off the corner close-up (not the owner's round numbers): aspect
**0.46**, height_fraction **0.34** of H (z 0.16..0.50 — inside the velvet
stack 0.156..0.558, clears tape_3 and tape_2), center 0.33. Gemini **3**
("label tiny, distorted, unreadable") — judged a 1K render artifact: a
48×104mm label is ≈21×45px in a full-frame 1024 render. Label verified
present at the correct spot and size via ASCII; a label close-up render
would settle it if a verdict-level check is wanted.

### Group E — materials minor tuning

- velvet: tint 0.16 → **[0.09, 0.09, 0.10]**; baked basecolor velvet mode
  24/255 = **9.4%** (requested 0.08-0.10). New `SurfaceSpec.rotate_deg`
  (np.rot90 in compose.py — square tiles stay tileable): the scan's nap
  streaks run horizontal in texture space, quarter-turned so they render
  **VERTICAL** (−84° measured in the render).
- tape: **round cord** — width == protrusion at 0.023×min(L,W,H) (~7mm on
  a queen, 2.3% of H). `_build_sweep`'s circular branch needed
  `bevel_resolution=4` (12-gon ring). Cord inner tangent exactly on the
  band wall, outer tangent exactly on nominal — pinned by
  `test_tape_section_is_a_round_cord_flush_on_the_wall`.
- mesh holes: `cell_m: 0.010` → **10.0mm** pitch measured in the texture.
- Gemini verdict: **rate-limited (429) four times over ~35 minutes**
  (waits of 0/4/15 min between attempts) — daily-quota exhaustion, not a
  burst limit. The measured evidence above stands; re-run
  `output/verdict_round3.py groupE_after "output/finish/TEST-QUEEN/review"`
  after the quota resets if a verdict is wanted.

### Group F — bake timeout is a real finish_delivery parameter

`finish_delivery(bake_timeout_sec=300.0)` → threaded to
`runner.execute_op("bake_maps", ..., timeout_sec=...)`; recorded in the
finish section of BOTH the refusal (blocked) report and the delivered
report; CLI `--bake-timeout`. Tests pin the default (300.0 reaches the
bake op and is recorded) and the override (3600.0 reaches only the bake
op). Closes the round-2 operational finding: a 4K mattress bake takes
~19 min and the old fixed 300s op timeout killed it.

### New gotchas (pinned by tests, or bitten hard enough to write down)

1. **SUBSURF at levels=0 is DISABLED** — `modifier_apply` raises
   "Modifier is disabled, skipping apply". The harness now adds subsurf
   only when `part_levels > 0`, and an explicit 0 is distinguished from
   None (None = inherit HP levels; 0 = bevel-only HP — required for the
   quilt to survive the bake).
2. **glTF triangulates quads** — GLB face counts are 2× blend quad
   counts. Never do blend-side arithmetic with GLB numbers (cost one
   faces_total pin: predicted 9,870, actual 10,446).
3. **pack_scale dropped 0.75 → 0.5625** — the round-cord tapes carry ~3×
   the strip surface into the atlas. Not a defect (it still packs), but
   the tapes' texel share tripled.
4. **Exact island-count pins drift with quilt softness** — smart-project
   regroups faces as normals shift. Island pins are now ranges.
5. **Gemini verdict quirks this round**: reads "diamond" on a square grid
   (uncalibrated for pattern orientation — overruled by the owner's
   direct reading), missed handles the pixel evidence shows, cannot read
   a 48mm label at 1K. Advisory only — keep measured evidence in the
   loop.

MAYA00053153 dims untouched (rule 9). `Temp/` (the owner's raw WhatsApp
photo drop — duplicate of `input/references/MAYA00053153/`) gitignored.
Committed, no push.

## Session log — 2026-09-01 (round 4: the review rig IS the measuring instrument)

Owner's round-4 order, in priority sequence: (1) FIX THE RENDER RIG FIRST —
it had produced three wrong reads (190mm aliasing beat, quilt axis, velvet
tone); "a rig that produces misleading images is a worse problem than any
remaining model defect"; add label/border CLOSE-UPS, keep the four standard
views. (2) Band structure: ONE dark velvet mass with two FAINT STITCHED
SEAMS inside, bounded by exactly two white knit ribs — no white ribs between
velvet bands (reviewer's photo fractions: crown .28 / air_mesh .15 /
knit_top .09 / velvet .27 with seams / knit_bottom .11 / base .10); decide
three-parts-shared-material vs one-part-with-seam-detail and say which+why.
(3) Label rendering purple → black/white/blue. (4) Re-run the Gemini verdict
on the NEW rig once quota resets; the 4/10 history is unreliable (scored
against misleading renders). Keep the square-vs-diamond quilt flag open; do
not push; MAYA00053153 dims untouched. Suite: **250 passed** (+7 round-4
tests).

### Task 1 — cross-key rig + close-ups (the instrument rework)

`setup_studio_lighting()` rewritten as **KeyA/KeyB cross keys**: two SUNs of
equal energy 1.8 at elevations 40°, azimuths 90° apart (travel along −X and
−Y), so NEITHER quilt axis is privileged — a square grid now shades on both
axes. Fill 0.7 from the camera-ish −45° azimuth, rim 1.2 from behind.
Direction math pinned: sun direction = Rz(rz)·Rx(rx)·(0,0,−1); horizontal
travel azimuth = 90+rz°, elevation = 90−rx°. Suns are direction-only
(4-tuples; no location). `test_review_rig_is_cross_key_without_axis_privilege`
pins the rig (both keys downward, axis-aligned X vs Y, total energy < 7).

New `frame_closeup_ortho()` + `closeups` param on `render_views`:
`frame="part"` (target bounds + pad) or `"model_height"` (target x/y, model
full height — the "border stack in context" view). Part resolution: exact
then prefix match; unresolved parts are reported in `closeup_skips`, never a
crash. Close-ups are threaded from the template (`review_closeups` →
`ReviewCloseupSpec` → `_render_review`), NOT hardcoded in the finishing
layer (rule 11): label (decal_patch, part frame, pad 0.5) + border
(decal_patch, model_height, pad 0.1).

**Instrument-only evidence** (old LP GLB, new rig —
`output/finish/TEST-QUEEN/review_rigtest/`, isolating the rig change from
the model changes): top-view FFT quilt power axis ratio **12.0 → 0.87**;
clipped (≥0.995) pixels **0.06% → 0.000%**; white bands read **0.51–0.75**
(vs pure white before), velvet **~0.05**; the label close-up frames the
patch at ~327×717 px vs **21×45 px** in the old full-frame render (~15×).
Result keys report `render_engine` + `view_transform` (EEVEE Next, AgX).

### Task 2 — ONE velvet mass with stitched seams (decision: ONE part + seam geometry)

**Decision: one part with seam detail as real LP geometry, NOT three parts
sharing a material.** Why: (a) three shared-material parts would show NO
seam at all — the wall is continuous with matching normals at part
boundaries, so the seams must be authored either way; (b) texture-space
seams cannot be positioned reliably against the atlas packer (island
assignment is arbitrary) and stitches displace the surface — a pressed
crease is resolution-independent, survives decimation (decimate is a no-op
at 15.4k/50k), shades under raking light and picks up bake AO; (c) one part
keeps the velvet nap streaks continuous (the "one dark mass" reading) and
the part count drops 18 → 14.

Mechanism: `SeamRingSpec(z, depth)` on PartSpec → `_build_extrude` grows a
ring stack — each seam at z expands to rings (z−w, 0), (z, depth), (z+w, 0)
with w = 2·depth, each ring inset along the local 2D outward wall normal;
the wall is rebuilt per ring pair. Template: velvet .27 of H with seams at
1/3 and 2/3 of the band, depth 0.0065×min(L,W,H) ≈ 2mm pressed crease.
Verified: 8 rings at the expected z, crease rings inset ~2mm inside the
wall, velvet = 7×48 quads + 2×48 fan tris, zero n-gons.

Render evidence (side view, centre column band, 1K): exactly **ONE dark
run z 0.216–0.475** (mean luminance 0.106 ≈ the 9.4% baked velvet) between
the two white knit ribs (0.113–0.214 and 0.478–0.560) — no white between;
border close-up: seams dip **0.035–0.045 below the velvet mean** at z
0.30/0.39 and stay dark (crease, not rib).

### Task 3 — the purple label (root cause: synthetic stand-in)

The old `input/decals/MAYA00053153/albedo.png` predated the reference
photos: 85% black + blue rounded-rect + **2% scattered pure magenta noise**
— averaged to purple at render scale. Replaced with a real crop of the label
from photo 9.28.22 (`output/make_decal_crop.py`, reproducible: crop box
(495,940,675,1340), border-bleed cleanup, ×2 LANCZOS; 65.4% black / 8.2%
white / 18.2% blue / **0 magenta**). Label close-up chroma in the new
render: **51.4% black** (mean rgb [0.123, 0.126, 0.130]), **30.2% white**
(text), **5.3% blue** (icon, mean [0.162, 0.395, 0.517]), **0.0% magenta**.

### Task 4 — Gemini verdict on the new rig

Attempted after the re-render: **429 again** (daily quota; resets ~12:30 IST
Sep 2). One-shot retry scheduled for 12:31 IST Sep 2 (workspace automation)
running `output/verdict_round3.py round4_newrig output/finish/TEST-QUEEN/review`
— the 6-view set including both close-ups. Per the owner: the 4/10 verdict
history is NOT a baseline (scored against the old rig's misleading images).

### Full-chain re-render (1K, TEST-QUEEN placeholder card)

`output/run_round3.py 1024` with `_LongBakeRunner` (3600s) — 6 review views
(front/side/top/iso + label + border), atlas pack_scale 0.5625, LP **15,420
tri-eq** (budget 50k, decimation no-op — seam geometry survives), REFUSED as
designed (placeholder dims, rule 9; evidence in `output/blocked/`).
Verification (all pass, `output/verify_round4_renders.py`): FFT axis ratio
**0.86**; total clipped **0.010%** (one specular pixel in the front view);
**1** band-scale dark run in the border stack; seams resolved as creases;
label black/white/blue with 0% magenta.

### New gotchas (round 4)

1. **The resolver's PartSpec whitelist silently drops new fields** —
   `seam_rings` compiled by the template but never passed through
   `_resolve_part` built a seam-free velvet (192 faces, not 768) with no
   error anywhere. Any new PartSpec field must be added to the resolver
   passthrough; the seam-ring test now catches this class of bug.
2. **2D edge normals are winding-dependent** — the left normal (−ty, tx)
   points INWARD for CCW loops (footprint_outline is CCW), so seam insets
   went 2mm OUTWARD (radial 1.4744 vs wall 1.4718). `_profile_2d_normals`
   now orients by signed area: outward = s·(ay+by, −(ax+bx)) with
   s = sign(area).
3. **Fan-cap centre vertices land in the base/top z-buckets** — a ring
   count over z-buckets reads 49 at the base/top rings (48 wall verts + 1
   cap centre) vs 48 interior. Pin per-ring, not globally.
4. **np.interp requires increasing xp** — feeding it a decreasing z array
   silently returns garbage (a whole band-profile read "all dark" and a
   seam "read as rib" from flipped profiles before the script bug was
   found; the renders were correct). Measure scripts are instruments too.
5. **Test-writing lesson (close-up framing)**: assert the
   geometrically-guaranteed property, not an opaque pixel share — a thin
   leg tightly framed still covers few opaque pixels (the tabletop intrudes
   into the frame). The guarantees: framed part centred, model clipped at a
   frame edge, vertical pixel span grows >1.3× vs the whole-model view.

MAYA00053153 dims untouched (rule 9). Committed, no push.

## Session log — 2026-09-02 (round 5: Cycles never touched the GPU — device parameterised, measured, reported honestly)

### The bug

`scene.cycles.device` was hardcoded `"CPU"` at two sites in
`src/blender/harness_script.py` (op_bake_materials ~1945, op_bake_maps ~2851).
Reviewer's 4K evidence: 531 s wall, GPU 0 % utilisation / 0 MiB VRAM the whole
bake, 1348 CPU-seconds. Zero GPU setup anywhere (no compute_device_type, no
get_devices(), no device enabling).

### The trap (recorded per the work order)

The round-3 heuristic "~19 min means CPU, minutes means GPU" was WRONG on this
machine: a CPU 4K bake here is **~8.8 min — which IS minutes**. Elapsed time
was the proxy; **GPU utilisation is the mechanism**. The heuristic would have
passed while the GPU sat idle (it did, for a full session). Same class of
error as round 4's symmetry-ratio metric. DESKTOP_SETUP §5 now says this
explicitly with the nvidia-smi one-liner. **Measure the mechanism, not the
proxy.**

### The fix (exactly like bake_timeout_sec, per master prompt §I)

- `--bake-device` CLI option → `finish_delivery(bake_device=...)` → bake op
  params `"device"` → harness `_configure_cycles_device()`. The harness reads
  NO config; the parameter arrives as an op parameter, threaded from the caller.
- Proper enablement order: `preferences.compute_device_type` →
  `get_devices()` → per-device `use` flags → **then** `scene.cycles.device`
  (setting the scene device alone silently does nothing).
- `auto` (default) tries OPTIX → CUDA → HIP → ONEAPI → METAL; explicit
  `optix`/`cuda`/… or `cpu`. No usable GPU → clean CPU fallback with a loud
  `fallback_reason` recorded in the bake result AND qa_report
  (`finish.bake_device_resolved`: requested, GPU/CPU, compute type, enabled
  devices, fallback reason). CI and the laptop fall back silently-clean.
- 7 new tests (`tests/test_bake_device.py`): threading, default, evidence in
  report, step timings, plus blender-marked live device-evidence shape tests.

### Measurements (4K, TEST-QUEEN, the reviewer's exact workload)

| device | wall | GPU util | VRAM | notes |
|---|---|---|---|---|
| CPU | **531.0 s** | 0 % / 0 MiB | 0 | the bug signature, reproduced |
| OptiX | 590.0 s | mean 13.2 %, max 76 % | 4067 MiB | GPU engaged, evidenced |
| OptiX + persistent_data | **555.5 s** | mean 11.5 %, max 54 % | 2086 MiB | −6 % vs non-persistent |
| CUDA (run 1) | crashed ~525 s | — | — | native exit −1 at the FIRST metallic session, no error text; ao/normal/basecolor/roughness already saved |
| CUDA (repro) | **560.6 s** | mean 10.6 %, max 73 % | 2068 MiB | identical command — crash NOT reproducible (1-in-2 flaky) |

256 px smoke: CPU 23.6 s vs OptiX 76.6 s (overhead-dominated at small res).
Full data: `output/bakeoff/bake_device_bench.json` (consolidated, all legs +
run-to-run baselines), `scripts/bench_bake_device.py` (GpuSampler: 1 Hz
nvidia-smi + per-process Blender CPU-seconds).

**Verdict: GPU is engaged and evidenced, but on THIS workload it is 5–11 %
SLOWER than CPU.** DESKTOP_SETUP §7's "2–4 min on OptiX" is not achievable by
device choice — see next section. Default stays `auto` (per the work order);
`--bake-device cpu` is the fast path on this machine today.

### Why no device wins: the bake op is session-overhead-bound

Profiled a full OptiX 4K bake via a direct subprocess with Cycles' timestamped
stdout (`output/tmp/profile_optix/blender_stdout.txt`, 266 bake sessions):
each "Loading <map>" marker is one Cycles session; the AO/detail/emission
loops show exactly 14 sessions each (one per LP object), but the
selected-to-active NORMAL loop shows **196 = 14 LP targets × 14 HP sources**:
Blender splits each bake call (all HP shells selected) into one internal
session PER SELECTED SOURCE. Per-session cost ~1.8 s with a full scene
re-sync (14 LP + 14 HP + 14 CAGE objects) between every session:

| phase | sessions | seconds | % of bake |
|---|---|---|---|
| ao (self-AO per object) | 14 | 30.3 | 5 % |
| **normal (selected-to-active)** | **196** | **352.0** | **64 %** |
| normal_detail (self, bump) | 14 | 41.5 | 8 % |
| basecolor/roughness/metallic | 42 | 94.7 | 17 % |

GPU ray-traces only ~60–80 s of the ~555 s op — hence mean utilisation ~12 %.
`use_persistent_data` keeps the BVH alive (−6 %, 590→555.5) but cannot remove
the per-session sync. **Identified follow-up (NOT done — it changes bake
semantics and pinned outputs, owner decision): select only the CORRESPONDING
HP shell as source per LP target → 196 sessions → 14, normal phase ~352 s →
~25 s. That, not the device, is where the 2–4 min lives.**

### Determinism verdict (work-order constraint 4)

Three layers, all measured:

1. **Fixture level — what the 250 tests actually pin**: bit-identical across
   devices. `scripts/check_bake_determinism.py` reproduces the
   test_delivery_finish.py fixtures verbatim (512 px ramp normal, AO cavity);
   the exact pinned pixels (G=105/85/68 at wy=0.15/0.30/0.45, Δ+0/+1/+1;
   neutral 128/128/255; AO 0/255) are the SAME on cpu, optix and cuda, and
   the whole maps diff **0 LSB**.
2. **Real 4K workload**: CPU vs OptiX/CUDA — normal ≤3, basecolor ≤5,
   roughness ≤3 LSB max (mean ~0.001 LSB, 0.000 % of texels beyond 2 LSB);
   metallic byte-identical. The AO channel's 16-LSB tail on 0.008 % of texels
   appears **between two CPU runs too** (reviewer's chain maps vs bench leg:
   normal/basecolor/roughness/metallic 0 LSB — CPU is bit-stable run-to-run;
   AO is the one stochastic channel).
3. **Full suite with GPU active** (auto→OptiX on this machine):
   **263 passed in 122 s** (250 baseline + 13 new). No pinned value shifted;
   nothing re-baselined.

**Verdict: GPU output is equivalent within tolerance — exact (0 LSB) at the
pinned fixtures, ≤5 LSB worst-channel at 4K where the same-device run-to-run
floor is 0–1 LSB. No re-baselining needed or performed.**

### Queued item 1 — per-step timings in qa_report (PLAN_AUTONOMOUS §7)

`finish_delivery` now records `finish.step_timings_sec`
(prepare_scene/bake_maps/decimate_lp/export_fbx/export_usdz/review_renders +
total_chain) in every qa_report — the §7 per-step budgets are now verifiable
from delivered reports. Pinned by test.

### Queued item 2 — vision escalation wired (VISION_CONFIG §3)

`config/ai.yaml` `vision.vlm.escalation_model: gemini-3.6-flash` (reviewer's
`model: gemini-3.5-flash-lite` pin untouched); `vlm.py` validates it with the
same pinned-version rule (-latest rejected); the agent loop escalates ONCE on
gate disagreement (parsed verdict ≠ matches_reference) and records
`escalated_from`. No hardcoding; no live calls (S1: billing unconfirmed).
4 new tests.

### Phase 3.0 — execute_blender_script removed

Gone from `AGENT_TOOLS_SCHEMA` and the executor; the executor's fallthrough
intentionally refuses it. Pinned by `tests/test_agent_surface.py` (schema
absence + executor refusal). The validated-spec boundary is now enforced by
test, not convention.

### Brain test (GLM_PROMPT_BRAIN_TEST.md)

S2 RESOLVED mid-session: the reviewer filled §4 (three hand-written
descriptions with stated dimensions; reference images committed under
input/references/BRAINTEST-*). S1 still blocks VISION calls only — the brain
test needs none (descriptions are the input by design). Authored the three
ObjectSpecs cold, one shot each, no builds/renders/measure-feedback (schema
validation only): `output/braintest_specs/braintest_a_writing_desk.json`,
`braintest_b_teacup.json`, `braintest_c_doormat.json` + generation script.
Delivered in the session report with §5 notes blocks; the reviewer builds and
judges per §7. (Authoring-time catch worth recording: the rounded-rect
profile generator had a TL corner-centre typo — `hy+r` instead of `hy-r` —
that bulged the doormat's top-left corner 2×r outward; caught by a bbox
self-check added to the generator BEFORE any build. The check now ships with
it. Coordinate lists need guards exactly like measurements do.)

### New gotchas (round 5)

1. **Blender splits selected-to-active bakes per selected source** — N
   sources = N internal Cycles sessions per bake call, each with a full scene
   re-sync. Selecting "all HPs" for every LP object costs 196 sessions where
   14 suffice. (Measured, not documented anywhere in Blender's docs.)
2. **CUDA bake is flaky on this driver/Blender build** — 1 native hard crash
   (exit −1, no error text) in 2 identical 4K runs, at a metallic self-bake
   session. OptiX ran the identical workload twice, clean. `auto` prefers
   OptiX before CUDA — keep that order.
3. **AO is the one stochastic bake channel** — 16-LSB tails on 0.008 % of
   texels between two CPU runs; never treat an AO pixel diff as device drift
   without a same-device run-to-run baseline. CPU-vs-CPU: everything else
   bit-stable.
4. **os.times() does not account child CPU on Windows** — poll
   `Get-Process blender` instead (GpuSampler).
5. **A crash that kills the measuring script loses the report** — bench and
   determinism scripts now record device crashes as findings and continue.

MAYA00053153 dims untouched (rule 9). 263 tests green. Committed, no push.

## Session log — 2026-09-02 (round 6: Phase 3.1 — the agent's delivery tools, measured facts only)

### Brain test — measured gates green on all three specs (§7 visual verdict pending)

The reviewer ran the three authored specs through the full loop
(`output/runs/20260902_051750_braintest_*`); measured results from the
manifests:

| spec | dimension gate | mesh gate | tri-eq | bounds (m) | worst delta | wall |
|---|---|---|---|---|---|---|
| A writing desk | passed | passed | 374 | 1.200 × 0.750 × 0.600 | 0.0 mm | 5.4 s |
| B teacup | passed | passed | 2016 | 0.135 × 0.075 × 0.105 | 0.01 mm | — |
| C doormat | passed | passed | 160 | 0.750 × 0.020 × 0.450 | 0.0 mm | — |

Renders exist (`output/braintest_renders/{a,b,c}/view_{front,iso,side,top}.png`)
but NO §7 visual verdict is recorded anywhere — that judgement is the
reviewer/owner's call (the builder is text-only). S3 therefore NOT triggered;
per master order §D, work continued on the fork-independent Phase 3 items.

### Phase 3.1 — finish / inspect / review / package as agent tools

`src/agent/tools.py` now exposes the master order's tool table, all returning
measured facts, never prose:

| tool | wraps | returns |
|---|---|---|
| `inspect` | topology_report + measure + NEW uv_report ops + local dimension gate | per-part dims/bounds/closed-solid, polycount, n-gons, UV/texel diagnostics, every gate WITH its value (deltas in mm, budget vs tri-eq, ground-contact failures) |
| `review` | render_views op + advisory verdict | render paths, closeup skips, verdict JSON (only when refs AND a VLM exist) |
| `finish` | `finish_delivery` | lp/hp tri-eq, budget, bake device + step timings, UV/texel facts, gates — or the loud refusal |
| `package` | `package_delivery` | package dir, gates, file manifest (sizes/hashes), placeholders — or the loud refusal |

Design points pinned by tests:

- **State threading**: `build_spec` records `last_spec` alongside
  `last_built_glb`; finish/review/inspect default to it. An explicitly-passed
  INVALID spec errors loudly — never a silent fallback to the stale spec.
- **Rule 9 through the tool boundary**: `PlaceholderDimensionsError` is
  caught and returned as `{"success": false, "refused": true, "reason":
  "dims_placeholder", "error": ...}` — a result the brain can read, never an
  exception crashing the loop. The package tool does NOT demand a source GLB
  for a placeholder job (the refusal fires before the source is touched, so
  a missing GLB can never mask it — pinned with the MAYA card).
- **Verdict cache** (VISION_CONFIG §6): keyed by sha256 of render + reference
  image contents + model id; a cache hit is flagged `"cached": true` and
  makes no VLM call (pinned: 2 identical reviews → 1 call).
- **One shared escalation policy** (§3): `advisory_visual_verdict()` in
  tools.py is now used by BOTH the agent loop's visual gate and the review
  tool (behavior-preserving refactor of `loop._run_visual_gate`; FakeVLM
  wiring test still passes). S1 honored: no live vision calls — all verdict
  tests run against fakes.
- Broad exception catch on finish/package returns the error as a tool result
  (the brain sees errors; the loop never dies mid-chain).

### Harness additions (read-only measurement)

- `op_uv_report` (new, registered in DISPATCH): import → `_uv_diagnostics`
  at a caller-chosen resolution. Pure measurement — unwrapping stays in
  `generate_uvs`; a file with no UV layers reports islands_total=0 + reason.
- `op_topology_report` gains additive `objects_detail`: per-object
  verts/faces/tri/quad/ngon/tri-eq, loose/boundary/nonmanifold edges,
  bounds, and `closed_solid`.

### New gotchas (round 6)

1. **glTF vertex splits make every imported closed box look open.** The
   chiral fixture's boxes report `boundary_edges: 24` per part straight
   after build→GLB→import (vertices are split per normal/UV attribute, so no
   edge is shared). `closed_solid` is therefore computed on a WELDED copy
   (`bmesh.ops.remove_doubles`, dist=1e-6 m — the splits are exact
   duplicates). The RAW boundary/nonmanifold counts stay in the report as
   file facts; the welded answer is what gates. Same class as the known
   "plain trimesh.concatenate reports false non-watertightness" invariant.
2. **A fresh parametric build is NOT "no UVs".** Blender primitives carry a
   default UV map and it survives the glTF round trip: the chiral build
   reports islands_total=12 (6 per box), in-bounds. The atlas step is about
   overlap elimination and packing, not UV existence — `inspect` reports
   what is actually in the file.

### Tests

`tests/test_agent_tools.py` (new): 21 tests — schema registration (incl.
Phase 3.0 no-code pin), inspect green/failure paths with values, invalid-
spec loudness, finish argument threading + REAL refusal chain on a stub
runner (evidence lands in blocked/, no package), package refusal without a
source (MAYA card), review verdict cache + one-escalation + closeup
threading + no-refs/no-VLM honesty, shared-helper unit + loop wiring, and a
blender-marked real round trip (chiral fixture: build → inspect →
generate_uvs → inspect; pins tri-eq 24, welded closed_solid True, raw
boundary 24, islands 12, dimensions gate green).

**Suite: 284 passed in 138 s** (263 baseline + 21 new; baseline grew,
nothing re-baselined). MAYA00053153 dims untouched (rule 9). Committed, no
push.

## Session log — 2026-09-02 (round 7: Phase 4 — intake, prompt → JobCard, owner textures)

### What the owner's prompt can now drive (all dynamic, nothing hardcoded)

`src/client/job.py` extended — NO parallel structure. New optional JobCard
fields, all `None` = contract default, all consumed through
`effective_*()` helpers so an override and the enforced number cannot
drift:

| field | effect | default (contract) |
|---|---|---|
| `polycount_ceiling` | overrides the tier table; UNBLOCKS `complex` (unknown ceiling → otherwise fail closed) | simple 50k (provisional) / medium 200k (observed) |
| `polycount_semantics` | which count the gate compares: `triangles` / `faces` / `triangle_equivalent` | triangle_equivalent (conservative) |
| `file_size_caps` | per-suffix `SizeCap(value, basis)` — MB (decimal, 10⁶) vs MiB (2²⁰) kept verbatim; the byte counts differ by 4.9% | observed decimal-MB caps (FBX 10 MB, LP 15 MB, HP 50 MB) |
| `required_formats` | defines "complete package" for the naming/file-size gates | full 9-file contract set |
| `texture_resolution` | bake/atlas resolution when the caller passes `resolution=None` (now the CLI default) | 1024 px |
| `fbx_axis_up`/`fbx_axis_forward` | FBX export axes (must be a non-parallel pair) | Y-up, -Z-forward |
| `intake_evidence` | constraint → quoted prompt fragment; rides into qa_report.json | — |

Settled decision (recorded here, pinned in tests): `required_formats`
drives the GATES and qa_report annotation (`required: true/false` per
emitted file + `contract_note`), NOT conditional emission — the finishing
chain always emits the standard superset because a partial chain degrades
the FBX (its materials come from the bake).

### Intake: prompt → JobCard, deterministic and loud

`intake_from_prompt()` (regex, no LLM) + `dump_job_yaml()` (round-trips
`load_job`, verified). Extracts ONLY explicitly stated constraints;
every silence or ambiguity is an `IntakeError` — rule 9 extended past
dimensions to every constraint:

- dims: `L x W x H <unit>` (unit REQUIRED; bare dims → error naming the
  triple; absent → placeholder path needs `placeholder_dims` +
  `placeholder_unit` together, refusal behavior unchanged);
- polycount: ceiling word + poly noun required (kills prose
  false-positives like "faces 3 challenges"); disagreeing duplicates
  error; non-integer ceilings error ("intake does not round a
  constraint");
- file-size caps: must NAME the deliverable; an orphan "max 20 MB"
  errors ("intake never guesses the target"); MB/MiB basis verbatim;
- resolution: `2048px`, or `2K` only with a texture word (a bare "8k" is
  more likely "8k tris"); conflicting statements error;
- formats: labeled clause only ("Formats: FBX, GLB"); unknown token
  fails loudly naming the known tokens;
- axis map: all three of L/W/H or none; FBX axes: up+forward pair or
  nothing (half-specified convention is a guess);
- complexity/orientation: explicit caller argument > prompt statement >
  error. Never guessed.

### Owner-supplied textures: the drop-directory index

`src/textures/owner_index.py` — scans `input/textures/owner/<surface>/`,
one sub-directory per surface with the SAME canonical map names the
harness `_find` already consumes (albedo/roughness/height + .jpg/disp.png
aliases), because a selected surface's path goes STRAIGHT into
`PBRMaterial.texture_dir` (triplanar BOX projection — no copying, no
renaming). Writes deterministic `index.json` with measured facts per map
(resolution_px, sha256, edge_wrap_delta_mean), skipped dirs with
reasons, and the selection contract in the index itself: **if a required
surface has no supplied file, compose from CC0 scans; NEVER generate one
with a diffusion model** (does not tile seamlessly, cannot produce a
true normal map).

`edge_wrap_delta_mean` = mean absolute per-channel diff (0–255) between
opposite edges: 0 = edge VALUES continue across the tile boundary.
Documented caveat (pinned as behavior in tests): a 1px checker is
geometrically tileable yet reads ~255 — the number measures value
continuity, not tiling correctness; judgment stays with the brain.

### Bugs the new tests caught (production fixes this round)

1. `_POLY_NOUN` lacked `faces?` — its own docstring promised "no more
   than 8000 faces" but the regex only knew tri/poly nouns; faces-
   semantics statements were silently ignored (worse than erroring: the
   ceiling would silently default). Fixed; `polycount_semantics="faces"`
   now extracts.
2. The tabletop orientation pattern `\btable[- ]top\b` missed the
   one-word spelling "Tabletop" → a tabletop prompt errored with "no
   orientation". Fixed to `\btable[- ]?top\b`.
3. Pillow deprecation: `Image.getdata` is removed in Pillow 14 —
   `_edge_wrap_delta_mean` rewritten on `tobytes()` (identical
   arithmetic, zero warnings).

### Gates + packaging threading (measured, pinned)

- `check_naming`/`check_polycount`/`check_file_sizes` read effective
  values only; existing message pins preserved verbatim ("50,000"
  ceiling text, "10.00MB > 10MB" offender format).
- faces semantics is a REAL constraint, pinned: 150k faces / 290k
  tri-eq passes a 200k `faces` ceiling that triangle_equivalent fails.
- MB vs MiB pinned: a 12,000,000-byte FBX passes "12 MiB"
  (12,582,912 B) and fails "12 MB" — the basis is carried, never
  assumed.
- `complex` + no ceiling fails closed ("the job card sets no
  polycount_ceiling override — ask the client, do not guess"); the
  override unblocks it.
- `finish_delivery` threads the card end-to-end (stubbed-chain test
  with a fake independent FBX parse): decimate budget = card ceiling
  (beats both tier table and spec tri_budget), bake resolution =
  card's 2048 when the CLI passes None, FBX exported with the card's
  Z-up/Y-forward pair, `axis_convention.requested` + per-file
  `required` flags + `contract_note` in qa_report.json, all six gates
  green against matching stub facts.
- CLI `--res` default None → the card's `texture_resolution`, else
  1024; the agent `finish` tool threads None the same way (it no
  longer forces 1024 over the card).

### Tests

`tests/test_client_intake.py` (37): SizeCap basis math; every effective
helper (incl. complex-tier unblock, semantics default, cap fallbacks);
card validation errors (unknown format/cap key, empty formats, half and
parallel FBX pairs); intake happy paths (full prompt extracting all
eight constraint kinds + evidence; metric dims; noun-first polycount;
faces semantics + k suffix; px resolution + num-first caps; explicit
args beating prompt statements); every refusal path (bare dims, no
dims, conflicting dims/polycount/resolution, orphan cap, unknown format
token, partial axis map, FBX up-only, missing complexity/orientation,
placeholder without unit); YAML round-trip with card equality; gate
overrides (complex fail-closed/ceiling override/faces
semantics/naming subset/MiB-vs-MB); finish threading via the stubbed
chain + explicit-resolution-beats-card.

`tests/test_owner_textures.py` (11): edge-wrap metric (flat 0.0, seam
>100, checker caveat ≥200, 1px → None); index surfaces sorted with
measured facts (resolution, sha256 vs hashlib on disk, wrap, min
resolution, other_files); .jpg and disp.png aliases; skipped dirs
(normal-only drop, hidden) + root files; selection contract carried in
the index; index.json written + deterministic across runs + ignores
itself; write=False; missing root fails loud.

**Suite: 332 passed in 130 s** (284 baseline + 48 new; baseline grew,
nothing re-baselined). MAYA00053153 dims untouched (rule 9). Committed
under the owner's identity, no push.


## Session log — 2026-09-02 (round 8: Phase 5 — the closed loop)

Master order Phase 5: `build → inspect (gates) → green? → review (vision)
→ decide → fix → repeat; red → skip vision, fix, repeat`. Gates before
eyes, always. Hard iteration cap, start at 8. On cap: stop, report
exactly what failed with the evidence. Never loop forever. Never claim a
success you cannot evidence.

### What changed

**Iteration cap 5 → 8** — `config/ai.yaml agent.max_iterations: 8` +
loop.py fallback default 8. Pinned by a config test.

**Honest cap report** (`loop.py`, the gap that motivated this round): a
cap-exhausted run whose corrector "fixed" the spec on the final iteration
used to exit the while loop with `last_error=None` and report
`completed_with_warnings` + `unresolved_error: null` — a silent,
unevidenced near-success. Now every cap-exhausted run (red gates OR never
verified) gets:
- manifest status `iteration_cap_exhausted` (surfaced by the web run
  registry via the manifest read; `web/js/app.js` classes it "bad");
- `metrics.iteration_cap_hit: true` + `metrics.cap_report` — which gates
  failed with values: dimension gate (checked/passed/failed counts, max
  delta in the message in mm, per-measurement failed details, ground
  contact) and mesh gate (faces, warnings, errors), or `last_error` when
  no iteration ever produced a verified build;
- a guaranteed non-None `unresolved_error` naming the cap ("Iteration cap
  (8) reached without passing gates: dimension gate FAILED (1/2
  measurements passed, max delta 60.00 mm); … no success is claimed.");
- an `iteration_cap_hit` progress event for the web UI.

**Gates before eyes, pinned**: new `tests/test_closed_loop.py` drives
`AgentLoop.run()` end-to-end with fakes (provider/runner/verifier/VLM —
no Blender, no network): 3 red iterations → ZERO vision verdict calls
(the analyst-eye describe is allowed pre-loop; verdicts are not); green →
exactly ONE advisory verdict after `verification.passed`.

**Owner texture library → analyst prompt**: `AgentLoop(owner_texture_root=…)`
(default auto-detects `input/textures/owner/`) indexes the drop directory
via Phase 4's `owner_index.py` and appends an "OWNER TEXTURE LIBRARY"
section to the analyst user text: every scanned surface with its exact
resolved `texture_dir` path, maps, min resolution, and the selection
contract (closest surface by look; NEVER invent a path; NEVER
diffusion — only these scans or presets). `ANALYST_SYSTEM_PROMPT`
MATERIALS documents `texture_dir` the same way. No library / empty
library → no section (presets only, exactly as before). The harness
already consumes `texture_dir` (canonical map names), so a selected
surface builds end-to-end.

**Close-ups in the loop's renders**: the spec's `review_closeups` now
ride into the loop's `render_views` op (same shape as the review tool), so
the visual gate receives label/border frames — the master order's
close-up rule only works if the loop actually produces them.

**429 branching implemented** (`src/ai/vlm.py`, VISION_CONFIG §7 —
previously policy-only in docs): every chat POST goes through
`_post_with_429_policy`:
- `RATE_LIMIT_EXCEEDED` → exponential backoff with jitter: base
  2 s × 2ⁿ capped at 60 s, + uniform jitter [0, 1) s, at most
  `RATE_LIMIT_MAX_RETRIES = 5` retries, then `RateLimitExhaustedError`
  (fail soft; NOT the quota branch — no local fallback for a rate limit).
- `QUOTA_EXCEEDED` / `RESOURCE_EXHAUSTED` → `QuotaExhaustedError`
  immediately, no retry. Classification prioritizes the specific
  `RATE_LIMIT_EXCEEDED` reason code — real Gemini rate-limit bodies also
  carry `status: "RESOURCE_EXHAUSTED"`, which alone is the quota branch
  per the §7 table.
- Quota → local Qwen verdict: `visual_verdict` routes through
  `_chat_vision_quota_fallback` — when the primary (Gemini) is
  quota-exhausted the call is served by the local OpenAI-compatible
  provider configured under `vision.local_fallback` (base_url + model of
  the vLLM server; loading/unloading it and returning the GPU to Blender
  is a server-side ops action, documented in code). The verdict records
  `quota_fallback: true` + the fallback model honestly. No fallback
  configured → honest `quota_exhausted` error verdict. The local tier has
  no second fallback (no loop-back).
- Tests monkeypatch `vlm._sleep` to RECORD delays — no real waiting; all
  against the mocked Gemini/local servers (S1: no live vision calls).

**Phase 5 image-size policy** (`vlm.py`): overview renders
(front/side/top/iso) and reference photos are downscaled to ≤768×768
(LANCZOS, aspect preserved, JPEG q90 / PNG) before sending; close-up
renders (any non-overview key) are NEVER downscaled — fine detail is
their entire purpose. `describe_reference_images` sends refs at ≤768.
Verdicts record the applied `image_policy`. `chat_vision` grew an
optional `image_max_dims` parameter (parallel to image_paths; None
entries pass through untouched) — backward compatible.

### Tests

`tests/test_closed_loop.py` (10): red-gates cap report (error text,
manifest status `iteration_cap_exhausted`, cap_report evidence incl.
failed measurement details, non-None unresolved_error, zero vision
calls, corrector ran every red iteration); green → exactly one vision
call + `completed`; build-failure cap (no verification → last_error in
cap_report); budget exhaustion is NOT a cap (status stays
`budget_exhausted`); review_closeups rendered into the loop's views;
owner library in the analyst prompt (both surfaces + exact paths +
never-diffusion, index.json written) + no-library/empty-library/
explicit-missing-root skips; config pins `max_iterations: 8`.

`tests/test_vlm.py` (+9): rate-limit backoff sequence (2 s and 4 s bases
+ jitter, recorded sleeps, then success); bounded give-up (exactly 5
retries, sorted exponential, ≤ 60 s + 1 s cap, RateLimitExhaustedError);
quota → zero sleeps + QuotaExhaustedError; quota → local-Qwen verdict
(`quota_fallback`, fallback model, parsed verdict); quota without
fallback → honest `quota_exhausted`; local primary has no second
fallback; `_image_b64` downscale semantics (oversized → 768 aspect-
preserved, close-up None → byte-identical, small → untouched);
`visual_verdict` threads the policy (mock server receives 768/768/2000
for reference/overview/close-up); describe sends refs at ≤768.

One classifier bug found by the tests: the first `_classify_429` treated
any `RESOURCE_EXHAUSTED` body as quota, but real Gemini rate-limit bodies
carry that status string too — the specific `RATE_LIMIT_EXCEEDED` reason
code now wins (documented in the classifier docstring).

**Suite: 351 passed in 131.75 s** (332 baseline + 19 new; baseline grew,
nothing re-baselined). S1 honored: no live vision calls — all 429/quota/
image-policy paths verified against the mocked Gemini v1beta and local
OpenAI-compatible servers with recorded sleeps. MAYA00053153 dims
untouched (rule 9). Committed under the owner's identity, no push.

## Session log — 2026-09-02 (round 9: Phase 6 — three unseen objects, end to end)

Master order Phase 6: three objects never seen before, start to finish,
report renders + honest read. **The reviewer is the visual judge** — S1
held for every run (owner has not confirmed Gemini billing; both
`THREED_VLM_API_KEY` and `GEMINI_API_KEY` stripped from the environment,
analyst prompt records "NOTE: vision is unavailable", visual gate fails
soft, no live vision calls).

### The chain (identical for all three)

`scripts/phase6_intake.py` (PROMPT.md → `input/jobs/<CODE>.yaml` via the
deterministic intake; dims + explicit unit, never inferred) →
`python -m src.cli build -p "$(cat PROMPT.md)" -m <measurements> -i
<photos> -n <name>` (analyst → gates → corrector) → `package --spec
<run>/spec.json --job input/jobs/<CODE>.yaml` (T3 finish chain) →
`validate <pkg> --job` (client mirror).

### Honest results per object

**A — coat stand** (refs: Rijksmuseum objectnr 8284 + a catalog photo;
job COATSTAND0001, 480×480×1750 mm floor): build GREEN in **2 iterations,
35.1 s**; package 29 s; **ALL SIX client gates PASSED** — dims
480/480/1750 mm, Δ ≤ 0.001 mm on every axis; LP 2240 tri-eq (budget
30000), HP 95872, 66 UV islands, 0 overlaps, texel ratio 1.0000051.
End-to-end ≈ 64 s.

**B — wall-mount mailbox** (refs: two museum/street photos; job
MAILBOX0001, 260×130×200 mm wall): three attempts, and the first two
failures were cold-path defects, not the model —
attempt 1 (125 s): corrector gave up at iteration 2 and the give-up was
**silent** (defect 1); attempt 2 (958 s): corrector chased mislabeled
targets with no measured part geometry (defect 2); attempt 3 (both fixes
in): GREEN in **7 iterations, 545 s**; package 28 s; **2 of 6 gates FAIL,
honestly**: Dimensions H→Z 200.73 mm vs 200.00 (**Δ+0.730 mm** — passes
the internal ±1 mm, fails the client ±0.01 in = ±0.254 mm) and
Orientation `wall` (intake accepts the word; the client contract defines
no wall semantics — the validator refuses to guess, rule 9). L/W exact.
LP 528/20000, HP 19728, 34 UV islands, 0 overlaps.

**C — watering can** (refs: two museum photos; job WATERCAN0001,
380×200×340 mm floor): build GREEN in **3 iterations, 181.8 s**; package
24 s; **1 gate FAIL**: L→X 379.19 vs 380.00 (**Δ−0.815 mm** — internal
placement tolerance ±5 mm passed, client ±0.254 mm failed); W/H exact.
LP 496/25000, HP 5728, 28 UV islands, 0 overlaps.

Renders (reviewer holds the visual verdict): `output/runs/
20260902_124637_phase6_a_coatstand_8a7669/renders/`,
`…_131627_phase6_b_mailbox_582f3d/renders/` (7 steps),
`…_132642_phase6_c_wateringcan_866b4d/renders/`, plus finish review
renders under `output/finish/<JOB>/review/`.

### Cold-path defects found by the runs (all fixed)

1. **Corrector give-up was silent.** A corrector response that failed
   JSON extraction or ObjectSpec validation read as "cannot fix" with
   the reason swallowed; a run could die at iteration 2 leaving no trace.
   `_correct_spec` now retries once with the failure quoted back
   (transient ≠ incapable), records `last_correction_failure`, and every
   give-up reaches the manifest as `unresolved_error: "Corrector gave
   up: …"`.
2. **The corrector flew blind.** Gate deltas alone leave part
   repositioning to guesswork (attempt 2 burned 958 s on mislabeled
   targets). Gate-failure correction prompts now carry the measured
   per-part geometry table (dims, center, bottom_z, top_z) — B converged
   only after this (`_measured_geometry_table`).
3. **NGON caps vs the strict delivery n-gon gate.** First live spec
   package in history with cylinders: `prepare_delivery_scene` refused
   20 n-gons (10 cylinders × 2 NGON caps) after the build had already
   converged. Caps are now TRIFAN fills in `_build_cylinder` /
   `_build_tapered_cylinder` / `_build_cone` — Blender 4.x parameter is
   **`end_fill_type`** (`fill_type` is unrecognized) — and extrude parts
   default to `caps: fan` at the schema level. Same triangle-equivalent
   count, so tri ceilings are unaffected.
4. **The regression that fix exposed, and its root cause.** After (3)
   the coffee_mug golden benchmark lost watertightness. Causal chain,
   pinned empirically: the EXACT boolean solver leaves **24
   coincident-but-distinct vertex pairs joined by zero-length edges**
   where the cut crosses the inner fan-cap ring (live mesh stays
   edge-closed; Blender's own `validate()` strips nothing); glTF
   tessellation ships them as **48 zero-area triangles `[P,P,Q]`**; the
   delivery check welds by position, and each degenerate face
   double-counts its edges (32 degree-4 + 8 degree-6 = 40 non-manifold
   edges) → not watertight. Fix: `apply_boolean` now dissolves
   zero-length edges (`_weld_solver_duplicates`, bmesh
   `dissolve_degenerate`, dist 1e-7 m — 2500× below the tightest client
   tolerance, so only solver artifacts can match). Bonus: the pinched
   5-vert loop faces dissolve back to quads (mug n-gons 48 → 0).
   Isolation verified: a bare TRIFAN cylinder is clean (0 zero-length
   edges) and the already-shipped COATSTAND LP/HP weld watertight with 0
   zero-area faces — the pathology is boolean-solver-only.
5. **Measurement grammar enforced pre-build + honest UNMEASURABLE
   feedback** (earlier in the phase): `applies_to` targets are
   structure-checked (unknown part / unmeasurable attribute refuse
   before any Blender call); verifier feedback names the reason instead
   of a fake Δ0 mm when a measurement cannot be taken.

### Findings recorded, deliberately NOT fixed (owner decisions)

- **Internal ±1 mm vs client ±0.254 mm tolerance gap.** B (+0.730) and
  C (−0.815) pass the internal gate and fail the client gate. Flipping
  the default was rejected mid-phase: the golden benchmark specs
  (coffee_mug, coffee_table, counter_stool, chiral_test) carry
  measurements without explicit `tolerance_m` and depend on the default.
  Options: tighten the default + add explicit tolerances to the goldens,
  or add a delivery-side re-check at client tolerance inside the loop.
- **Orientation `wall`** is accepted by intake vocabulary but has no
  client-contract semantics → the validator refuses to guess (rule 9).
  Needs a client clarification or an intake-vocabulary restriction.
- **No `center_z` in the measurement grammar**: A's peg-ring centers
  land 14 mm low (the analyst maps center-height constraints to
  `top_z`).
- **B body/dome split** 126.5/73.5 mm vs the prompt's stated 135/65.

### Tests

**+17 (351 → 368)**: explicit-JSON-null materials (2), resolver crash →
build error not run crash (1), corrector retry/give-up-reason/validation
reason/measured-geometry table (5), extrude caps default fan (1),
applies_to grammar + UNMEASURABLE feedback (4), and the NEW
blender-marked `tests/test_spec_shapes_delivery.py` (4: every vocabulary
shape n-gon-free; every shape a closed solid; boolean result welds
watertight after glTF; boolean result exports zero zero-area faces).

**Suite: 368 passed in 131.47 s** (was 365 passed + 1 failed at the low
point — the mug regression was fixed at the source, not by loosening its
assertion; baseline grew, nothing re-baselined). S1 honored (no live
vision calls). Rule 9 honored (no dims inferred; MAYA00053153
untouched). Committed under the owner's identity, no push.

## Session log — 2026-09-02 (round 10: Phase 7 — batch throughput)

Master order Phase 7: "3 jobs concurrently (32 threads, 64 GB), CPU bakes
at 1K for iteration, GPU reserved for final 4K; then a batch of 5 with
real measured wall clock per model and total." S1 held for every run
(owner has not confirmed Gemini billing; the batch driver strips both
`THREED_VLM_API_KEY` and `GEMINI_API_KEY` per worker, analyst prompts
record "NOTE: vision is unavailable", visual gate fails soft — the
reviewer holds the visual verdict on the renders).

### Subjects (authored fresh, never seen before)

Five hard-surface objects, two CC0/museum reference photos each with
PROVENANCE.json, PROMPT.md in the Phase 6 format, deterministic intake →
`input/jobs/<CODE>.yaml` (dims + explicit unit, never inferred):

| Job | Object | Card dims (mm) | Ceiling |
|---|---|---|---|
| STEPSTOOL0001 | two-step step stool | 450×420×480 | 25,000 |
| MILKCHURN0001 | milk churn | 340×340×640 | 20,000 |
| GARDENTROWEL0001 | garden trowel | 320×70×45 | 15,000 |
| CHAMBERSTICK0001 | chamber candlestick | 190×140×65 | 20,000 |
| GALVBUCKET0001 | galvanized bucket | 260×260×330 | 20,000 |

Driver: `scripts/phase7_batch.py` — per-job subprocess chain
(build → package --bake-device cpu → validate), ThreadPoolExecutor,
`THREED_BLENDER_THREADS` per worker (32 cores ÷ concurrency), per-step
logs + `summary.json` under `output/phase7/<tag>/`.

### The shakedown found two cold-path defects (both fixed at the source)

3-way shakedown (177 s total): MILKCHURN + GARDENTROWEL all gates PASS;
STEPSTOOL **Dimensions FAIL** — L/W swapped 90° about Z (Δ∓30 mm). Root
cause: the analyst's spec declares its own measurement→axis binding, so
the internal dimension gate verifies the analyst's DECLARED binding; the
loop never saw the job card, so the swap was invisible until package
time. **Fix:** `src.cli build --job <card>` threads the card into the
loop — the analyst prompt gets a CLIENT JOB CARD CONTRACT section (axis
map, meter-converted dims, `applies_to` bindings) and
`evaluate_card_axis_gate` (verifier.py) checks the measured overall
extents against the card inside `verify_run`, so the corrector fixes it
in-loop. Pinned in `tests/test_card_axis_gate.py` (12 tests).

The first full batch then failed CHAMBERSTICK by **+0.100 mm** and
GALVBUCKET by **+0.104 mm** — inside the internal ±1 mm (loop stopped)
but outside the client tolerance, which is 0.01 **in the card's declared
unit** (±0.01 mm for mm cards, ~100× tighter than the internal figure).
**Fix:** the card-axis gate now enforces the CARD's delivery tolerance
(`job.dim_tolerance_m()`), so an internally-green build is driven to
client-green inside the loop. Same test file pins the near-miss case.

Third fix (pre-existing, exposed by these runs): prompts phrased "under
N triangles" made intake set `polycount_semantics: triangles` — the
client Polycount gate then counted literal triangle faces, which read
~0 on a quad-clean FBX (vacuous pass). Prompts rephrased to "polycount
ceiling N" (noun `polycount` → semantics unstated → conservative
triangle-equivalent default); all five cards re-intaken.

### Batch of 5 — ALL FIVE JOBS PASS ALL SIX CLIENT GATES

Concurrency 5, 6 Blender threads/worker, CPU 1K iteration bakes
(GPU reserved), 32 logical cores / 62 GB RAM:

| Job | Iterations | Build | Package | Validate | LP tri-eq | HP tri-eq | Dimensions Δ |
|---|---|---|---|---|---|---|---|
| STEPSTOOL0001 | 1 | 31.2 s | 21.8 s | 10.7 s | 432 | 4,800 | ±0.000 mm |
| MILKCHURN0001 | 2 | 56.4 s | 23.7 s | 2.6 s | 2,176 | 49,152 | ±0.000 mm |
| CHAMBERSTICK0001 | 2 | 106.0 s | 20.2 s | 2.7 s | 2,688 | 14,080 | ±0.000 mm |
| GALVBUCKET0001 | 5 | 210.4 s | 16.8 s | 2.6 s | 1,472 | 38,464 | +0.002 mm worst |
| GARDENTROWEL0001 | 4 | 289.4 s | 12.5 s | 2.6 s | 44 | 1,316 | ±0.000 mm |

**Total wall clock: 305 s (5.1 min) for 5 verified, packaged models** —
~61 s/model of throughput at 5-way concurrency, every model
dimension-exact against its card (worst delta +0.002 mm, inside the
±0.01 mm card tolerance), polycount gates now counting real
triangle-equivalents (44–2,688 against 15,000–25,000 ceilings). Evidence:
`output/phase7/batch5/` (per-step logs + summary.json),
`output/packages/<CODE>/qa_report.json`. Iteration counts rose vs the
pre-fix batch (the card gate refuses to stop at ±1 mm) — that is the
honest cost of converging to the client's delivery tolerance in-loop;
STEPSTOOL needed only 1 iteration because the prompt contract oriented
the analyst correctly on the first spec.

### Final 4K GPU bake (GPU was reserved during the batch)

`package --spec <milkchurn run>/spec.json --job MILKCHURN0001.yaml
--res 4096 --bake-device optix --out-root output/phase7/final4k`:
**99.4 s** wall, `bake_device_resolved: GPU / OPTIX` on the RTX 4080
SUPER (recorded in qa_report), 5-map bake 84.6 s at 4096², LP 2,176 /
HP 49,152 tri-eq, 24 UV islands, 0 overlaps, texel ratio 1.0000008,
ALL SIX GATES PASSED, 9 deliverables + qa_report under
`output/phase7/final4k/MILKCHURN0001/`. Review renders await the
reviewer's visual verdict:
`output/phase7/finish/MILKCHURN0001/review/`.

### Suite + commit

**Suite: 389 passed in 135.43 s** (389 = 368 Phase 6 + 9
batch-concurrency + 12 card-axis-gate; baseline grew, nothing
re-baselined). S1 honored (no live vision calls). Rule 9 honored (no
dims inferred; MAYA00053153 untouched). AGENTS.md invariants added: the
card-axis gate contract and the polycount-phrasing semantics.
Committed under the owner's identity, no push.
