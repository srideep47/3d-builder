"""TRELLIS.2 backend tests — against a stub trellis-server (the trellis.cpp
HTTP contract), GPU-free and torch-free. The real server needs the GGUF set
from scripts/setup-trellis-cpp.ps1 and a free GPU; these tests pin the wire
contract (multipart fields, GLB response, error propagation) and the
backend's own behavior: remote/spawn modes, adopt-don't-double-spawn,
scale-to-target post-processing, decimation guard, and texture-preserving
pass-through.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = PROJECT_ROOT / "services" / "img3d_service"
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from providers.base import GenerateParams  # noqa: E402
from providers.trellis import REQUIRED_GGUF, TrellisBackend  # noqa: E402

GLB_MAGIC = b"glTF"


# ── Stub trellis-server (contract from trellis.cpp v0.6.0) ──────────────────


def _make_glb() -> bytes:
    import trimesh

    mesh = trimesh.creation.icosphere(subdivisions=2, radius=0.5)
    buf = BytesIO()
    mesh.export(buf, file_type="glb")
    return buf.getvalue()


_STUB_GLB = _make_glb()  # fixed payload: pass-through comparisons are exact


class _StubState:
    fields: list[dict] = []
    files: list[dict] = []
    mode = "ok"  # ok | error500


def _parse_multipart(body: bytes, content_type: str) -> tuple[dict, dict]:
    """Minimal multipart/form-data parser for the stub (httpx-shaped input)."""
    if "boundary=" not in content_type:
        return {}, {}
    boundary = content_type.split("boundary=", 1)[1].strip().strip('"').encode()
    fields: dict[str, str] = {}
    files: dict[str, bytes] = {}
    for part in body.split(b"--" + boundary):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        header_blob, _, content = part.partition(b"\r\n\r\n")
        disposition = ""
        for line in header_blob.split(b"\r\n"):
            key, _, value = line.partition(b":")
            if key.decode().strip().lower() == "content-disposition":
                disposition = value.decode()
        name = None
        for token in disposition.split(";"):
            token = token.strip()
            if token.startswith("name="):
                name = token[5:].strip('"')
        if name is None:
            continue
        if "filename=" in disposition:
            files[name] = content
        else:
            fields[name] = content.decode()
    return fields, files


class _StubHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._reply(200, b"ok", "text/plain")
        else:
            self._reply(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path != "/generate":
            self._reply(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", "0"))
        fields, files = _parse_multipart(self.rfile.read(length), self.headers.get("Content-Type", ""))
        _StubState.fields.append(fields)
        _StubState.files.append(files)
        if _StubState.mode == "error500":
            self._reply(500, json.dumps({"error": "pipeline exploded"}).encode(), "application/json")
        elif "image" not in files:
            self._reply(400, json.dumps({"error": "missing 'image' file part"}).encode(), "application/json")
        else:
            self._reply(200, _STUB_GLB, "model/gltf-binary")

    def _reply(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep pytest output clean
        pass


@pytest.fixture()
def stub_server():
    _StubState.fields.clear()
    _StubState.files.clear()
    _StubState.mode = "ok"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture()
def remote_env(stub_server, monkeypatch):
    """Point the backend at the stub in remote mode (IMG3D_TRELLIS_URL)."""
    monkeypatch.setenv("IMG3D_TRELLIS_URL", stub_server)
    return stub_server


def _dead_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _make_image(tmp: Path, name: str = "ref.png") -> Path:
    from PIL import Image

    p = tmp / name
    Image.new("RGB", (32, 32), (180, 120, 60)).save(p)
    return p


# ── Remote mode ─────────────────────────────────────────────────────────────


def test_remote_mode_is_available(remote_env):
    backend = TrellisBackend()
    ok, reason = backend.is_available()
    assert ok, reason
    assert "healthy" in reason


def test_remote_mode_unreachable_reason(monkeypatch):
    monkeypatch.setenv("IMG3D_TRELLIS_URL", f"http://127.0.0.1:{_dead_port()}")
    backend = TrellisBackend()
    ok, reason = backend.is_available()
    assert not ok
    assert "IMG3D_TRELLIS_URL" in reason  # tells the operator how to fix it


def test_generate_posts_contract_fields_and_scales(tmp_path, remote_env):
    img = _make_image(tmp_path)
    img_bytes = img.read_bytes()
    backend = TrellisBackend()

    out = backend.generate(
        GenerateParams(
            image_path=img,
            output_dir=tmp_path / "out",
            target_size_m=[0.3, 0.2, 0.1],
            max_tris=50000,
            seed=7,
        )
    )

    # wire contract: file part "image" + text fields "seed"/"resolution"
    assert _StubState.files[-1]["image"] == img_bytes
    assert _StubState.fields[-1]["seed"] == "7"
    assert _StubState.fields[-1]["resolution"] == "512"  # default light path

    # output contract: GLB on disk, exact per-axis target bounds, honest count
    assert out.glb_path == tmp_path / "out" / "ref_trellis.glb"
    assert out.glb_path.read_bytes()[:4] == GLB_MAGIC
    assert out.duration_sec >= 0.0

    import trimesh

    mesh = trimesh.load(out.glb_path, force="mesh", process=True)
    assert out.tri_count == len(mesh.faces) > 0
    for got, want in zip(mesh.extents, [0.3, 0.2, 0.1]):
        assert abs(got - want) < 0.005


def test_generate_resolution_extra_override(tmp_path, remote_env):
    img = _make_image(tmp_path)
    backend = TrellisBackend()
    out = backend.generate(
        GenerateParams(
            image_path=img,
            output_dir=tmp_path,
            extra={"resolution": 1024},
        )
    )
    assert out.tri_count > 0
    assert _StubState.fields[-1]["resolution"] == "1024"
    assert "seed" not in _StubState.fields[-1]  # seed=None → field omitted


def test_generate_passthrough_keeps_server_bytes(tmp_path, remote_env):
    """No target and under budget → the delivered file is the server's GLB
    bit-for-bit (PBR textures untouched by any re-export)."""
    img = _make_image(tmp_path)
    backend = TrellisBackend()
    out = backend.generate(
        GenerateParams(image_path=img, output_dir=tmp_path, max_tris=100000)
    )
    assert out.glb_path.read_bytes() == _STUB_GLB
    assert out.tri_count > 0


def test_generate_decimation_guard(tmp_path, remote_env):
    """max_tris below the mesh: decimate when fast-simplification is present,
    otherwise skip loudly-tolerated (the budget is enforced downstream) —
    never crash, never report a mesh we didn't deliver."""
    img = _make_image(tmp_path)
    backend = TrellisBackend()
    out = backend.generate(
        GenerateParams(image_path=img, output_dir=tmp_path, max_tris=10)
    )
    assert out.tri_count > 0
    try:
        import fast_simplification  # noqa: F401

        assert out.tri_count <= 10
    except ImportError:
        import trimesh

        reloaded = trimesh.load(out.glb_path, force="mesh", process=True)
        assert out.tri_count == len(reloaded.faces)  # full mesh, honestly counted


