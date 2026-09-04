"""comfy_trellis2 provider tests — graph construction from /object_info
defaults (§2.4's "build from defaults" contract), the HTTP flow against a
fake ComfyUI, unload (/free), and GenerateParams view resolution. No GPU,
no network: httpx is monkeypatched, /object_info is a fixture mirroring
the real Trellis2 INPUT_TYPES extracted from the installed custom node.
"""

from __future__ import annotations

import sys
import types
import uuid
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = PROJECT_ROOT / "services" / "img3d_service"
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from providers.base import GenerateParams  # noqa: E402
from providers.comfy_trellis2 import (  # noqa: E402
    ComfyTrellis2Backend,
    _build_node,
)

# Mirrors the INPUT_TYPES of the installed ComfyUI-Trellis2 nodes (subset
# with the exact spec shapes that matter to default-filling: typed inputs
# with config defaults, combos with defaults, a combo WITHOUT a default
# (file_format), and 1-element typed specs (data links)).
OBJECT_INFO = {
    "Trellis2LoadModel": {"input": {"required": {
        "modelname": (["microsoft/TRELLIS.2-4B", "visualbruno/TRELLIS.2-4B-FP8", "TencentARC/Pixal3D-T"], {"default": "microsoft/TRELLIS.2-4B"}),
        "backend": (["flash_attn", "xformers", "sdpa", "flash_attn_3"], {"default": "flash_attn"}),
        "device": (["cpu", "cuda"], {"default": "cuda"}),
        "low_vram": ("BOOLEAN", {"default": True}),
        "keep_models_loaded": ("BOOLEAN", {"default": True}),
        "conv_backend": (["spconv", "torchsparse", "flex_gemm"], {"default": "flex_gemm"}),
        "sparse_backend": (["xformers", "flash_attn"], {"default": "flash_attn"}),
        "use_reconviagen": ("BOOLEAN", {"default": False}),
    }}},
    "Trellis2LoadImageWithTransparency": {"input": {"required": {
        "image": (["whatever_is_in_input_dir.png"],),
    }}},
    "Trellis2PreProcessImage": {"input": {"required": {
        "image": ("IMAGE",),
        "padding": ("INT", {"default": 0, "min": 0, "max": 512}),
        "remove_background": ("BOOLEAN", {"default": False}),
        "max_size": ("INT", {"default": 2048}),
    }}},
    "Trellis2MeshWithVoxelMultiViewGenerator": {"input": {
        "required": {
            "pipeline": ("TRELLIS2PIPELINE",),
            "front_image": ("IMAGE",),
            "seed": ("INT", {"default": 12345}),
            "pipeline_type": (["1024_cascade", "512"], {"default": "1024_cascade"}),
            "cfg_strength": ("FLOAT", {"default": 6.5}),
            "front_axis": (["z", "x"], {"default": "z"}),
            "fill_holes": ("BOOLEAN", {"default": True}),
            "keep_only_shell": ("BOOLEAN", {"default": True}),
        },
        "optional": {
            "back_image": ("IMAGE",),
            "left_image": ("IMAGE",),
            "right_image": ("IMAGE",),
        },
    }},
    "Trellis2RemeshWithQuad": {"input": {"required": {
        "mesh": ("MESHWITHVOXEL",),
        "remesh_band": ("FLOAT", {"default": 1.0}),
        "remesh_project": ("FLOAT", {"default": 0.0}),
        "dual_contouring_resolution": (["Auto", "512", "1024"], {"default": "Auto"}),
        "remove_floaters": ("BOOLEAN", {"default": True}),
        "remove_inner_faces": ("BOOLEAN", {"default": True}),
    }}},
    "Trellis2SimplifyMesh": {"input": {"required": {
        "mesh": ("MESHWITHVOXEL",),
        "target_face_num": ("INT", {"default": 1000000}),
        "method": (["Cumesh", "Meshlib"], {"default": "Cumesh"}),
    }}},
    "Trellis2FillHolesWithMeshlib": {"input": {"required": {
        "mesh": ("MESHWITHVOXEL",),
    }}},
    "Trellis2MeshWithVoxelToTrimesh": {"input": {"required": {
        "mesh": ("MESHWITHVOXEL",),
        "reorient_vertices": (["None", "90 degrees", "-90 degrees"], {"default": "90 degrees"}),
    }}},
    "Trellis2MeshTexturingMultiView": {"input": {
        "required": {
            "pipeline": ("TRELLIS2PIPELINE",),
            "front_image": ("IMAGE",),
            "trimesh": ("TRIMESH",),
            "seed": ("INT", {"default": 0}),
            "texture_steps": ("INT", {"default": 12}),
            "resolution": ([512, 1024, 1536], {"default": 1024}),
            "texture_size": ("INT", {"default": 4096, "min": 512, "max": 16384}),
            "texture_alpha_mode": (["OPAQUE", "MASK"], {"default": "OPAQUE"}),
            "front_axis": (["z", "x"], {"default": "z"}),
        },
        "optional": {
            "back_image": ("IMAGE",),
            "left_image": ("IMAGE",),
            "right_image": ("IMAGE",),
        },
    }},
    "Trellis2ExportMesh": {"input": {"required": {
        "trimesh": ("TRIMESH",),
        "filename_prefix": ("STRING", {"default": "3D/Trellis2"}),
        "file_format": (["glb", "obj", "ply", "stl", "3mf", "dae"]),
    }}},
}

