"""TRELLIS.2 backend — drives the trellis.cpp resident HTTP server.

TRELLIS.2 (microsoft/TRELLIS.2, MIT, 4B-parameter flow-matching image-to-3D
over O-Voxel sparse latents, PBR surface attributes) has no official Windows
package: the reference Python repo is Linux-only (CUDA-toolkit submodules;
nvdiffrast/nvdiffrec carry NVlabs licences). The MIT-licensed C++/GGML port
trellis.cpp ships prebuilt Windows CUDA binaries and a resident HTTP server
that runs the TRELLIS.2-4B weights as GGUF — one image in, one
PBR-textured GLB out, with no Python, no CUDA toolchain, and nothing
installed into either venv. Quality parity with the reference pipeline is
the port's claim; spot-check against the HF demo space
(huggingface.co/spaces/microsoft/TRELLIS.2) before trusting a bake-off win.

Layout (gitignored; created by scripts/setup-trellis-cpp.ps1):
  <models_dir>/trellis/*.gguf   — the 10 GGUF files of the chosen tier
                                  (ilintar/trellis2-gguf: f16 ~16.5 GB,
                                  q8 ~9.5 GB, q4 ~6 GB)
  <models_dir>/trellis/bin/     — trellis-server.exe (+ DLLs) from the
                                  trellis-cuda-windows-x64.zip release asset

Modes (URL env wins):
  remote — IMG3D_TRELLIS_URL set: talk to an already-running server (e.g.
           the owner's Trellis Studio instance, which defaults to :8080).
  spawn  — default: launch trellis-server on 127.0.0.1:<port> ourselves,
           adopt an already-healthy server on that port, and terminate ONLY
           the process we spawned at interpreter exit (adopted servers,
           including the Studio's, stay up).

Env knobs: IMG3D_TRELLIS_URL, IMG3D_TRELLIS_BIN (binary path override),
IMG3D_TRELLIS_PORT (default 8712), IMG3D_TRELLIS_MODELS (GGUF dir override),
IMG3D_TRELLIS_RES (default "512" — the light single-resolution path; "1024"
selects the cascade, which the port also reports as runnable on 16 GB),
IMG3D_TRELLIS_ARGS (extra raw server flags, split on spaces — e.g.
"--gpu 1 --require-gpu").

Server contract (pinned from trellis.cpp v0.6.0, src/trellis-server.cpp):
GET /health → 200 body "ok" (text/plain). POST /generate, multipart with
file part "image" plus text fields "seed", "resolution", "bg_removal",
"uv", "band", "webp" → 200 GLB bytes (model/gltf-binary), 400/500 JSON
{"error": ...}. The server's own defaults (auto background removal with
BiRefNet, xatlas UVs, per-resolution decimation) are deliberately left
untouched. The server has no shutdown endpoint — process control is ours.
"""

from __future__ import annotations

import atexit
import os
import subprocess
import time
from io import BytesIO
from pathlib import Path

import httpx

from .base import GenerateOutput, GenerateParams, NeuralBackend, decimate_to_budget

# ilintar/trellis2-gguf ships exactly these ten files per tier; the server
# cannot generate without the full set, so availability probes for it.
REQUIRED_GGUF = [
    "ss_flow.gguf",
    "ss_dec.gguf",
    "shape_flow_512.gguf",
    "shape_flow_1024.gguf",
    "shape_dec.gguf",
    "tex_flow_512.gguf",
    "tex_flow_1024.gguf",
    "tex_dec.gguf",
    "dinov3.gguf",
    "birefnet.gguf",
]

DEFAULT_PORT = 8712
HEALTH_TIMEOUT_S = 2.0
SPAWN_READY_TIMEOUT_S = 90.0
GENERATE_TIMEOUT_S = 900.0  # first request also loads the GGUF set from disk

_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

_SETUP_HINT = "run scripts/setup-trellis-cpp.ps1"


