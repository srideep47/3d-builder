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

## T2 — Export & packaging ⬜ (not started)

## T3 — UV + HP/LP bake pipeline ⬜ (not started)

## T4 — Reference implementation (mattress MAYA00053153) ⬜ (blocked on owner-supplied dimensions — never infer, rule 9)

## T5 — Generalise ⬜ (not started)
