"""GPU lock semantics (§4.0): same-thread reentrancy, cross-thread
exclusion, cross-process exclusion against a real subprocess, timeout →
GpuLockError (stop condition S3), and the two-copy identity pin — the
service venv imports its own gpu_lock.py, so the copies must stay
byte-identical.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from src.neural.gpu_lock import GpuLock, GpuLockError, machine_gpu_lock

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_reentrant_same_thread(tmp_path):
    lock = GpuLock(path=tmp_path / "gpu.lock", timeout_s=5)
    with lock:
        with lock:
            assert lock._depth == 2
    assert lock._depth == 0
    # the file lock is genuinely free afterwards — a second lock object takes it
    other = GpuLock(path=lock.path, timeout_s=1)
    other.acquire()
    other.release()


def test_release_without_acquire(tmp_path):
    lock = GpuLock(path=tmp_path / "gpu.lock")
    with pytest.raises(GpuLockError, match="without a matching acquire"):
        lock.release()


def test_cross_thread_excludes(tmp_path):
    lock = GpuLock(path=tmp_path / "gpu.lock", timeout_s=5)
    held = threading.Event()
    release = threading.Event()

    def holder():
        with lock:
            held.set()
            release.wait(10)

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    assert held.wait(5)
    with pytest.raises(GpuLockError):
        lock.acquire(timeout_s=0.4)  # RLock: blocked by our own other thread
    release.set()
    t.join(10)
    lock.acquire(timeout_s=5)
    lock.release()


def _spawn_holder(path: Path, hold_s: float) -> subprocess.Popen:
    code = (
        "import sys, time\n"
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
        "from src.neural.gpu_lock import GpuLock\n"
        f"lock = GpuLock(path={str(path)!r}, timeout_s=10)\n"
        "lock.acquire()\n"
        "print('HELD', flush=True)\n"
        f"time.sleep({hold_s})\n"
        "lock.release()\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(PROJECT_ROOT),
    )


def test_cross_process_excludes(tmp_path):
    lock = GpuLock(path=tmp_path / "gpu.lock", timeout_s=10)
    proc = _spawn_holder(lock.path, 1.5)
    try:
        line = proc.stdout.readline()
        assert "HELD" in line, f"child did not report holding the lock: {line!r}"
        t0 = time.monotonic()
        lock.acquire(timeout_s=10)  # must wait out the child's 1.5 s hold
        waited = time.monotonic() - t0
        assert waited >= 0.5, f"acquired after only {waited:.2f}s — cross-process exclusion broken"
        lock.release()
    finally:
        proc.wait(timeout=15)


def test_timeout_fails_loud(tmp_path):
    lock = GpuLock(path=tmp_path / "gpu.lock", timeout_s=10)
    proc = _spawn_holder(lock.path, 1.2)
    try:
        line = proc.stdout.readline()
        assert "HELD" in line
        with pytest.raises(GpuLockError, match="GPU lock busy"):
            lock.acquire(timeout_s=0.3)  # stop condition S3: fail loud, never proceed
    finally:
        proc.wait(timeout=15)
    lock.acquire(timeout_s=5)  # and it recovers once the holder exits
    lock.release()


def test_env_timeout_override(tmp_path, monkeypatch):
    monkeypatch.setenv("THREED_GPU_LOCK_TIMEOUT", "0.25")
    lock = GpuLock(path=tmp_path / "g.lock")
    assert lock.timeout_s == 0.25


def test_machine_gpu_lock_context(tmp_path, monkeypatch):
    import src.neural.gpu_lock as gl

    monkeypatch.setenv("THREED_GPU_LOCK", str(tmp_path / "machine.lock"))
    monkeypatch.setattr(gl, "_machine_lock", None)
    try:
        with gl.gpu_lock():
            assert machine_gpu_lock()._depth == 1
            with gl.gpu_lock():  # reentrant through the context manager
                assert machine_gpu_lock()._depth == 2
        assert machine_gpu_lock()._depth == 0
        assert (tmp_path / "machine.lock").exists()
    finally:
        gl._machine_lock = None  # don't leak the tmp-path singleton


def test_gpu_lock_copies_identical():
    main = (PROJECT_ROOT / "src" / "neural" / "gpu_lock.py").read_bytes()
    service = (PROJECT_ROOT / "services" / "img3d_service" / "gpu_lock.py").read_bytes()
    assert main == service, (
        "services/img3d_service/gpu_lock.py drifted from src/neural/gpu_lock.py — "
        "the service venv imports its own copy; keep them byte-identical"
    )


# ── BlenderRunner integration (§4.0 main-env side) ───────────────────────────


def test_needs_gpu_lock_rule():
    from src.blender.runner import _needs_gpu_lock

    assert _needs_gpu_lock("bake_maps", None) is True  # device defaults to auto
    assert _needs_gpu_lock("bake_maps", {"device": "auto"}) is True
    assert _needs_gpu_lock("bake_maps", {"device": "OPTIX"}) is True
    assert _needs_gpu_lock("bake_maps", {"device": "cpu"}) is False
    assert _needs_gpu_lock("render_views", {}) is True
    assert _needs_gpu_lock("bake_materials", {"device": "cuda"}) is True
    assert _needs_gpu_lock("build_from_spec", {"device": "auto"}) is False


def test_runner_holds_machine_lock_around_gpu_ops(monkeypatch, tmp_path):
    """execute_op wraps GPU-capable Cycles ops in the machine lock (shared
    file with the img3d service) and skips it for explicit cpu / non-GPU
    ops; an unobtainable lock fails loud as BlenderExecutionError (S3)."""
    import types

    import src.blender.runner as runner_mod
    import src.neural.gpu_lock as gl
    from src.blender.runner import BlenderRunner, BlenderExecutionError

    monkeypatch.setenv("THREED_GPU_LOCK", str(tmp_path / "runner.lock"))
    monkeypatch.setattr(gl, "_machine_lock", None)
    real_gpu_lock = runner_mod.gpu_lock
    lock_calls: list = []
    held_depths: list[int] = []
    run_calls: list[list] = []

    def recording_lock(timeout_s=None):
        lock_calls.append(timeout_s)
        return real_gpu_lock(timeout_s=timeout_s)

    def fake_run(cmd, **kw):
        held_depths.append(gl.machine_gpu_lock()._depth)
        run_calls.append(cmd)
        return types.SimpleNamespace(
            stdout=runner_mod.SENTINEL_BEGIN + '{"success": true}' + runner_mod.SENTINEL_END,
            stderr="",
            returncode=0,
        )

    try:
        monkeypatch.setattr(runner_mod, "gpu_lock", recording_lock)
        monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)
        r = BlenderRunner(blender_path="fake-blender-for-test")

        r.execute_op("bake_maps", {"device": "auto"})
        assert held_depths == [1], "GPU op must run inside the machine lock"

        r.execute_op("bake_maps", {"device": "cpu"})
        assert held_depths == [1, 0], "explicit cpu must skip the lock"

        r.execute_op("build_from_spec", {})
        assert held_depths == [1, 0, 0], "non-Cycles ops never take the lock"
        assert lock_calls == [None]  # one lock acquisition, default timeout

        # S3: an unobtainable lock fails loud, and Blender is never started
        def failing_lock(timeout_s=None):
            raise GpuLockError("GPU lock busy for 3s — card held by another tenant")

        monkeypatch.setattr(runner_mod, "gpu_lock", failing_lock)
        before = len(run_calls)
        with pytest.raises(BlenderExecutionError, match="S3"):
            r.execute_op("bake_maps", {})
        assert len(run_calls) == before
    finally:
        gl._machine_lock = None  # don't leak the tmp-path singleton
