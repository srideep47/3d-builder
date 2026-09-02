"""Phase 8 item 2 pin: the DEFAULT review rig rakes form over the
absolute-contrast floor (blender-marked; auto-skips without Blender).

The tuned rig (committed as `setup_studio_lighting` defaults) and the
contrast probe are one contract: keys SUN 2.5 W/m² at 10° elevation
raking x- and y-relief separately, fill a whisper (0.1), rim 0.6 from
behind. Measured on the tuning fixture (RIGTUNE0001: flat mattress
proportions, 17 quilt cells over 2 m), the crown quilt reads
~9.5/~7.5 grey levels on x/y — both above the 6-level floor.

The SUBSTRATE RULE (hard-won in the tuning session): the pin renders a
PURE-FORM substrate — the built GLB with every material flattened to flat
grey PBR and all texture references stripped. Tuning or pinning on a
prepared-but-unbaked GLB is invalid: its materials still reference SOURCE
textures while UVs have been atlas-repacked, so the normal map samples
garbage and tilts the effective shading normals arbitrarily (the crown
rendered pitch-black under both keys while the file normals were healthy).
And an unstripped albedo shows ~10.5 grey levels of SPURIOUS modulation at
the quilt pitch (knit texture aliased onto the cell grid) — the probe must
measure FORM, not albedo. The real baked LP is verified end-to-end in the
delivery run (evidence in PROGRESS.md), not re-verified here.
"""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.blender

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = PROJECT_ROOT / "templates" / "mattress.yaml"
JOB = PROJECT_ROOT / "input" / "jobs" / "RIGTUNE0001.yaml"


@pytest.fixture(scope="module")
def runner():
    from src.blender.locate import locate_blender
    from src.blender.runner import BlenderRunner

    if locate_blender() is None:
        pytest.skip("Blender 3.3+ not found on this machine")
    return BlenderRunner()


def _pure_form(glb: Path, out: Path) -> Path:
    """Flatten every material to flat grey PBR, strip texture references
    (test-local GLB surgery: JSON chunk re-serialized with space padding,
    BIN chunk kept verbatim). Orphaned images/textures/samplers stay in
    the JSON — legal glTF, ignored by loaders."""
    data = glb.read_bytes()
    assert data[:4] == b"glTF", "not a GLB"
    json_len = int.from_bytes(data[12:16], "little")
    doc = json.loads(data[20:20 + json_len])
    bin_start = 20 + json_len + 8
    bin_chunk = data[bin_start:]  # verbatim, including trailing pad
    for mat in doc.get("materials", []):
        pbr = mat.setdefault("pbrMetallicRoughness", {})
        for key in ("baseColorTexture", "metallicRoughnessTexture"):
            pbr.pop(key, None)
        for key in ("normalTexture", "occlusionTexture", "emissiveTexture",
                    "emissiveFactor", "extensions", "extras"):
            mat.pop(key, None)
        pbr["baseColorFactor"] = [0.75, 0.75, 0.75, 1.0]
        pbr["metallicFactor"] = 0.0
        pbr["roughnessFactor"] = 0.8
    js = json.dumps(doc).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    bin_len = len(bin_chunk)
    if bin_len % 4:
        bin_chunk += b"\x00" * (4 - bin_len % 4)
        bin_len = len(bin_chunk)
    total = 12 + 8 + len(js) + 8 + bin_len
    out.write_bytes(
        b"glTF" + (2).to_bytes(4, "little") + total.to_bytes(4, "little")
        + len(js).to_bytes(4, "little") + b"JSON" + js
        + bin_len.to_bytes(4, "little") + b"BIN\x00" + bin_chunk)
    return out


@pytest.fixture(scope="module")
def probe_results(runner, tmp_path_factory):
    """The compiled mattress (RIGTUNE0001 fixture dims) built, flattened to
    pure form, rendered with the DEFAULT rig (no lighting override), and
    measured by the template's own contrast probes + view stats."""
    from src.client.job import load_job
    from src.render.metrics import measure_contrast_probe, view_stats
    from src.spec.resolver import resolve_spec_to_build_params
    from src.spec.template import compile_spec, load_template

    spec, _warnings = compile_spec(load_template(TEMPLATE), load_job(JOB))
    tmp = tmp_path_factory.mktemp("rigpin")
    glb = tmp / "built.glb"
    res = runner.execute_op(
        "build_from_spec",
        resolve_spec_to_build_params(spec, output_glb_path=str(glb)))
    assert res["success"], res.get("error")

    form = _pure_form(glb, tmp / "pure_form.glb")
    rv = runner.execute_op("render_views", {
        "model_path": str(form),
        "output_dir": str(tmp / "renders"),
        "prefix": "RIGPIN",
    })
    assert rv["success"], rv.get("error")
    views = rv["views"]

    probes = [measure_contrast_probe(
        views[p.view], tuple(p.region), tuple(p.cycles),
        band=tuple(p.band), min_amplitude=p.min_amplitude, axes=p.axes)
        for p in (spec.contrast_probes or [])]
    stats = {name: view_stats(path) for name, path in views.items()}
    return {"probes": probes, "stats": stats, "rv": rv}


def test_default_rig_meets_absolute_floor_on_both_axes(probe_results):
    """THE 8.2 contract: the default rig renders the crown quilt with
    absolute grey-level amplitude at or above the template's floor on BOTH
    axes (the floor gates the weakest axis — never a ratio, never the
    max)."""
    assert probe_results["probes"], "mattress template must author probes"
    for p in probe_results["probes"]:
        assert p["valid"], p.get("reason")
        assert p["amplitude_x"] >= p["min_amplitude"], p
        assert p["amplitude_y"] >= p["min_amplitude"], p
        assert p["passed"] is True, p


def test_default_rig_views_are_balanced(probe_results):
    """No blown or crushed fabric and a sane mean under the tuned rig."""
    for name, s in probe_results["stats"].items():
        assert s["valid"], (name, s.get("reason"))
        assert s["clipped_fraction"] < 0.02, (name, s)
        assert s["crushed_fraction"] < 0.02, (name, s)
        assert 40.0 <= s["mean_luminance"] <= 230.0, (name, s)


def test_rig_tune_environment_pinned(probe_results):
    """The rig constants were tuned under EEVEE Next + AgX — if a Blender
    upgrade changes the scene defaults, the floor above is the tripwire
    and this line names the suspect."""
    assert probe_results["rv"]["render_engine"] == "BLENDER_EEVEE_NEXT", \
        probe_results["rv"]["render_engine"]
    assert probe_results["rv"]["view_transform"] == "AgX", \
        probe_results["rv"]["view_transform"]