FAKE_HEX = "feedface" * 8  # patched uuid4 → deterministic export tag


def _backend() -> ComfyTrellis2Backend:
    b = ComfyTrellis2Backend()
    b._ready = True  # skip server spawn/adopt
    b._object_info_cache = OBJECT_INFO
    return b


def _img(tmp_path: Path, name: str) -> Path:
    from PIL import Image

    p = tmp_path / name
    Image.new("RGB", (32, 32), (120, 120, 120)).save(p)
    return p


class _Resp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPError(f"HTTP {self.status_code}")


def _fake_http(monkeypatch, history=None, prompt_status=200, uploads=None, free_calls=None):
    def fake_post(url, **kw):
        if url.endswith("/upload/image"):
            name = kw["files"]["image"][0]
            if uploads is not None:
                uploads.append(name)
            return _Resp(200, {"name": name, "subfolder": ""})
        if url.endswith("/prompt"):
            if prompt_status != 200:
                return _Resp(prompt_status, {"error": "bad graph", "node_errors": {}})
            return _Resp(200, {"prompt_id": "pid1"})
        if url.endswith("/free"):
            if free_calls is not None:
                free_calls.append(kw.get("json"))
            return _Resp(200, {})
        return _Resp(404, {})

    def fake_get(url, **kw):
        if "/history/" in url:
            return _Resp(200, history or {})
        if url.endswith("/object_info"):
            return _Resp(200, OBJECT_INFO)
        if url.endswith("/system_stats"):
            return _Resp(200, {"system": {}})
        return _Resp(404, {})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)


_SUCCESS_HISTORY = {"pid1": {"status": {"completed": True, "status_str": "success"}}}


# ── GenerateParams view resolution ──────────────────────────────────────────


def test_resolve_views():
    p = Path("x.png")
    assert GenerateParams(image_path=p).resolve_views() == {"front": p}
    assert GenerateParams(views={"front": p, "back": p}).resolve_views() == {
        "front": p,
        "back": p,
    }
    with pytest.raises(ValueError):
        GenerateParams().resolve_views()


# ── graph construction from /object_info defaults ───────────────────────────


