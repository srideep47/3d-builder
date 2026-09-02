# MESH_SOURCES.md — Mesh Sources & Retopology Scoping

> **Status**: scoping document (Phase 8.4). No retopology implementation is
> committed. Everything measured below was produced on this machine
> (32-core CPU, 62 GB RAM, RTX 4080 SUPER 16 GB — note: all Blender remesh
> tools measured here are **CPU paths**; the GPU was idle) with the repo's
> Blender 4.5.13 on 2026-09-02. Evidence fixtures live under
> `output/meshsrc/` (gitignored); the job cards
> `input/jobs/RETOPO0001.yaml` / `RETOPO0002.yaml` are committed.
>
> This doc is the gate for Phase 8.5 (neural image-to-3D): **organics stay
> out of the delivery path until retopology exists** (master order, item:
> "Do not let scope drift here").

## 1. Purpose

Phase 8.3 gave the repo one mesh-source contract: a part is parametric,
scripted, imported, scanned, or neural (`src/spec/schema.py`,
`GenerationMethod.is_file_backed`). That contract lets dirty meshes
**enter** the pipeline. This document scopes what it takes to make them
**deliverable**: the measured failure modes of non-parametric geometry, the
tooling available to repair them, the output contract a retopologized part
must satisfy, and the phased integration plan behind the existing
mesh-source contract (the `scanned` provenance is the hook).

## 2. The mesh-source landscape (Phase 8.3 recap)

| method | geometry from | file-backed | notes |
|---|---|---|---|
| `parametric` (default) | shape vocabulary | no | born clean: quads, UVs, named |
| `custom_script` | harness script | no | last resort |
| `image_to_3d` | img3d service (T3/TRELLIS/Hunyuan, Phase 8.5) | yes (cached mesh) | `image_crop` required |
| `imported` | authored GLB/FBX/OBJ asset | yes | `mesh_path` + `target_size` required |
| `scanned` | 3D-scan / photogrammetry output | yes | `mesh_path` + `target_size`; the retopology hook |

All file-backed methods share ONE harness path (import → join → rescale to
`target_size` → place) with `mesh_scale: fit|uniform`. File units are never
trusted; bounds land on `target_size` by construction. What the contract
does NOT do: repair topology. That is retopology's job.

## 3. What actually fails today — the measured audit

Fixtures (reproducible): `dense_scan.glb` = Blender icosphere, subdivision
4 (2,562 verts, 5,119 tris), one face deleted via bmesh (3 boundary
edges), exported GLB; `dense_scan_big.glb` = subdivision 6 (40,962 verts,
81,919 tris), one face deleted. Job cards carry the measured dims at full
precision (424.389/421.198/445.512 mm and 428.304/424.246/447.386 mm).

### 3.1 The six client gates do not stop a triangulated scan

5,119-tri scan, full T3 chain (`scanned` part, split import — the status
quo), job card RETOPO0001:

- **All six client gates PASS.** Dimensions Δ+0.000 mm; N-gons 0 (the gate
  counts >4-vert faces; triangles are not n-gons); Polycount 5,119 ≤
  50,000; Orientation fixed by the 8.3 base-mode placement (min z −0.0000).
- The delivery is still **unusable as a textured product** — see 3.2.