def test_generate_propagates_server_error(tmp_path, remote_env):
    _StubState.mode = "error500"
    backend = TrellisBackend()
    with pytest.raises(RuntimeError) as excinfo:
        backend.generate(GenerateParams(image_path=_make_image(tmp_path), output_dir=tmp_path))
    msg = str(excinfo.value)
    assert "failed (500)" in msg  # status code surfaced
    assert "pipeline exploded" in msg  # server's JSON error body surfaced


# ── Spawn mode ──────────────────────────────────────────────────────────────


def _install_marker_trellis(models_root: Path) -> None:
    """Fake a completed setup-trellis-cpp.ps1 run: GGUF markers + a binary
    marker. The marker exe is never executed in these tests."""
    gguf_dir = models_root / "trellis"
    (gguf_dir / "bin").mkdir(parents=True, exist_ok=True)
    for f in REQUIRED_GGUF:
        (gguf_dir / f).write_bytes(b"gguf-marker")
    (gguf_dir / "bin" / "trellis-server.exe").write_bytes(b"MZ-marker")


def test_spawn_mode_gates_on_install(tmp_path, monkeypatch):
    monkeypatch.delenv("IMG3D_TRELLIS_URL", raising=False)
    monkeypatch.setenv("IMG3D_TRELLIS_PORT", str(_dead_port()))
    backend = TrellisBackend(models_dir=tmp_path)  # empty models dir
    ok, reason = backend.is_available()
    assert not ok
    assert "setup-trellis-cpp.ps1" in reason
    with pytest.raises(RuntimeError, match="setup-trellis-cpp.ps1"):
        backend.load()