def test_graph_all_four_views():
    b = _backend()
    uploads = {label: f"tag_{label}.png" for label in ("front", "back", "left", "right")}
    g = b._build_graph(uploads, "tagX", GenerateParams(output_dir=Path("."), max_tris=42000, seed=7))

    # §2.3 measured overrides on LoadModel
    m = g["model"]["inputs"]
    assert m["modelname"] == "visualbruno/TRELLIS.2-4B-FP8"
    assert m["backend"] == "sdpa"
    assert m["device"] == "cuda"
    assert m["low_vram"] is True
    assert m["keep_models_loaded"] is True
    assert m["conv_backend"] == "flex_gemm"
    assert m["sparse_backend"] == "xformers"
    assert m["use_reconviagen"] is False

    # per-view load + preprocess, remove_background forced on (§2.4)
    for label in ("front", "back", "left", "right"):
        assert g[f"load_{label}"]["inputs"]["image"] == f"tag_{label}.png"
        assert g[f"pre_{label}"]["inputs"]["image"] == [f"load_{label}", 0]
        assert g[f"pre_{label}"]["inputs"]["remove_background"] is True
        assert g[f"pre_{label}"]["inputs"]["max_size"] == 2048  # default filled

    gen = g["generate"]["inputs"]
    assert gen["pipeline"] == ["model", 0]
    assert gen["front_image"] == ["pre_front", 0]
    assert gen["back_image"] == ["pre_back", 0]
    assert gen["left_image"] == ["pre_left", 0]
    assert gen["right_image"] == ["pre_right", 0]
    assert gen["pipeline_type"] == "512"
    assert gen["front_axis"] == "z"
    assert gen["seed"] == 7
    assert gen["fill_holes"] is True  # untouched default

    assert g["simplify"]["inputs"]["target_face_num"] == 42000
    assert g["simplify"]["inputs"]["method"] == "Cumesh"
    assert g["quad"]["inputs"]["mesh"] == ["generate", 0]
    assert g["fill"]["inputs"]["mesh"] == ["simplify", 0]
    assert g["totrim"]["inputs"]["mesh"] == ["fill", 0]
    assert g["totrim"]["inputs"]["reorient_vertices"] == "90 degrees"

    tex = g["texture"]["inputs"]
    assert tex["trimesh"] == ["totrim", 0]
    assert tex["pipeline"] == ["model", 0]
    assert tex["front_image"] == ["pre_front", 0]
    assert tex["back_image"] == ["pre_back", 0]
    assert tex["seed"] == 7
    assert tex["resolution"] == 1024
    assert tex["texture_size"] == 4096

    exp = g["export"]["inputs"]
    assert exp["trimesh"] == ["texture", 0]
    assert exp["file_format"] == "glb"  # combo with no default → first choice
    assert exp["filename_prefix"] == "img3d/tagX"


def test_graph_front_only_omits_optional_views():
    b = _backend()
    g = b._build_graph({"front": "f.png"}, "t", GenerateParams(output_dir=Path(".")))
    for absent in ("load_back", "load_left", "load_right", "pre_back", "pre_left", "pre_right"):
        assert absent not in g
    for node_key in ("generate", "texture"):
        for slot in ("back_image", "left_image", "right_image"):
            assert slot not in g[node_key]["inputs"]


def test_graph_seed_defaults_from_object_info():
    b = _backend()
    g = b._build_graph({"front": "f.png"}, "t", GenerateParams(output_dir=Path(".")))
    assert g["generate"]["inputs"]["seed"] == 12345
    assert g["texture"]["inputs"]["seed"] == 0


def test_graph_keep_loaded_env_override(monkeypatch):
    monkeypatch.setenv("IMG3D_COMFY_KEEP_LOADED", "false")
    b = ComfyTrellis2Backend()
    b._ready = True
    b._object_info_cache = OBJECT_INFO
    g = b._build_graph({"front": "f.png"}, "t", GenerateParams(output_dir=Path(".")))
    assert g["model"]["inputs"]["keep_models_loaded"] is False


def test_graph_node_literals_escape_hatch():
    b = _backend()
    params = GenerateParams(
        output_dir=Path("."),
        extra={"node_literals": {"texture": {"texture_size": 2048}, "generate": {"cfg_strength": 4.0}}},
    )
    g = b._build_graph({"front": "f.png"}, "t", params)
    assert g["texture"]["inputs"]["texture_size"] == 2048
    assert g["generate"]["inputs"]["cfg_strength"] == 4.0


def test_graph_node_literals_unknown_node():
    b = _backend()
    params = GenerateParams(output_dir=Path("."), extra={"node_literals": {"nope": {}}})
    with pytest.raises(RuntimeError, match="unknown node 'nope'"):
        b._build_graph({"front": "f.png"}, "t", params)


def test_build_node_requires_link_for_undefaulted_data_input():
    info = {"X": {"input": {"required": {"mesh": ("MESHWITHVOXEL",)}}}}
    with pytest.raises(RuntimeError, match="must set it explicitly"):
        _build_node(info, "X", links={}, literals={})


def test_build_node_fills_combo_first_choice_without_default():
    info = {"X": {"input": {"required": {"file_format": (["glb", "obj"],)}}}}
    node = _build_node(info, "X", links={}, literals={})
    assert node["inputs"]["file_format"] == "glb"


# ── generate() over the fake HTTP flow ──────────────────────────────────────


