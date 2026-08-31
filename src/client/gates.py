"""Client validator gates — the local mirror of MetaZtech's panel
(GLM_BRIEF §4.2). One pure function per panel row; each returns a structured
result mirroring their layout.

Design contract:
- PURE: no Blender, no network, no filesystem mutation. Facts about the mesh
  (topology, bounds) arrive as a `MeshFacts` value gathered by the harness
  (`op_topology_report`, one fresh Blender process pointed at the packaged
  FBX — repo rule 1). Gates without facts FAIL CLOSED ("could not verify"):
  we never learn about a failure from their validator.
- Product-agnostic (rule 11): these functions know file names, numbers and
  axes. Any product noun appearing here means the abstraction leaked.
- The complete-file list lives in contract.py and is shared with
  check_file_sizes (owner amendment 1) — the gates cannot drift apart.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .contract import MB, REQUIRED_DELIVERABLES, TIER_TRI_CEILINGS, required_filenames
from .job import JobCard
from .units import from_metres

# A "Floor" model rests on the ground plane: min z within ±0.5 mm of 0.
# Matches the repo's internal grounding-gate convention (PLAN.md §8).
GROUND_TOLERANCE_M = 0.0005

# Orientations whose validator semantics we have NOT observed. The client
# contract (§4.2) only documents `Floor`; `tabletop` is the same physical
# check (the product rests on a surface = z=0 plane). wall/ceiling contact a
# vertical/inverted plane — guessing their convention could ship a bad
# package, so these fail closed until the client documents them.
_GROUND_PLANE_ORIENTATIONS = {"floor", "tabletop"}
_UNIMPLEMENTED_ORIENTATIONS = {"wall", "ceiling"}


@dataclass
class GateResult:
    """Mirrors one row of the client's validator panel."""

    gate: str
    passed: bool
    expected: str
    received: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MeshFacts:
    """Topology + bounds of the packaged primary model (the <JOB>.fbx),
    measured in metres by the harness `op_topology_report` op."""

    tri_count: int
    quad_count: int
    ngon_count: int
    triangle_equivalent: int  # tris + 2·quads + (n−2)·ngons — the "polycount"
    bounds_min_m: tuple[float, float, float]
    bounds_max_m: tuple[float, float, float]
    vertices: int = 0
    faces_total: int = 0
    loose_vertices: int = 0
    loose_edges: int = 0
    boundary_edges: int = 0
    nonmanifold_edges: int = 0
    source: str = ""

    @classmethod
    def from_topology_report(cls, report: dict) -> "MeshFacts":
        """Build from the harness op_topology_report result dict."""
        bounds = report["bounds"]
        return cls(
            tri_count=int(report["triangles"]),
            quad_count=int(report["quads"]),
            ngon_count=int(report["ngons"]),
            triangle_equivalent=int(report["triangle_equivalent"]),
            bounds_min_m=tuple(float(v) for v in bounds["min"]),
            bounds_max_m=tuple(float(v) for v in bounds["max"]),
            vertices=int(report.get("vertices", 0)),
            faces_total=int(report.get("faces_total", 0)),
            loose_vertices=int(report.get("loose_vertices", 0)),
            loose_edges=int(report.get("loose_edges", 0)),
            boundary_edges=int(report.get("boundary_edges", 0)),
            nonmanifold_edges=int(report.get("nonmanifold_edges", 0)),
            source=str(report.get("model_path", "")),
        )

    def extent_m(self, axis: str) -> float:
        i = "xyz".index(axis)
        return self.bounds_max_m[i] - self.bounds_min_m[i]

    def min_z_m(self) -> float:
        return self.bounds_min_m[2]


def _unverifiable(gate: str, reason: str) -> GateResult:
    return GateResult(gate=gate, passed=False, expected="verified mesh facts",
                      received="not verified", message=f"could not verify: {reason}")


# ── Naming ───────────────────────────────────────────────────────────────────


