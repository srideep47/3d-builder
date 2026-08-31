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

## T4 — Reference implementation (mattress MAYA00053153) ⬜ (blocked on owner-supplied dimensions — never infer, rule 9)

## T5 — Generalise ⬜ (not started)
