"""Package assembly tests (pure — no Blender needed).

A stub runner stands in for BlenderRunner.execute_op: it writes GENUINE
auditable artefacts (a real minimal binary FBX via fbx_inspect's writer, a
valid stored USDZ zip) and reports topology consistent with the stub
geometry, so every audit path in package.py is exercised for real — only
Blender itself is absent.

The chiral boxes mirror input/fixtures/chiral_test.spec.json (20 x 12 x 11 in
base + off-centre boss): distinct extents on all three axes and an
asymmetric feature, so a transposed dimension or mirrored export cannot
pass silently.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from src.client.fbx_inspect import box_corner_cloud, build_minimal_fbx
from src.client.job import JobCard
from src.client.package import package_delivery, usdz_structure_report

# (dimensions [dx, dy, dz] m, centre [cx, cy, cz] m) — synced with
# input/fixtures/chiral_test.spec.json. Overall bounds: 0.508 x 0.3048 x
# 0.2794 m (20 x 12 x 11 in), boss off-centre in +X +Y.
CHIRAL_BOXES = [
    ((0.508, 0.3048, 0.2286), (0.0, 0.0, 0.1143)),
    ((0.1016, 0.1016, 0.0508), (0.127, 0.0762, 0.254)),
]

# 20 x 12 x 11 inches — the job card the chiral fixture must satisfy.
CHIRAL_JOB_DIMS = {"length": 20, "width": 12, "height": 11, "unit": "in"}


def stub_box_polygon_indices(n_boxes: int) -> list[int]:
    """Quad polygon indices over 8 corners per box — the corner order must
    match box_corner_cloud (sx, sy, sz triple loop). The last index of each
    polygon is bit-inverted, per the FBX spec."""
    pvi: list[int] = []
    for b in range(n_boxes):
        base = b * 8
        for a, e, c, d in ((0, 1, 3, 2), (4, 5, 7, 6), (0, 4, 5, 1),
                           (2, 3, 7, 6), (0, 2, 6, 4), (1, 3, 7, 5)):
            pvi.extend([base + a, base + e, base + c, -(base + d) - 1])
    return pvi


def stub_topology_report(boxes, model_path):
    """Topology + bounds consistent with stub_box_geometry: 6 quads per box,
    no triangles, no n-gons, analytic world bounds."""
    quads = 6 * len(boxes)
    mins = [min(p[i] - d[i] / 2.0 for d, p in boxes) for i in range(3)]
    maxs = [max(p[i] + d[i] / 2.0 for d, p in boxes) for i in range(3)]
    return {
        "success": True,
        "model_path": str(model_path),
        "units": "meters",
        "objects": len(boxes),
        "vertices": 8 * len(boxes),
        "faces_total": quads,
        "triangles": 0,
        "quads": quads,
        "ngons": 0,
        "triangle_equivalent": 2 * quads,
        "loose_vertices": 0,
        "loose_edges": 0,
        "boundary_edges": 0,
        "nonmanifold_edges": 0,
        "bounds": {"min": mins, "max": maxs},
    }


class StubRunner:
    """Stands in for BlenderRunner: writes genuine auditable files, no Blender."""

    def __init__(self, boxes=None, facts_overrides=None):
        self.boxes = boxes if boxes is not None else CHIRAL_BOXES
        self.facts_overrides = facts_overrides or {}
        self.calls: list[tuple[str, dict]] = []

    def execute_op(self, op: str, params: dict) -> dict:
        self.calls.append((op, dict(params)))
        if op == "export_fbx":
            # Faithful to the shape Blender's exporter produces: axis
            # conversion baked into the mesh data (Y-up file space,
            # x/z/-y), values in centimetres (UnitScaleFactor 1.0), a
            # Model node carrying the transform, Connections linking
            # geometry -> model -> scene root.
            cloud = box_corner_cloud(self.boxes)  # blender Z-up, metres
            file_space = [[p[0] * 100.0, p[2] * 100.0, -p[1] * 100.0] for p in cloud]
            pvi = stub_box_polygon_indices(len(self.boxes))
            Path(params["path"]).write_bytes(build_minimal_fbx(
                vertices=file_space, polygon_vertex_index=pvi,
                creator="stub-fbx-writer",
                models=[{"uid": 1001, "name": "stub_model",
                         "translation": (0.0, 0.0, 0.0), "geometry_uid": 5001}],
                geometry_uid=5001))
            return {"success": True, "axis_up": params.get("axis_up")}
        if op == "export_usdz":
            with zipfile.ZipFile(params["path"], "w", zipfile.ZIP_STORED) as zf:
                zf.writestr("model.usda", "#usda 1.0\n(stub layer)\n")
            return {"success": True, "method": "stub-zip"}
        if op == "topology_report":
            report = stub_topology_report(self.boxes, params["model_path"])
            report.update(self.facts_overrides)
            return report
        if op == "info":
            return {"success": True, "blender_version": "stub-blender-4.5"}
        raise AssertionError(f"StubRunner cannot serve op {op!r}")


def make_job(**overrides) -> JobCard:
    data = dict(
        job_code="PKGTEST0001",
        dims=dict(CHIRAL_JOB_DIMS),
        complexity="simple",
        orientation="floor",
        product_class="test_fixture",
        reference_dir="input/fixtures",
    )
    data.update(overrides)
    return JobCard.model_validate(data)


def run_package(tmp_path, runner=None, job=None):
    """package_delivery against the stub, with the log captured."""
    src = tmp_path / "source.glb"
    src.write_bytes(b"stub-glb-payload")
    logs: list[str] = []
    report = package_delivery(
        job or make_job(), src, out_root=tmp_path / "packages",
        runner=runner or StubRunner(), log=logs.append,
    )
    package_dir = tmp_path / "packages" / (job or make_job()).job_code
    return report, package_dir, logs, src


# ── assembly ────────────────────────────────────────────────────────────────


def test_assembles_complete_package(tmp_path):
    report, package_dir, _, _ = run_package(tmp_path)
    expected = {
        "PKGTEST0001.fbx", "PKGTEST0001_LP.glb", "PKGTEST0001_HP.glb",
        "PKGTEST0001_LP.usdz", "PKGTEST0001_BaseColor.png", "PKGTEST0001_Normal.png",
        "PKGTEST0001_Roughness.png", "PKGTEST0001_Metallic.png", "PKGTEST0001_AO.png",
    }
    on_disk = {p.name for p in package_dir.iterdir()}
    assert expected <= on_disk
    assert (package_dir / "qa_report.json").is_file()
    assert report["all_passed"] is True


def test_export_fbx_requested_y_up(tmp_path):
    runner = StubRunner()
    run_package(tmp_path, runner=runner)
    fbx_calls = [p for op, p in runner.calls if op == "export_fbx"]
    assert len(fbx_calls) == 1
    assert fbx_calls[0]["axis_up"] == "Y"
    assert fbx_calls[0]["axis_forward"] == "-Z"


def test_missing_source_glb_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="source GLB not found"):
        package_delivery(make_job(), tmp_path / "nope.glb", out_root=tmp_path, runner=StubRunner())


# ── placeholders (owner amendment 3) ────────────────────────────────────────


def test_placeholders_logged_and_flagged_never_silent(tmp_path):
    report, package_dir, logs, src = run_package(tmp_path)
    # Loud at run time...
    assert any("PLACEHOLDER" in msg and "LP and HP" in msg for msg in logs)
    assert any("PLACEHOLDER" in msg and "texture" in msg for msg in logs)
    # ...and in the audit record.
    assert report["placeholders"]["lp_hp_single_source"] is True
    assert report["placeholders"]["textures_synthetic"] is True
    # Per-file flags: the two GLBs and five PNGs are placeholders, the FBX/USDZ are not.
    flags = {f["name"]: f["placeholder"] for f in report["files"]}
    assert flags["PKGTEST0001_LP.glb"] is True
    assert flags["PKGTEST0001_HP.glb"] is True
    assert all(flags[f"PKGTEST0001_{t}.png"] is True
               for t in ("BaseColor", "Normal", "Roughness", "Metallic", "AO"))
    assert flags["PKGTEST0001.fbx"] is False
    assert flags["PKGTEST0001_LP.usdz"] is False
    # The placeholder LP/HP really are byte-identical to the source (that is
    # the thing being flagged — silently shipping them would be the bug).
    src_bytes = src.read_bytes()
    assert (package_dir / "PKGTEST0001_LP.glb").read_bytes() == src_bytes
    assert (package_dir / "PKGTEST0001_HP.glb").read_bytes() == src_bytes


def test_placeholder_normal_map_is_opengl_neutral(tmp_path):
    """The synthetic Normal fill must be (128,128,255) — a wrong flat normal
    would shade the model incorrectly in the client's viewer even as a
    placeholder."""
    from PIL import Image

    _, package_dir, _, _ = run_package(tmp_path)
    with Image.open(package_dir / "PKGTEST0001_Normal.png") as img:
        assert img.getpixel((0, 0)) == (128, 128, 255)
        assert img.size == (64, 64)


# ── qa_report as complete audit record (owner amendment 4) ─────────────────


def test_qa_report_records_job_card_gates_and_convention(tmp_path):
    report, package_dir, _, _ = run_package(tmp_path)
    raw = json.loads((package_dir / "qa_report.json").read_text(encoding="utf-8"))
    assert raw == report  # the file on disk IS the returned report

    assert report["schema"] == "threed-qa-report/1"
    assert report["job_code"] == "PKGTEST0001"
    # Job card as loaded — dims with their explicit unit survive verbatim.
    assert report["job_card"]["dims"] == CHIRAL_JOB_DIMS
    assert report["job_card"]["complexity"] == "simple"
    assert report["job_card"]["axis_map"] == {"length": "x", "width": "y", "height": "z"}

    # Every gate with expected/received/message.
    gates = {g["gate"]: g for g in report["gates"]}
    assert set(gates) == {"Naming", "N-gons", "Polycount", "Dimensions", "Orientation", "File sizes"}
    for g in gates.values():
        assert set(g) >= {"gate", "passed", "expected", "received", "message"}
        assert g["passed"] is True

    # Axis convention actually written, independently parsed.
    ax = report["axis_convention"]
    assert ax["requested"] == {"axis_up": "Y", "axis_forward": "-Z"}
    assert ax["written"]["up_axis"] == "y"  # parsed from the stub FBX's own header
    assert ax["fbx_version"] == 7400
    assert "independent" in ax["verified_by"]

    # Tool versions present.
    assert report["tools"]["blender"] == "stub-blender-4.5"
    assert report["tools"]["python"]
    assert report["tools"]["platform"]

    # Open questions carried into every report until the client answers.
    oq_ids = {q["id"] for q in report["open_questions"]}
    assert {"simple-polycount-ceiling", "complex-polycount-ceiling", "usdz-size-cap"} <= oq_ids


def test_qa_report_file_hashes_and_sizes_match_disk(tmp_path):
    report, package_dir, _, _ = run_package(tmp_path)
    recorded = {f["name"]: f for f in report["files"]}
    assert len(recorded) == 9
    for name, f in recorded.items():
        path = package_dir / name
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        assert f["sha256"] == h, name
        assert f["size_bytes"] == path.stat().st_size, name
        assert len(f["sha256"]) == 64


def test_cross_checks_harness_vs_independent_fbx_parse(tmp_path):
    report, _, _, _ = run_package(tmp_path)
    cc = report["cross_checks"]
    # Stub geometry: 2 boxes x 6 quads = 12 faces, 24 triangle-equivalent, 0 n-gons.
    assert cc["faces_total_harness"] == 12
    assert cc["faces_total_independent_parse"] == 12
    assert cc["triangle_equivalent_harness"] == 24
    assert cc["triangle_equivalent_independent_parse"] == 24
    assert cc["ngon_count_harness"] == 0
    assert cc["ngon_count_independent_parse"] == 0
    # World extents: the independent parse resolves the Model chain + cm->m
    # normalisation; the harness re-imports in Blender space. Sorted
    # multiset must agree: 20 x 12 x 11 in.
    assert cc["world_extents_independent_parse_m"] == pytest.approx([0.508, 0.3048, 0.2794])
    assert cc["world_extents_harness_m"] == pytest.approx([0.508, 0.3048, 0.2794])
    assert cc["agree"] is True


def test_gate_failure_is_recorded_not_swallowed(tmp_path):
    """A failing package still produces its audit record — with the failing
    gate's expected/received preserved. Transposed L/W is the failure the
    axis map exists to catch: every number looks right, the assignment is wrong."""
    transposed_bounds = {"min": [-0.1524, -0.254, 0.0], "max": [0.1524, 0.254, 0.2794]}
    runner = StubRunner(facts_overrides={"bounds": transposed_bounds})
    report, package_dir, _, _ = run_package(tmp_path, runner=runner)
    assert report["all_passed"] is False
    dims = next(g for g in report["gates"] if g["gate"] == "Dimensions")
    assert dims["passed"] is False
    assert "L→X" in dims["received"]
    assert (package_dir / "qa_report.json").is_file()


def test_fbx_written_from_glb_is_flagged_triangulated(tmp_path):
    """The FBX is exported from the GLB, and glTF stores triangles only — the
    report must say so (it shapes the T3 decision on where the FBX comes from)."""
    report, _, _, _ = run_package(tmp_path)
    fbx_entry = next(f for f in report["files"] if f["name"] == "PKGTEST0001.fbx")
    assert "triangulated" in fbx_entry["note"]


# ── USDZ structure report ───────────────────────────────────────────────────


def test_usdz_structure_report_on_stub_package(tmp_path):
    _, package_dir, _, _ = run_package(tmp_path)
    report = usdz_structure_report(package_dir / "PKGTEST0001_LP.usdz")
    assert report["exists"] is True
    assert report["members"] == ["model.usda"]
    assert report["compressed"] is False  # USDZ requires stored (uncompressed) entries
    assert report["has_layer"] is True
    assert report["size_bytes"] > 0


def test_usdz_structure_report_on_missing_and_corrupt(tmp_path):
    missing = usdz_structure_report(tmp_path / "absent.usdz")
    assert missing == {"path": "absent.usdz", "exists": False}

    corrupt = tmp_path / "corrupt.usdz"
    corrupt.write_bytes(b"this is not a zip at all")
    report = usdz_structure_report(corrupt)
    assert report["exists"] is True
    assert "error" in report
