"""TRELLIS 2 image-to-3D via a local ComfyUI (GLM_PROMPT_NEURAL_INTAKE.md §4.2).

Drives the measured multi-view graph (work order §2.4) over ComfyUI's HTTP
API — no torch in the service venv; the GPU work happens inside the
ComfyUI process (its own Python 3.11 venv):

    POST /prompt          submit an API-format graph
                          {"prompt": {node_id: {class_type, inputs}}, "client_id"}
    GET  /history/{id}    poll until status.completed
    GET  /object_info     node schemas — literal inputs are built from these
                          defaults (work order: the saved UI workflow has
                          FEWER widget values than the current nodes have
                          inputs; positional mapping silently mis-assigns)
    POST /upload/image    images must land in ComfyUI's input dir first
                          (multipart field "image", overwrite=true)
    POST /free            unload models + free VRAM (§4.0 unload())

Graph (§2.4, literal values from /object_info defaults, overridden only
where §2.3/§2.4 measured differently):

    Trellis2LoadModel(modelname="visualbruno/TRELLIS.2-4B-FP8", backend="sdpa",
                      device="cuda", low_vram=True, conv_backend="flex_gemm",
                      sparse_backend="xformers", use_reconviagen=False,
                      keep_models_loaded=<env, default True — §4.0 measures both>)
    per present view: Trellis2LoadImageWithTransparency
                      → Trellis2PreProcessImage(remove_background=True)
    Trellis2MeshWithVoxelMultiViewGenerator(pipeline_type="512", front_axis="z",
                      front_image required; back/left/right optional)
    → Trellis2RemeshWithQuad
    → Trellis2SimplifyMesh(target_face_num=max_tris)
    → Trellis2FillHolesWithMeshlib
    → Trellis2MeshWithVoxelToTrimesh
    → Trellis2MeshTexturingMultiView(resolution=1024, texture_size=4096,
                      same view images)
    → Trellis2ExportMesh(file_format="glb", filename_prefix="img3d/<tag>")

TRELLIS 2 accepts NO text prompt — the prompt is consumed entirely by the
intake/JobCard/conform side, never by the mesh generator.

Output retrieval: Trellis2ExportMesh is an OUTPUT_NODE that writes
{prefix}_{counter:05}_.glb under ComfyUI's output dir and returns plain
strings (no ui dict), so /history carries nothing usable. The provider
gives every job a unique filename_prefix and reads the file from disk
(same machine by design), then copies it into params.output_dir.

target_size_m is deliberately NOT applied here: §4.4 conform owns sizing
and must REFUSE on aspect mismatch — a provider that pre-distorted the
mesh to the target would erase exactly the mismatch that step exists to
catch. The delivery harness re-scales to the part's target_size anyway.

Env knobs:
  IMG3D_COMFYUI_URL       remote/adopt mode — talk to an already-running
                          ComfyUI (wins over spawning)
  IMG3D_COMFYUI_DIR       ComfyUI root for spawn mode (default
                          D:\\Work\\AI_Tools\\ComfyUI — this machine's install)
  IMG3D_COMFYUI_PORT      default 8189
  IMG3D_COMFYUI_OUTPUT    output dir override (default <dir>/output; set
                          this if ComfyUI runs with --output-directory)
  IMG3D_COMFY_MODEL       default visualbruno/TRELLIS.2-4B-FP8
  IMG3D_COMFY_KEEP_LOADED default true (LoadModel keep_models_loaded);
                          §4.0 measures true vs false — unload() POSTs
                          /free either way
  IMG3D_COMFY_TIMEOUT_S   generation poll timeout, default 1800 (measured
                          wall: up to 678 s + 220 s texturing)
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .base import GenerateOutput, GenerateParams, NeuralBackend, decimate_to_budget

REQUIRED_NODES = [
    "Trellis2LoadModel",
    "Trellis2LoadImageWithTransparency",
    "Trellis2PreProcessImage",
    "Trellis2MeshWithVoxelMultiViewGenerator",
    "Trellis2RemeshWithQuad",
    "Trellis2SimplifyMesh",
    "Trellis2FillHolesWithMeshlib",
    "Trellis2MeshWithVoxelToTrimesh",
    "Trellis2MeshTexturingMultiView",
    "Trellis2ExportMesh",
]

VIEW_SLOTS = ("front", "back", "left", "right")

# This machine's install; override with IMG3D_COMFYUI_DIR elsewhere.
DEFAULT_COMFYUI_DIR = r"D:\Work\AI_Tools\ComfyUI"
DEFAULT_PORT = 8189
DEFAULT_MODEL = "visualbruno/TRELLIS.2-4B-FP8"
HEALTH_TIMEOUT_S = 3.0
SPAWN_READY_TIMEOUT_S = 300.0  # ComfyUI imports dozens of custom nodes at boot
POLL_INTERVAL_S = 2.0
DEFAULT_GENERATE_TIMEOUT_S = 1800.0
UPLOAD_TIMEOUT_S = 120.0

_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _default_literal(class_name: str, in_name: str, in_spec: Any) -> Any:
    """The literal value for an unlinked required input, from /object_info."""
    if not isinstance(in_spec, (list, tuple)) or len(in_spec) < 1:
        raise RuntimeError(
            f"unexpected /object_info spec for {class_name}.{in_name}: {in_spec!r} — "
            "ComfyUI schema drift; the provider must set this input explicitly"
        )
    type_decl = in_spec[0]
    config = in_spec[1] if len(in_spec) > 1 and isinstance(in_spec[1], dict) else {}
    if "default" in config:
        return config["default"]
    if isinstance(type_decl, (list, tuple)) and len(type_decl) > 0:
        return type_decl[0]  # combo with no default: first choice
    raise RuntimeError(
        f"{class_name}.{in_name} has neither a default nor choices — "
        "the provider must set it explicitly (an unlinked data-type input?)"
    )


def _build_node(
    object_info: dict,
    class_name: str,
    links: dict[str, tuple[str, int]],
    literals: dict[str, Any],
) -> dict:
    """One API-format node: link inputs as [node_id, slot]; literal inputs
    from `literals` (the measured overrides) or /object_info defaults.
    Optional inputs that are neither linked nor overridden are omitted
    (ComfyUI treats an absent optional input as None)."""
    spec = object_info.get(class_name)
    if spec is None:
        raise RuntimeError(f"node class {class_name} missing from /object_info")
    inputs: dict[str, Any] = {}
    for section in ("required", "optional"):
        section_inputs = (spec.get("input") or {}).get(section) or {}
        for in_name, in_spec in section_inputs.items():
            if in_name in links:
                inputs[in_name] = [links[in_name][0], links[in_name][1]]
            elif in_name in literals:
                inputs[in_name] = literals[in_name]
            elif section == "required":
                inputs[in_name] = _default_literal(class_name, in_name, in_spec)
    return {"class_type": class_name, "inputs": inputs}


class ComfyTrellis2Backend(NeuralBackend):
    name = "comfy_trellis2"

    def __init__(self, models_dir=None, device: str = "cuda"):
        # models_dir unused (weights live inside ComfyUI's models dir);
        # accepted for registry symmetry like trellis.cpp.
        self.device = device
        self._url = (os.environ.get("IMG3D_COMFYUI_URL") or "").rstrip("/") or None
        self._dir = Path(os.environ.get("IMG3D_COMFYUI_DIR") or DEFAULT_COMFYUI_DIR)
        self._port = int(os.environ.get("IMG3D_COMFYUI_PORT", str(DEFAULT_PORT)))
        self._out_dir = Path(os.environ.get("IMG3D_COMFYUI_OUTPUT") or (self._dir / "output"))
        self._model = os.environ.get("IMG3D_COMFY_MODEL", DEFAULT_MODEL)
        self._keep_loaded = os.environ.get("IMG3D_COMFY_KEEP_LOADED", "true").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        self._timeout = float(os.environ.get("IMG3D_COMFY_TIMEOUT_S", DEFAULT_GENERATE_TIMEOUT_S))
        venv_bin = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        self._venv_python = self._dir / "venv311" / venv_bin
        self._client_id = uuid.uuid4().hex
        self._object_info_cache: dict | None = None
        self._proc: subprocess.Popen | None = None
        self._log_fh = None
        self._spawned = False
        self._ready = False

    @property
    def _base_url(self) -> str:
        return self._url or f"http://127.0.0.1:{self._port}"

    # ── availability / lifecycle ────────────────────────────────────────────

    def _health_ok(self, timeout: float = HEALTH_TIMEOUT_S) -> bool:
        try:
            r = httpx.get(f"{self._base_url}/system_stats", timeout=timeout)
            return r.status_code == 200
        except Exception:
            return False

    def _node_classes(self) -> dict:
        if self._object_info_cache is None:
            try:
                r = httpx.get(f"{self._base_url}/object_info", timeout=60.0)
                r.raise_for_status()
                self._object_info_cache = r.json()
            except Exception as e:
                raise RuntimeError(f"ComfyUI /object_info failed: {e}") from e
        return self._object_info_cache

    def _missing_nodes(self) -> list[str]:
        try:
            info = self._node_classes()
        except RuntimeError:
            return []
        return [c for c in REQUIRED_NODES if c not in info]

    def is_available(self) -> tuple[bool, str]:
        if self._health_ok():
            missing = self._missing_nodes()
            if missing:
                return False, (
                    f"ComfyUI healthy at {self._base_url} but Trellis2 nodes missing: "
                    f"{', '.join(missing)} — install ComfyUI-Trellis2 into its custom_nodes"
                )
            return True, f"ComfyUI healthy at {self._base_url} with Trellis2 nodes"
        if self._url:
            return False, (
                f"no ComfyUI at {self._url} — start it, or unset IMG3D_COMFYUI_URL "
                "to let the backend spawn one"
            )
        if not self._dir.is_dir():
            return False, f"ComfyUI dir not found at {self._dir} — set IMG3D_COMFYUI_DIR"
        if not self._venv_python.is_file():
            return False, f"ComfyUI venv python not found at {self._venv_python}"
        return True, f"ready (managed spawn on 127.0.0.1:{self._port} from {self._dir})"

    def load(self) -> None:
        if self._health_ok():
            self._ready = True  # remote mode, or a ComfyUI already on the port (adopted)
            return
        if self._url:
            raise RuntimeError(f"ComfyUI not reachable at {self._url}")
        if not self._dir.is_dir():
            raise RuntimeError(f"ComfyUI dir not found at {self._dir} — set IMG3D_COMFYUI_DIR")
        if not self._venv_python.is_file():
            raise RuntimeError(f"ComfyUI venv python not found at {self._venv_python}")
        self._spawn_server()
        self._ready = True

    def _spawn_server(self) -> None:
        log_path = self._dir / "img3d_spawn.log"
        log_fh = open(log_path, "ab")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        cmd = [
            str(self._venv_python),
            "main.py",
            "--port",
            str(self._port),
            "--disable-auto-launch",
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=str(self._dir),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        deadline = time.monotonic() + SPAWN_READY_TIMEOUT_S
        try:
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"ComfyUI exited at startup (rc={proc.returncode}; log: {log_path})"
                    )
                if self._health_ok(timeout=1.0):
                    self._proc, self._log_fh, self._spawned = proc, log_fh, True
                    atexit.register(self.shutdown)
                    return
                time.sleep(0.5)
            raise RuntimeError(
                f"ComfyUI not healthy within {SPAWN_READY_TIMEOUT_S:.0f}s (log: {log_path})"
            )
        except Exception:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
            log_fh.close()
            raise

    def shutdown(self) -> None:
        """Terminate a ComfyUI process WE spawned; adopted servers stay up."""
        proc, self._proc = self._proc, None
        fh, self._log_fh = self._log_fh, None
        self._spawned = False
        self._ready = False
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        if fh is not None:
            fh.close()

    def unload(self) -> None:
        """§4.0: POST /free — unload models + release VRAM so the card is
        free for a Blender bake. Affects the whole ComfyUI process (all
        loaded models, not just ours) — that is the point."""
        if not self._health_ok():
            return  # nothing resident we can talk to
        try:
            r = httpx.post(
                f"{self._base_url}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=120.0,
            )
        except httpx.HTTPError as e:
            raise RuntimeError(f"ComfyUI /free failed: {e}") from e
        if r.status_code != 200:
            raise RuntimeError(f"ComfyUI /free failed ({r.status_code}): {r.text[:300]}")

    # ── generation ──────────────────────────────────────────────────────────

    def generate(self, params: GenerateParams) -> GenerateOutput:
        if not self._ready:
            self.load()
        started = time.perf_counter()
        assert params.output_dir is not None
        params.output_dir.mkdir(parents=True, exist_ok=True)

        views = {label: Path(p) for label, p in params.resolve_views().items()}
        if "front" not in views:
            raise RuntimeError("comfy_trellis2 needs a front view")
        unknown = [label for label in views if label not in VIEW_SLOTS]
        if unknown:
            raise RuntimeError(f"unknown view labels {unknown} — expected {list(VIEW_SLOTS)}")

        tag = f"img3d_{uuid.uuid4().hex[:10]}"
        uploads = self._upload_views(views, tag)
        graph = self._build_graph(uploads, tag, params)
        prompt_id = self._submit(graph)
        self._await_completion(prompt_id)
        src = self._find_export(tag)
        out_path = params.output_dir / f"{tag}.glb"
        shutil.copyfile(src, out_path)

        import trimesh

        mesh = trimesh.load(out_path, file_type="glb", force="mesh", process=True)
        if len(mesh.faces) == 0:
            raise RuntimeError("ComfyUI export contains no faces")
        tri_count = int(len(mesh.faces))
        if tri_count > params.max_tris:
            # SimplifyMesh already targets max_tris in-graph; this only fires
            # when the exporter overshot AND re-export keeps the file honest.
            mesh = decimate_to_budget(mesh, params.max_tris, self.name)
            mesh.export(out_path)
            tri_count = int(len(mesh.faces))

        return GenerateOutput(
            glb_path=out_path,
            tri_count=tri_count,
            duration_sec=time.perf_counter() - started,
        )

    # ── graph plumbing ──────────────────────────────────────────────────────

    def _upload_views(self, views: dict[str, Path], tag: str) -> dict[str, str]:
        """Upload each view into ComfyUI's input dir; returns label → the
        filename ComfyUI assigned (what LoadImageWithTransparency wants)."""
        uploaded: dict[str, str] = {}
        for label, path in views.items():
            if not path.is_file():
                raise RuntimeError(f"view image missing: {path}")
            mime = _MIME.get(path.suffix.lower(), "image/png")
            name = f"{tag}_{label}{path.suffix.lower()}"
            try:
                r = httpx.post(
                    f"{self._base_url}/upload/image",
                    files={"image": (name, path.read_bytes(), mime)},
                    data={"overwrite": "true"},
                    timeout=UPLOAD_TIMEOUT_S,
                )
            except httpx.HTTPError as e:
                raise RuntimeError(f"ComfyUI image upload failed ({label}): {e}") from e
            if r.status_code != 200:
                raise RuntimeError(
                    f"ComfyUI image upload failed ({label}, {r.status_code}): {r.text[:300]}"
                )
            body = r.json()
            subfolder, fname = body.get("subfolder", ""), body.get("name", name)
            uploaded[label] = f"{subfolder}/{fname}" if subfolder else fname
        return uploaded

    def _build_graph(self, uploads: dict[str, str], tag: str, params: GenerateParams) -> dict:
        info = self._node_classes()
        missing = [c for c in REQUIRED_NODES if c not in info]
        if missing:
            raise RuntimeError(f"Trellis2 nodes missing from ComfyUI: {', '.join(missing)}")

        def node(class_name: str, links=None, literals=None) -> dict:
            return _build_node(info, class_name, links or {}, literals or {})

        # §2.3 measured overrides (fp8 fits 16 GB; flash_attn is NOT
        # installed in this ComfyUI — the saved workflow's value would crash).
        model_literals = {
            "modelname": self._model,
            "backend": "sdpa",
            "device": self.device,
            "low_vram": True,
            "keep_models_loaded": self._keep_loaded,
            "conv_backend": "flex_gemm",
            "sparse_backend": "xformers",
            "use_reconviagen": False,
        }

        g: dict[str, dict] = {}
        g["model"] = node("Trellis2LoadModel", literals=model_literals)
        for label in VIEW_SLOTS:
            if label not in uploads:
                continue
            g[f"load_{label}"] = node(
                "Trellis2LoadImageWithTransparency", literals={"image": uploads[label]}
            )
            g[f"pre_{label}"] = node(
                "Trellis2PreProcessImage",
                links={"image": (f"load_{label}", 0)},
                literals={"remove_background": True},
            )

        gen_literals: dict[str, Any] = {"pipeline_type": "512", "front_axis": "z"}
        if params.seed is not None:
            gen_literals["seed"] = int(params.seed)
        gen_links: dict[str, tuple[str, int]] = {
            "pipeline": ("model", 0),
            "front_image": ("pre_front", 0),
        }
        for label in ("back", "left", "right"):
            if label in uploads:
                gen_links[f"{label}_image"] = (f"pre_{label}", 0)
        g["generate"] = node(
            "Trellis2MeshWithVoxelMultiViewGenerator", links=gen_links, literals=gen_literals
        )

        g["quad"] = node("Trellis2RemeshWithQuad", links={"mesh": ("generate", 0)})
        g["simplify"] = node(
            "Trellis2SimplifyMesh",
            links={"mesh": ("quad", 0)},
            literals={"target_face_num": int(params.max_tris)},
        )
        g["fill"] = node("Trellis2FillHolesWithMeshlib", links={"mesh": ("simplify", 0)})
        g["totrim"] = node("Trellis2MeshWithVoxelToTrimesh", links={"mesh": ("fill", 0)})

        tex_literals: dict[str, Any] = {}
        if params.seed is not None:
            tex_literals["seed"] = int(params.seed)
        tex_links: dict[str, tuple[str, int]] = {
            "pipeline": ("model", 0),
            "front_image": ("pre_front", 0),
            "trimesh": ("totrim", 0),
        }
        for label in ("back", "left", "right"):
            if label in uploads:
                tex_links[f"{label}_image"] = (f"pre_{label}", 0)
        g["texture"] = node(
            "Trellis2MeshTexturingMultiView", links=tex_links, literals=tex_literals
        )

        g["export"] = node(
            "Trellis2ExportMesh",
            links={"trimesh": ("texture", 0)},
            literals={"file_format": "glb", "filename_prefix": f"img3d/{tag}"},
        )

        # escape hatch for node inputs the measured graph didn't need to pin:
        # params.extra["node_literals"] = {"texture": {"texture_size": 2048}, ...}
        for node_key, literals in (params.extra.get("node_literals") or {}).items():
            if node_key not in g:
                raise RuntimeError(f"node_literals override for unknown node '{node_key}'")
            g[node_key]["inputs"].update(literals)
        return g

    def _submit(self, graph: dict) -> str:
        try:
            r = httpx.post(
                f"{self._base_url}/prompt",
                json={"prompt": graph, "client_id": self._client_id},
                timeout=60.0,
            )
        except httpx.HTTPError as e:
            raise RuntimeError(f"ComfyUI /prompt request failed: {e}") from e
        if r.status_code == 400:
            try:
                detail = r.json()
            except Exception:
                detail = r.text[:1000]
            raise RuntimeError(f"ComfyUI rejected the graph (400): {detail}")
        if r.status_code != 200:
            raise RuntimeError(f"ComfyUI /prompt failed ({r.status_code}): {r.text[:500]}")
        prompt_id = r.json().get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI /prompt returned no prompt_id: {r.text[:300]}")
        return prompt_id

    def _await_completion(self, prompt_id: str) -> None:
        deadline = time.monotonic() + self._timeout
        last_note = "no history response yet"
        while time.monotonic() < deadline:
            try:
                r = httpx.get(f"{self._base_url}/history/{prompt_id}", timeout=15.0)
                if r.status_code == 200:
                    entry = r.json().get(prompt_id)
                    if entry is not None:
                        status = entry.get("status") or {}
                        if status.get("completed"):
                            if status.get("status_str") == "error":
                                raise RuntimeError(
                                    f"ComfyUI execution failed: {self._execution_error(entry)}"
                                )
                            return
                        last_note = f"status {status.get('status_str', 'running')}"
                else:
                    last_note = f"history HTTP {r.status_code}"
            except httpx.HTTPError as e:
                last_note = str(e)
            time.sleep(POLL_INTERVAL_S)
        raise RuntimeError(
            f"ComfyUI job {prompt_id} did not complete within {self._timeout:.0f}s "
            f"(last: {last_note})"
        )

    @staticmethod
    def _execution_error(entry: dict) -> str:
        for msg in entry.get("status", {}).get("messages", []):
            if isinstance(msg, (list, tuple)) and len(msg) >= 2 and msg[0] == "execution_error":
                d = msg[1]
                return (
                    f"{d.get('node_type')} (node {d.get('node_id')}): "
                    f"{d.get('exception_type')}: {d.get('exception_message')}"
                )
        return "unknown error (no execution_error message in history)"

    def _find_export(self, tag: str) -> Path:
        """Trellis2ExportMesh writes {prefix}_{counter:05}_.glb under
        <comfy output>/img3d/; the tag is unique per job so exactly one
        file matches. Short retry — the file is written before history
        completion, but don't trust that ordering."""
        search_dir = self._out_dir / "img3d"
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if search_dir.is_dir():
                matches = sorted(search_dir.glob(f"{tag}_*.glb"))
                if matches:
                    return matches[-1]
            time.sleep(0.5)
        raise RuntimeError(f"ComfyUI export not found: {search_dir / (tag + '_*.glb')}")