This corrects the planning assumption that a dense triangulated mesh
"fails all of them". The gates measure the contract (names, counts, dims,
grounding), not mesh quality. Two further gaps: **no watertight check
exists in the package path** (the fixture's hole ships silently), and the
internal loop gate (`Verifier.evaluate_mesh`) treats both non-watertight
and over-budget face counts as *warnings* (`passed: True` on both
fixtures).

### 3.2 The real failures (measured)

| failure | measured evidence |
|---|---|
| **UV atlas shattering** | 5,118 islands ≈ one per face (mattress baseline: 124); every atlas cell holds exactly 1 face; texel density spread 36.5–204.0 texels/m = **5.59× ratio** (retopo'd runs: 1.00) |
| **Polycount at real scan scale** | 81,919 tri > 50,000 gate max (T2: FAIL) |
| **Finish-chain collapse at scale** | `prepare_delivery_scene` **TIMEOUT at 300.0 s** on the 82k-island atlas (T3: aborts before any gate runs) |
| **No watertight enforcement** | the 1-hole fixture passes all six gates; internal verifier logs it as a warning only |
| **No quad editability** | raw T2 scan sits in tri soup; the client's quad-clean FBX requirement is only met by parametric luck |
| **Raw-scan ground offset** | T2 raw file: Orientation FAIL at −23.8 mm (small) / −25.1 mm (big) below ground; fixed in the spec path by the 8.3 placement contract |

## 4. Root cause: glTF vertex splitting, not "triangles"

The fixture GLB stores **15,355 verts for 5,119 triangles** (3 verts per
face, +2) — the known glTF per-attribute vertex split. Blender's importer
preserves the split, so every edge is a boundary edge
(`boundary_edges = 15,355 = nonmanifold_edges`). From there:

- `smart_project` cannot walk across faces (no shared corners) → **one UV
  island per face** → the atlas packer gets 5,118 cells → texel chaos and
  the 300 s timeout at 82k faces.
- `quadriflow_remesh` **refuses** the mesh outright ("QuadriFlow:
  Remeshing failed") — it needs manifold input.
- The glTF importer's `merge_vertices=True` option does **not** repair it
  (15,355 verts unchanged; it only fuses verts with identical normals/UVs,
  and flat per-corner normals differ).

The split has **two** attribute sources, not one (measured on a
smooth-shaded icosphere that kept its primitive UV layer): smooth shading
removes the per-corner normal differences, but the sphere's own UV seams
still split corners — **162 verts exported as 205, with 88 boundary edges**.
Strip the UV layer too and the round trip is exact (162 → 162, 0 boundary).
So a mesh can arrive split even when it was never flat-shaded.

**The repair is a by-distance weld** — edit-mode
`bpy.ops.mesh.remove_doubles(threshold=1e-6)` on the imported object:

| mesh | before weld | after weld | time |
|---|---|---|---|
| 5,119 tris | 15,355 verts, 15,355 boundary edges | 2,562 verts, **3** boundary edges | 0.02 s |
| 81,919 tris | 245,653 verts, all boundary | 40,962 verts, **3** boundary edges | 0.25 s |

(The 3 remaining boundary edges are the fixture's genuine hole.)

## 5. In-Blender tool survey (all measured on this machine)

Inputs: the welded fixtures above. "Islands" = UV islands after
`smart_project` (66°) counted with a corrected bmesh counter, validated
against production: born-in-Blender UV sphere (512 faces) → 6 islands;
split-imported scan → 5,118 (matches the T3 atlas number exactly).

### 5.1 Weld — `remove_doubles(threshold=1e-6)`

**BUILT (R1)**: `_weld_imported_mesh()` in `src/blender/harness_script.py`
runs on EVERY file-backed import inside `op_build_from_spec` — after the
multi-object join (join fuses datablocks but not coincident verts), before
rescale. Because `op_prepare_delivery_scene` calls `op_build_from_spec`
in-process, the single call site covers the whole T3 spec path. The op
result carries per-part `weld` stats (verts/boundary edges before/after) —
the export re-splits, so that report is the only observable evidence.
Measurements in §4. Effect on the atlas: 5,118 → **212 islands** (welded,
not retopologized). Pinned by `tests/test_mesh_source.py` (fixture: 957 →
162 verts, boundary 957 → 3 — the hole rim survives; clean control: 162 →
162, a no-op).

### 5.2 QuadriFlow — `bpy.ops.object.quadriflow_remesh`

| input | output | time |
|---|---|---|
| sanity cube (294 quads), target 200 | 221 quads | <0.1 s |
| welded scan (5,119 tris, 1 hole), target 2000 | **1,796 quads, hole closed, 0 boundary edges** | 0.57 s |
| welded big scan (81,919 tris), target 8000 | **7,812 quads, closed** | 2.66 s |

Target faces honored within ~10%. Closes small holes. UV islands after
smart project: 69 (small, on live quads) / 160 (big).

**Hazard (measured defect #1): QuadriFlow silently no-ops on
voxel-remeshed geometry.** On the voxel output (14,132 quads) it returns
in 0.01 s with the mesh byte-identical — at every target tried (2,000 /
4,000 / 8,000 / 12,000), after `shade_smooth`, on a duplicated object,
after clearing custom split normals, and after a full GLB round trip +
re-weld. No error is raised. Consequences: voxel and QuadriFlow are
**alternatives, never a chain**, and any future harness op that chains
remesh steps must verify the face count actually changed (fail-closed),
because Blender will not report this failure.

### 5.3 Voxel remesh — Remesh modifier, mode `VOXEL` (OpenVDB)

| voxel_size (m) | quads out | dims drift (mm) | time |
|---|---|---|---|
| 0.012 | 6,152 | −5.5 / −4.1 / −2.9 | 0.03 s |
| 0.008 | 14,132 | −4.0 / −3.7 / −2.2 | 0.04 s |
| 0.006 | 25,278 | −2.6 / −2.1 / −1.3 | 0.04 s |
| 0.005 | 36,578 | −2.3 / −2.3 / −1.1 | 0.05 s |
| **0.004** | **400** | **−28 / −17 / −61** | 0.03 s |

All-quad output, holes closed, manifold. The surface shrinks by roughly
½ voxel. **Hazard (measured defect #2): at voxel_size 0.004 the remesh
collapses** to a 400-quad blob with dims up to −61 mm — deterministic
(two identical trials; `adaptivity` 0.0, `use_remove_disconnected` True),
unexplained. Until understood, the usable band on ~0.4 m objects is
voxel_size ≥ 0.005. UV islands after smart project: 202 (at 0.008).

The Remesh modifier in 4.5 exposes modes BLOCKS / SMOOTH / SHARP / VOXEL
only — there is no quad mode in the modifier; QuadriFlow is op-only.

### 5.4 Decimate (already in the T3 LP path)

Collapse 0.5 on 14,132 voxel quads → 6,430 tris + 3,851 quads: it
triangulates. It is a polycount reducer, not a retopology tool.

### 5.5 The GLB round trip destroys quads

1,796 quads exported to GLB → **3,592 triangles** on re-import (glTF
stores only triangles; the exporter triangulates). Verts stay welded if
the export is smooth-shaded and UV-free (verified: 1,798 verts, 0
boundary edges). Consequence for the integration design: **retopology
must run inside the live harness scene** (after import, before
rescale/place/UV) — a pre-processed GLB hop silently throws away
quad-ness and re-risks the vertex split.

## 6. End-to-end proof: retopology rescues the full T3 chain

Full `python -m src.cli package --spec ... --job ...` runs (job cards
RETOPO0001/0002, dims at full precision; the retopo meshes went through
the GLB hop, so they arrive as triangles — quads would be even better
in-scene):

| run | gates | LP (tri-eq) | UV islands | texel ratio | chain time |
|---|---|---|---|---|---|
| 5,119-tri scan, split import (status quo) | ALL PASS | 5,119 | 5,118 | 5.59 | 45.7 s |
| same, **welded** | ALL PASS | 5,119 | 212 | 1.00 | 19.9 s |
| same, **weld + QuadriFlow 2000** | ALL PASS | 3,592 | 221 | 1.00 | 19.0 s |
| 81,919-tri scan, split import | — | — | — | — | **TIMEOUT 300 s** (prepare) |
| same, **weld + QuadriFlow 8000** | ALL PASS | 15,624 | 367 | 1.00 | 44.0 s |

Texel ratio = max/min texels-per-m across islands (uniformity); 1.00 is
the packer doing its job. Note what welding alone buys: at modest scan
scale it fixes the entire texture story (212 islands, ratio 1.00). What
retopology adds: polycount control (3,592 vs 5,119; 15,624 vs a hard
FAIL at 81,919), closed manifolds, and — if applied in-scene — quad
editability. At real scan scale it is the difference between a delivery
and a timeout.

## 7. The retopology output contract

A retopologized part is accepted only if all of these hold (each is
directly measurable in the harness, fail-closed):

1. **Welded topology**: boundary-edge count explains only genuine holes
   (fixture: 3); verts ≈ ½ faces for quads, not 3× faces.
2. **Quad-dominant**: ≥ 95% quads, 0 n-gons (QuadriFlow measured: 100%
   quads, 0 tris).
3. **Watertight** (or hole inventory recorded): boundary edges = 0 after
   hole closing, or every remaining hole listed in the part record.
4. **Face budget**: triangle-equivalent ≤ 50,000 (client Polycount gate).
5. **Dimensions**: rescale-to-`target_size` already guarantees bounds
   (Δ+0.000 mm measured); the retopology stage runs BEFORE rescale so
   surface drift (voxel: ~½ voxel) is absorbed.
6. **Atlas-compatible**: after `smart_project`, islands ≤ ~500 at this
   scale (measured: 69–367; mattress baseline 124) and texel ratio ≤ 1.01.
7. **Stage verified**: if a remesh step is a no-op (face count unchanged),
   raise — Blender's silent no-ops (§5.2) must never pass unnoticed.

## 8. Integration design (behind the mesh-source contract)

- **PartSpec gains an optional `retopology` block**, e.g.
  `retopology: {tool: "quadriflow", target_faces: 8000}` or
  `{tool: "voxel", voxel_size: 0.006}`. Absent block = import as-is (the
  8.3 contract unchanged). The block is legal on any file-backed method
  (`imported`, `scanned`, `image_to_3d`) — that is how Phase 8.5 organics
  will ride it.
- **The harness applies it in the live scene**, right after
  `import_any`, before join/rescale/place: weld (always, for every
  file-backed part — it costs 0.02–0.25 s and is the root-cause fix) →
  the requested tool → verify §7 → continue the existing path. Never via
  a GLB intermediate (§5.5).
- **Fail-closed checks** after each stage: topology actually changed,
  face count within budget, boundary edges accounted for.
- **Corrector rules** (mirror the 8.3 wording): never drop `mesh_path`,
  `mesh_scale`, or the `retopology` block; dimension fixes go through
  `target_size`, never through re-topology parameters.
- **Recipe persistence**: like `run_dir/neural/`, persist the applied
  recipe + pre/post topology stats under `run_dir/retopo/<part>/` so
  qa_report can carry the evidence.
- **Watertight gate**: add a boundary-edge check to the package gates (or
  promote the verifier's watertight warning) once retopology exists to
  satisfy it — until then it would only block.

## 9. Phased plan

- **R1 — weld-on-import: DONE** (built into `op_build_from_spec`'s
  file-backed branch, §5.1). Measured on the pinned fixture: 957 → 162
  verts, boundary edges 957 → 3 (hole rim preserved), T3 islands 6 at
  texel ratio 1.0000001, bounds on target within 4e-8 m; the do-no-harm
  control (UV-free smooth sphere) reports 162 → 162, a no-op. Suite 442
  (3 new tests in `tests/test_mesh_source.py`).
- **R2 — the `retopology` block** (quadriflow primary; voxel for
  hole-closing/density control with voxel_size ≥ 0.005). Delivers
  polycount control + quads + watertight. Guarded by the §7 contract and
  the no-op detection.
- **R3 — external/neural retopo backends** (only if CPU QuadriFlow output
  proves insufficient for 8.5 organics): evaluate against the same §7
  contract. Nothing measured yet; candidates and the TRELLIS/Hunyuan
  choice are the owner's call (§10).

**Dependency for 8.5**: neural image-to-3D backends emit dense triangle
soup (typically ≥ 50k faces) — R1 is a hard prerequisite, R2 strongly
recommended. This is the "neural behind retopology" ordering from the
master plan.

## 10. Open questions (owner decisions)

1. **8.5 backend**: TRELLIS 2 (MIT, 16 GB at 512³) vs Hunyuan3D 2.1
   (12–16 GB). Both fit the 4080 SUPER; order says one at a time.
2. **Watertight as a client gate**: adding it changes the MetaZtech
   contract surface (currently six gates) — needs the client's sign-off
   before it blocks delivery.
3. **Voxel 0.004 collapse** (§5.3): investigate upstream (Blender/OpenVDB)
   or simply document the floor; no production need identified below
   0.005 m on ~0.4 m objects.
4. **QuadriFlow-on-voxel silent no-op** (§5.2): report upstream if
   reproducible outside this repo; in-repo it is contained by the §7.7
   no-op guard.

## Appendix: measurement confessions (§H)

- The first two survey probes used a broken UV-island counter: bmesh
  `edge.link_loops` returns one loop per adjacent face (the corner where
  the edge *starts*), so a "both endpoints match" test never fired and
  the counter returned the face count for every mesh. Every
  "islands = face count" number in those probes was an artifact. The
  corrected counter (per-vertex lookup via `face.loops`) was validated
  against two known cases (§5) before any number in this document was
  trusted.
- Probe 1 exported intermediates after a scene reset, so one UV
  measurement ran on the wrong mesh; discarded, re-measured live.
- The `merge_vertices=True` hypothesis was wrong (measured: no effect);
  the by-distance weld is the actual repair.
- HP tri-equivalents varied across runs (122,856 / 181,080 / 171,264 /
  405,200) because the bake-subdivision of the LP depends on topology;
  not load-bearing for any conclusion above.