def test_spawn_mode_partial_weights_fail_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("IMG3D_TRELLIS_URL", raising=False)
    monkeypatch.setenv("IMG3D_TRELLIS_PORT", str(_dead_port()))
    _install_marker_trellis(tmp_path)
    (tmp_path / "trellis" / "tex_flow_1024.gguf").unlink()  # 9 of 10 present
    backend = TrellisBackend(models_dir=tmp_path)
    ok, reason = backend.is_available()
    assert not ok
    assert "1/10" in reason


def test_spawn_mode_ready_after_setup(tmp_path, monkeypatch):
    monkeypatch.delenv("IMG3D_TRELLIS_URL", raising=False)
    monkeypatch.setenv("IMG3D_TRELLIS_PORT", str(_dead_port()))
    _install_marker_trellis(tmp_path)
    backend = TrellisBackend(models_dir=tmp_path)
    ok, reason = backend.is_available()
    assert ok, reason
    assert "spawn" in reason


def test_spawn_mode_adopts_healthy_server(tmp_path, monkeypatch, stub_server):
    """A server already on the spawn port is ADOPTED, never double-spawned —
    the marker exe would fail to execute, so adoption is the only path that
    works here. Adopted servers are not terminated by shutdown()."""
    monkeypatch.delenv("IMG3D_TRELLIS_URL", raising=False)
    monkeypatch.setenv("IMG3D_TRELLIS_PORT", stub_server.rsplit(":", 1)[1])
    _install_marker_trellis(tmp_path)
    backend = TrellisBackend(models_dir=tmp_path)

    backend.load()  # adopt, no spawn
    assert backend._spawned is False and backend._proc is None

    img = _make_image(tmp_path)
    out = backend.generate(GenerateParams(image_path=img, output_dir=tmp_path / "out"))
    assert out.tri_count > 0
    assert _StubState.files[-1]["image"] == img.read_bytes()

    backend.shutdown()  # adopted → nothing to terminate, stub must survive
    assert stub_server  # (fixture still owns it)
    img2 = _make_image(tmp_path, "second.png")
    out2 = backend.generate(GenerateParams(image_path=img2, output_dir=tmp_path / "out"))
    assert out2.tri_count > 0  # server still up after our shutdown()


def test_spawn_command_flags(tmp_path, monkeypatch):
    monkeypatch.delenv("IMG3D_TRELLIS_URL", raising=False)
    monkeypatch.delenv("IMG3D_TRELLIS_PORT", raising=False)
    monkeypatch.setenv("IMG3D_TRELLIS_RES", "1024")
    monkeypatch.setenv("IMG3D_TRELLIS_ARGS", "--gpu 1 --require-gpu")
    backend = TrellisBackend(models_dir=tmp_path)
    cmd = backend._server_command()
    assert cmd[0].endswith("trellis-server.exe")
    assert cmd[cmd.index("--models") + 1] == str(tmp_path / "trellis")
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--port") + 1] == "8712"  # default, no env override
    assert cmd[cmd.index("--res") + 1] == "1024"
    assert cmd[cmd.index("--gpu") + 1] == "1"  # extra raw args appended
    assert "--require-gpu" in cmd


def test_spawn_mode_generate_requires_load(tmp_path, monkeypatch):
    """Direct generate() without a prior load(): cold path must fail closed
    with the setup hint (no server, no weights), never attempt a request."""
    monkeypatch.delenv("IMG3D_TRELLIS_URL", raising=False)
    monkeypatch.setenv("IMG3D_TRELLIS_PORT", str(_dead_port()))
    backend = TrellisBackend(models_dir=tmp_path)
    with pytest.raises(RuntimeError, match="setup-trellis-cpp.ps1"):
        backend.generate(
            GenerateParams(image_path=_make_image(tmp_path), output_dir=tmp_path)
        )