class TrellisBackend(NeuralBackend):
    name = "trellis"

    def __init__(self, models_dir=None, device: str = "cuda"):
        self.models_dir = (
            Path(models_dir) if models_dir else Path(__file__).resolve().parents[3] / "models"
        )
        # `device` accepted for registry symmetry; trellis.cpp picks GPU 0
        # itself (override via IMG3D_TRELLIS_ARGS "--gpu N").
        self.device = device
        self._url = (os.environ.get("IMG3D_TRELLIS_URL") or "").rstrip("/") or None
        self._port = int(os.environ.get("IMG3D_TRELLIS_PORT", str(DEFAULT_PORT)))
        self._gguf_dir = Path(
            os.environ.get("IMG3D_TRELLIS_MODELS") or (self.models_dir / "trellis")
        )
        exe = "trellis-server.exe" if os.name == "nt" else "trellis-server"
        self._bin = Path(os.environ.get("IMG3D_TRELLIS_BIN") or (self._gguf_dir / "bin" / exe))
        self._res = str(os.environ.get("IMG3D_TRELLIS_RES", "512"))
        self._extra_args = os.environ.get("IMG3D_TRELLIS_ARGS", "").split()
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
            r = httpx.get(f"{self._base_url}/health", timeout=timeout)
            return r.status_code == 200
        except Exception:
            return False

    def _missing_gguf(self) -> list[str]:
        return [f for f in REQUIRED_GGUF if not (self._gguf_dir / f).is_file()]

    def is_available(self) -> tuple[bool, str]:
        if self._health_ok():
            return True, f"trellis-server healthy at {self._base_url}"
        if self._url:
            return False, (
                f"no trellis-server at {self._url} — start it, or unset "
                "IMG3D_TRELLIS_URL to let the backend spawn one"
            )
        missing = self._missing_gguf()
        if missing:
            return False, (
                f"TRELLIS GGUF set incomplete in {self._gguf_dir} "
                f"(missing {len(missing)}/{len(REQUIRED_GGUF)}) — {_SETUP_HINT}"
            )
        if not self._bin.is_file():
            return False, f"trellis-server binary not found at {self._bin} — {_SETUP_HINT}"
        return True, f"ready (managed spawn on 127.0.0.1:{self._port}, res {self._res})"

    def load(self) -> None:
        if self._health_ok():
            self._ready = True  # remote mode, or a server already on the port (adopted)
            return
        if self._url:
            raise RuntimeError(f"trellis-server not reachable at {self._url}")
        missing = self._missing_gguf()
        if missing:
            raise RuntimeError(
                f"TRELLIS GGUF set incomplete in {self._gguf_dir} "
                f"(missing {len(missing)}/{len(REQUIRED_GGUF)}) — {_SETUP_HINT}"
            )
        if not self._bin.is_file():
            raise RuntimeError(f"trellis-server binary not found at {self._bin} — {_SETUP_HINT}")
        self._spawn_server()
        self._ready = True

    def _server_command(self) -> list[str]:
        return [
            str(self._bin),
            "--models",
            str(self._gguf_dir),
            "--host",
            "127.0.0.1",
            "--port",
            str(self._port),
            "--res",
            self._res,
            *self._extra_args,
        ]

    def _spawn_server(self) -> None:
        log_path = self._gguf_dir / "server.log"
        log_fh = open(log_path, "ab")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen(
            self._server_command(),
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
                        f"trellis-server exited at startup (rc={proc.returncode}; log: {log_path})"
                    )
                if self._health_ok(timeout=1.0):
                    self._proc, self._log_fh, self._spawned = proc, log_fh, True
                    atexit.register(self.shutdown)
                    return
                time.sleep(0.25)
            raise RuntimeError(
                f"trellis-server not healthy within {SPAWN_READY_TIMEOUT_S:.0f}s (log: {log_path})"
            )
        except Exception:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            log_fh.close()
            raise

    def shutdown(self) -> None:
        """Terminate a server process WE spawned; adopted servers stay up."""
        proc, self._proc = self._proc, None
        fh, self._log_fh = self._log_fh, None
        self._spawned = False
        self._ready = False
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if fh is not None:
            fh.close()

    # ── generation ──────────────────────────────────────────────────────────

    def generate(self, params: GenerateParams) -> GenerateOutput:
        if not self._ready:
            self.load()
        started = time.perf_counter()
        params.output_dir.mkdir(parents=True, exist_ok=True)

        data = {"resolution": str(params.extra.get("resolution", self._res))}
        if params.seed is not None:
            data["seed"] = str(int(params.seed))
        mime = _MIME.get(params.image_path.suffix.lower(), "image/png")
        try:
            r = httpx.post(
                f"{self._base_url}/generate",
                files={"image": (params.image_path.name, params.image_path.read_bytes(), mime)},
                data=data,
                timeout=GENERATE_TIMEOUT_S,
            )
        except httpx.HTTPError as e:
            raise RuntimeError(f"trellis-server request failed: {e}") from e
        if r.status_code != 200:
            raise RuntimeError(
                f"trellis-server /generate failed ({r.status_code}): {r.text[:500]}"
            )
        if r.content[:4] != b"glTF":
            raise RuntimeError("trellis-server returned a non-GLB payload")

        import trimesh

        mesh = trimesh.load(BytesIO(r.content), file_type="glb", force="mesh", process=True)
        if len(mesh.faces) == 0:
            raise RuntimeError("trellis-server returned an empty mesh")

        out_path = params.output_dir / f"{params.image_path.stem}_trellis.glb"
        target = params.target_size_m
        if not target and len(mesh.faces) <= params.max_tris:
            # pass-through: untouched server bytes keep the baked PBR
            # textures bit-exact (the delivery pipeline re-UVs anyway, but
            # API consumers that only wanted a mesh keep the full material)
            out_path.write_bytes(r.content)
        else:
            mesh = decimate_to_budget(mesh, params.max_tris, "trellis")
            if target:
                extents = mesh.extents
                mesh.apply_scale([target[i] / max(extents[i], 1e-9) for i in range(3)])
            mesh.export(out_path)

        return GenerateOutput(
            glb_path=out_path,
            tri_count=int(len(mesh.faces)),
            duration_sec=time.perf_counter() - started,
        )