def test_generate_end_to_end(tmp_path, monkeypatch):
    import trimesh

    b = _backend()
    b._out_dir = tmp_path / "comfyout"
    export_dir = b._out_dir / "img3d"
    export_dir.mkdir(parents=True)
    export = export_dir / f"img3d_{FAKE_HEX[:10]}_00000_.glb"
    box = trimesh.creation.box(extents=(0.2, 0.2, 0.2))
    box.export(export)

    uploads: list[str] = []
    _fake_http(monkeypatch, history=_SUCCESS_HISTORY, uploads=uploads)
    monkeypatch.setattr(uuid, "uuid4", lambda: types.SimpleNamespace(hex=FAKE_HEX))

    views = {
        "front": _img(tmp_path, "front.png"),
        "back": _img(tmp_path, "back.png"),
        "left": _img(tmp_path, "left.png"),
        "right": _img(tmp_path, "right.png"),
    }
    out = b.generate(GenerateParams(views=views, output_dir=tmp_path / "out", max_tris=50000, seed=3))

    assert out.tri_count == 12  # the box we planted as the export
    assert out.glb_path.name == f"img3d_{FAKE_HEX[:10]}.glb"
    assert out.glb_path.exists()
    # every labelled view was uploaded to ComfyUI's input dir
    assert sorted(Path(u).name for u in uploads) == [
        f"img3d_{FAKE_HEX[:10]}_back.png",
        f"img3d_{FAKE_HEX[:10]}_front.png",
        f"img3d_{FAKE_HEX[:10]}_left.png",
        f"img3d_{FAKE_HEX[:10]}_right.png",
    ]


def test_generate_requires_front_view(tmp_path):
    b = _backend()
    with pytest.raises(RuntimeError, match="front"):
        b.generate(GenerateParams(views={"back": _img(tmp_path, "b.png")}, output_dir=tmp_path))


def test_generate_rejects_unknown_view_label(tmp_path):
    b = _backend()
    img = _img(tmp_path, "f.png")
    with pytest.raises(RuntimeError, match="unknown view labels"):
        b.generate(GenerateParams(views={"front": img, "top": img}, output_dir=tmp_path))


def test_generate_execution_error_reported(tmp_path, monkeypatch):
    b = _backend()
    history = {"pid1": {"status": {
        "completed": True,
        "status_str": "error",
        "messages": [["execution_error", {
            "node_type": "Trellis2LoadModel", "node_id": "model",
            "exception_type": "RuntimeError", "exception_message": "boom",
        }]],
    }}}
    _fake_http(monkeypatch, history=history)
    monkeypatch.setattr(uuid, "uuid4", lambda: types.SimpleNamespace(hex=FAKE_HEX))
    with pytest.raises(RuntimeError, match=r"Trellis2LoadModel.*boom"):
        b.generate(GenerateParams(views={"front": _img(tmp_path, "f.png")}, output_dir=tmp_path))


def test_generate_poll_timeout(tmp_path, monkeypatch):
    import providers.comfy_trellis2 as mod

    b = _backend()
    b._timeout = 0.3
    monkeypatch.setattr(mod, "POLL_INTERVAL_S", 0.05)
    _fake_http(monkeypatch, history={"pid1": {"status": {"completed": False, "status_str": "running"}}})
    monkeypatch.setattr(uuid, "uuid4", lambda: types.SimpleNamespace(hex=FAKE_HEX))
    with pytest.raises(RuntimeError, match="did not complete within"):
        b.generate(GenerateParams(views={"front": _img(tmp_path, "f.png")}, output_dir=tmp_path))


def test_generate_prompt_rejected(tmp_path, monkeypatch):
    b = _backend()
    _fake_http(monkeypatch, history=_SUCCESS_HISTORY, prompt_status=400)
    monkeypatch.setattr(uuid, "uuid4", lambda: types.SimpleNamespace(hex=FAKE_HEX))
    with pytest.raises(RuntimeError, match="rejected the graph"):
        b.generate(GenerateParams(views={"front": _img(tmp_path, "f.png")}, output_dir=tmp_path))


# ── unload (§4.0) ───────────────────────────────────────────────────────────


def test_unload_posts_free(monkeypatch):
    free_calls: list[dict] = []
    _fake_http(monkeypatch, history=_SUCCESS_HISTORY, free_calls=free_calls)
    b = _backend()
    b.unload()
    assert free_calls == [{"unload_models": True, "free_memory": True}]


def test_unload_noop_when_server_down(monkeypatch):
    def dead_get(url, **kw):
        return _Resp(503, {})

    monkeypatch.setattr(httpx, "get", dead_get)
    b = _backend()
    b.unload()  # must not raise


# ── registry ────────────────────────────────────────────────────────────────


def test_backend_registered():
    from providers import BACKENDS

    assert "comfy_trellis2" in BACKENDS
    assert BACKENDS["comfy_trellis2"] is ComfyTrellis2Backend
