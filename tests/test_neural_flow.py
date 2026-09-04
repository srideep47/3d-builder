"""Neural build flow tests (§4.1–§4.4 chain): generate → analyse →
conform → deliver, with a fake img3d provider and a stubbed finish_delivery
— the flow's OWN logic is under test (event sequence, manifest evidence,
stop conditions), not Blender.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import trimesh

from src.client.job import JobCard, JobDims
from src.img3d.provider import ImageTo3DResult
from src.neural.flow import run_neural_build


def _card(length=0.8, width=0.6, height=0.12) -> JobCard:
    return JobCard(
        job_code="FLOW0001",
        dims=JobDims(length=length, width=width, height=height, unit="M"),
        product_class="test",
        complexity="simple",
        orientation="floor",
        reference_dir="input/reference",
    )


def _write_box(path: Path, extents, texture=False, metallic=0.34) -> Path:
    box = trimesh.creation.box(extents=extents)
    if texture:
        import numpy as np
        from PIL import Image

        mr = Image.new("RGBA", (32, 32), (0, 190, int(metallic * 255), 255))
        albedo = Image.new("RGBA", (32, 32), (200, 180, 160, 255))
        mat = trimesh.visual.material.PBRMaterial(
            baseColorTexture=albedo, metallicRoughnessTexture=mr
        )
        box.visual = trimesh.visual.TextureVisuals(
            uv=np.zeros((len(box.vertices), 2)), material=mat
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    box.export(path)
    return path


@dataclass
class FakeProvider:
    glb_path: Path
    available: bool = True
    calls: list[dict] = field(default_factory=list)

    base_url = "fake://img3d"

    def is_available(self) -> bool:
        return self.available

    def generate_mesh_from_views(self, views, output_dir, max_tris=None, seed=None):
        self.calls.append({"views": dict(views), "max_tris": max_tris})
        # the real provider writes into run_dir/neural/ — mirror that so the
        # run-relative GLB path (the web viewer's fetch URL) is exercised
        out = Path(output_dir) / self.glb_path.name
        if out != self.glb_path:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(self.glb_path.read_bytes())
        return ImageTo3DResult(
            success=True, output_glb_path=out, tri_count=12,
            duration_sec=0.01)


@dataclass
class _StubDelivery:
    all_passed: bool = True
    calls: list = field(default_factory=list)

    def __call__(self, job_card, spec, **kwargs):
        self.calls.append({"job": job_card.job_code, "spec": spec, "kwargs": kwargs})
        return {
            "all_passed": self.all_passed,
            "package_dir": "output/packages/FLOW0001",
            "gates": [{"name": "Dimensions", "passed": self.all_passed}],
        }


@pytest.fixture()
def delivery(monkeypatch):
    stub = _StubDelivery()
    monkeypatch.setattr("src.client.package.finish_delivery", stub)
    return stub


def _run(tmp_path, provider, card=None, route=None, declared_fabric=False):
    events: list[dict] = []
    result = run_neural_build(
        views={"front": str(tmp_path / "front.png"),
               "back": str(tmp_path / "back.png")},
        job_card=card or _card(),
        route_decision=route or {"route": "neural", "reason": "test", "forced": False},
        run_dir=tmp_path / "run",
        progress=events.append,
        declared_fabric=declared_fabric,
        img3d_provider=provider,
    )
    return result, events


def test_flow_happy_path_records_every_stage(tmp_path, delivery):
    glb = _write_box(tmp_path / "gen" / "x.glb", (0.4, 0.3, 0.06), texture=True)
    provider = FakeProvider(glb)
    result, events = _run(tmp_path, provider)

    assert result.success and result.status == "completed"
    assert result.package_dir == "output/packages/FLOW0001"
    # the full event chain, in order
    names = [e["event"] for e in events]
    assert names == [
        "run_started", "route_decided", "neural_generation_started",
        "neural_generation_done", "analyse_done", "conform_done",
        "package_started", "package_done", "run_finished",
    ]
    # the provider got the labelled views, front included
    assert provider.calls[0]["views"]["front"].endswith("front.png")
    # manifest evidence: route + analyse + conform + package, every time
    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["metrics"]["route"]["route"] == "neural"
    assert manifest["metrics"]["analyse"]["triangles"] == 12
    assert manifest["metrics"]["analyse"]["maps"]["albedo"]["present"] is True
    assert "consolidates" in manifest["metrics"]["conform"]["retopology"]
    assert manifest["metrics"]["package"]["package_dir"] == "output/packages/FLOW0001"
    assert manifest["status"] == "completed"
    # the split maps + conform spec were written into the run dir
    assert (tmp_path / "run" / "maps" / "albedo.png").exists()
    assert (tmp_path / "run" / "maps" / "roughness.png").exists()
    assert (tmp_path / "run" / "maps" / "metallic.png").exists()
    assert (tmp_path / "run" / "spec.json").exists()
    # finish_delivery received the CONFORM spec (file-backed part + retopology)
    spec = delivery.calls[0]["spec"]
    part = spec.parts[0]
    assert part.mesh_path is not None and part.retopology is not None
    assert part.target_size == [0.8, 0.6, 0.12]
    # the web viewer's fetch URL: run-relative GLB path rides run_finished
    finish_ev = next(e for e in events if e["event"] == "run_finished")
    assert finish_ev["final_glb_rel"] == "neural/x.glb"
    assert finish_ev["final_glb"].endswith("x.glb")
    assert result.final_glb_path.endswith(str(Path("run") / "neural" / "x.glb"))


def test_flow_s1_refusal_stops_before_delivery(tmp_path, delivery):
    """The square-mattress case: the aspect gate fires at ANALYSE (gates
    before eyes — the earliest honest stop), the delivery chain never runs,
    the manifest carries the measured refusal evidence."""
    glb = _write_box(tmp_path / "gen" / "x.glb", (0.8, 0.8, 0.8))
    result, events = _run(tmp_path, FakeProvider(glb))

    assert not result.success and result.status == "failed"
    assert "(S1)" in result.error and "aspect_ratio" in result.error
    assert delivery.calls == []  # never distorted, never delivered
    names = [e["event"] for e in events]
    assert "analyse_done" in names and "conform_done" not in names
    assert "package_started" not in names
    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text(encoding="utf-8"))
    aspect = next(c for c in manifest["metrics"]["analyse"]["checks"]
                  if c["name"] == "aspect_ratio")
    assert aspect["passed"] is False
    assert aspect["value"]["measured_ratio"] == [1.0, 1.0, 1.0]
    assert manifest["status"] == "failed"


def test_flow_analyse_gate_failure_stops_before_conform(tmp_path, delivery):
    glb = _write_box(tmp_path / "gen" / "x.glb", (0.8, 0.6, 0.12))
    box = trimesh.creation.box(extents=(0.8, 0.6, 0.12))
    open_mesh = trimesh.Trimesh(
        vertices=box.vertices, faces=box.faces[: len(box.faces) // 2], process=False)
    open_mesh.export(glb)  # open edges → the §4.3 gate fires
    result, events = _run(tmp_path, FakeProvider(glb))

    assert not result.success and result.status == "failed"
    assert "analyse gates failed" in result.error
    assert "open_edges" in result.error
    assert delivery.calls == []
    assert not (tmp_path / "run" / "spec.json").exists()


def test_flow_provider_unavailable_fails_loud(tmp_path, delivery):
    result, _ = _run(tmp_path, FakeProvider(tmp_path / "nope.glb", available=False))
    assert not result.success and result.status == "failed"
    assert "img3d service unreachable" in result.error  # S3-class, named


def test_flow_generation_failure_recorded(tmp_path, delivery):
    provider = FakeProvider(tmp_path / "x.glb")

    def fail(views, output_dir, max_tris=None, seed=None):
        return ImageTo3DResult(success=False, output_glb_path=None,
                               tri_count=0, duration_sec=0.0,
                               error="backend exploded")

    provider.generate_mesh_from_views = fail
    result, _ = _run(tmp_path, provider)
    assert not result.success
    assert "backend exploded" in result.error


def test_flow_declared_fabric_metallic_gate_fires(tmp_path, delivery):
    """§3.6: 34% metallic on declared fabric stops the flow at analyse."""
    glb = _write_box(tmp_path / "gen" / "x.glb", (0.8, 0.6, 0.12), texture=True,
                     metallic=0.34)
    result, events = _run(tmp_path, FakeProvider(glb), declared_fabric=True)
    assert not result.success
    assert "metallic_fabric" in result.error
    assert delivery.calls == []


def test_flow_gates_failed_status(tmp_path, delivery):
    """Delivery ran but its gates failed → completed_with_warnings, honest."""
    glb = _write_box(tmp_path / "gen" / "x.glb", (0.4, 0.3, 0.06))
    delivery.all_passed = False
    result, _ = _run(tmp_path, FakeProvider(glb))
    assert not result.success
    assert result.status == "completed_with_warnings"
    assert "delivery gates failed" in result.error