def check_naming(package_dir: Path, job: JobCard) -> GateResult:
    """Every required deliverable from contract.py is present, exact name.
    Presence is checked against the actual directory listing (which reports
    real on-disk casing) — Windows filesystems are case-insensitive, so a
    path stat would accept `<job>_ao.png` as `<job>_AO.png`.
    Their observed message: "Expected: <JOB>.fbx and <JOB>_* textures"."""
    try:
        actual = {p.name for p in package_dir.iterdir() if p.is_file()}
    except OSError:
        actual = set()
    missing = [name for name in required_filenames(job.job_code) if name not in actual]
    expected = f"{job.job_code}.fbx and {job.job_code}_* deliverables " \
               f"({len(REQUIRED_DELIVERABLES)} files)"
    if missing:
        return GateResult("Naming", False, expected,
                          f"{len(missing)} missing", f"Missing: {', '.join(missing)}")
    return GateResult("Naming", True, expected, "all present",
                      f"Expected: {job.job_code}.fbx and {job.job_code}_* textures — all present")


# ── N-gons ───────────────────────────────────────────────────────────────────


def check_ngons(job: JobCard, facts: MeshFacts | None) -> GateResult:
    """Strict zero n-gon gate (faces with > 4 vertices). Their observed
    message: "Count: 0 — No n-gons detected"."""
    if facts is None:
        return _unverifiable("N-gons", "no mesh facts (Blender unavailable or FBX missing)")
    count = facts.ngon_count
    if count == 0:
        return GateResult("N-gons", True, "Count: 0", "Count: 0",
                          "Count: 0 — No n-gons detected")
    return GateResult("N-gons", False, "Count: 0", f"Count: {count}",
                      f"Count: {count} — N-gons detected")


# ── Polycount ────────────────────────────────────────────────────────────────


def check_polycount(job: JobCard, facts: MeshFacts | None) -> GateResult:
    """Tier-ceiling triangle budget. Ceiling values and their provenance live
    in contract.TIER_TRI_CEILINGS (simple = 50k PROVISIONAL, medium = 200k
    observed, complex = unknown → fail closed)."""
    ceiling = TIER_TRI_CEILINGS[job.complexity]
    if ceiling is None:
        return GateResult(
            "Polycount", False, f"known ceiling for tier '{job.complexity}'",
            "no known ceiling",
            f"No polycount ceiling is known for complexity tier '{job.complexity}' "
            "(simple is provisional 50k, medium is observed 200k) — ask the client, "
            "do not guess (rule 9)",
        )
    if facts is None:
        return _unverifiable("Polycount", "no mesh facts (Blender unavailable or FBX missing)")
    tris = facts.triangle_equivalent
    if tris <= ceiling:
        return GateResult("Polycount", True, f"Max: {ceiling:,}", f"Polycount: {tris:,}",
                          f"Polycount: {tris:,} against Max: {ceiling:,}")
    return GateResult("Polycount", False, f"Max: {ceiling:,}", f"Polycount: {tris:,}",
                      f"Polycount: {tris:,} exceeds Max: {ceiling:,} (tier '{job.complexity}')")


# ── Dimensions ───────────────────────────────────────────────────────────────


def check_dimensions(job: JobCard, facts: MeshFacts | None) -> GateResult:
    """L/W/H in the job's declared unit, within the CLIENT tolerance
    (job.dim_tolerance, default ±0.01 in — see job.py for the rationale and
    for why this is separate from the internal ±1 mm build tolerance).

    The axis assignment comes from job.axis_map (L→X, W→Y, H→Z by default):
    a model with L and W transposed fails here even though the multiset of
    extents looks right, because their validator derives aspect ratios from
    the assignment."""
    if facts is None:
        return _unverifiable("Dimensions", "no mesh facts (Blender unavailable or FBX missing)")
    unit = job.canonical_unit
    tol = job.dim_tolerance_in_job_units()
    expected_by_axis = job.expected_bounds_m()

    lines = []
    passed = True
    for dim_name in ("length", "width", "height"):
        axis = getattr(job.axis_map, dim_name)
        declared = getattr(job.dims, dim_name)
        received = from_metres(facts.extent_m(axis), unit)
        delta = received - declared
        ok = abs(delta) <= tol + 1e-9
        passed = passed and ok
        lines.append(f"{dim_name[0].upper()}→{axis.upper()}: expected {declared:.2f}, "
                     f"received {received:.2f} {unit} (Δ{delta:+.3f})")
    expected_str = " ".join(f"{getattr(job.dims, d):.2f}" for d in ("length", "width", "height")) \
        + f" {unit} (±{tol})"
    received_str = "; ".join(lines)
    return GateResult("Dimensions", passed, expected_str, received_str,
                      "dimensions within tolerance" if passed else "dimension mismatch")


# ── Orientation ──────────────────────────────────────────────────────────────


def check_orientation(job: JobCard, facts: MeshFacts | None) -> GateResult:
    """`Floor` (and `tabletop`): the model rests on the ground plane —
    min z within ±0.5 mm of 0. wall/ceiling semantics are unobserved and
    fail closed rather than being guessed."""
    expected = f"{job.orientation.capitalize()}"
    if job.orientation in _UNIMPLEMENTED_ORIENTATIONS:
        return GateResult("Orientation", False, expected, "not checked",
                          f"orientation '{job.orientation}' semantics are not observed in the "
                          "client contract — refusing to guess (rule 9)")
    if facts is None:
        return _unverifiable("Orientation", "no mesh facts (Blender unavailable or FBX missing)")
    min_z = facts.min_z_m()
    if abs(min_z) <= GROUND_TOLERANCE_M:
        return GateResult("Orientation", True, expected, f"Floor (min z = {min_z:.4f} m)",
                          f"Expected: {expected} — model rests on the ground plane")
    return GateResult("Orientation", False, expected, f"min z = {min_z:.4f} m",
                      f"model {'floats' if min_z > 0 else 'sinks'} above/below the ground plane "
                      f"by {abs(min_z) * 1000:.1f} mm (tolerance ±{GROUND_TOLERANCE_M * 1000:.1f} mm)")


# ── File sizes ───────────────────────────────────────────────────────────────


def check_file_sizes(package_dir: Path, job: JobCard) -> GateResult:
    """Every capped deliverable from contract.py is within its observed cap.
    Shares the file list with check_naming (owner amendment 1)."""
    offenders = []
    checked = 0
    for deliverable in REQUIRED_DELIVERABLES:
        if deliverable.max_bytes is None:
            continue  # no known cap (usdz, textures) — presence is Naming's job
        path = package_dir / (job.job_code + deliverable.suffix)
        if not path.is_file():
            continue  # missing files are Naming's failure; don't double-report
        checked += 1
        size = path.stat().st_size
        if size > deliverable.max_bytes:
            offenders.append(f"{path.name}: {size / MB:.2f}MB > {deliverable.max_bytes / MB:.0f}MB")
    caps = ", ".join(f"{d.suffix} ≤ {d.max_bytes // MB}MB" for d in REQUIRED_DELIVERABLES
                     if d.max_bytes is not None)
    if offenders:
        return GateResult("File sizes", False, caps, f"{len(offenders)} over cap",
                          "; ".join(offenders))
    return GateResult("File sizes", True, caps, f"{checked} files within caps",
                      "all file sizes within caps")


# ── Runner ───────────────────────────────────────────────────────────────────


def run_all_gates(package_dir: Path, job: JobCard,
                  facts: MeshFacts | None) -> list[GateResult]:
    """All six panel rows, in the client's panel order."""
    return [
        check_naming(package_dir, job),
        check_ngons(job, facts),
        check_polycount(job, facts),
        check_dimensions(job, facts),
        check_orientation(job, facts),
        check_file_sizes(package_dir, job),
    ]
