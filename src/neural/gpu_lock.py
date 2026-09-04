"""Machine-wide GPU lock (GLM_PROMPT_NEURAL_INTAKE.md §4.0).

One graphics card, several GPU tenants: Blender bakes spawned by this
repo's processes, the img3d service's neural backends (including the
ComfyUI process the comfy_trellis2 backend drives — ComfyUI itself never
takes this lock; the service takes it on its behalf around a generation),
and local vision-model servers. An unlucky interleaving OOMs one of them;
this lock is the whole sequencing story.

Do not over-engineer (the work order's own instruction): a byte-range
lock on one shared file. msvcrt.locking on Windows, fcntl.flock on POSIX.
The OS releases the lock when the owning process dies, so a crashed
holder cannot wedge the machine — no staleness repair, no PID tracking.

Semantics:
  - cross-PROCESS exclusion: the file lock
  - cross-THREAD exclusion within a process: a threading.RLock guards the
    file lock, so two threads of one process serialise (two Blender bakes
    in one webapp process contend for the card exactly like two
    processes), while the SAME thread can re-enter (a bake inside a
    generation's window, a nested acquire)
  - generous timeout bounding the WHOLE acquire — thread wait and file
    wait alike — then GpuLockError; fail loud, stop condition S3; never
    silently proceed without the lock

Lock file location: THREED_GPU_LOCK env override (use an ABSOLUTE path —
both environments must resolve to the same file). Default:
%PROGRAMDATA%/threed-builder/gpu.lock on Windows (the directory is
created on demand; Authenticated Users may create subfolders there by
default — if yours is locked down, create it once as admin or set the
env), /tmp/threed-builder-gpu.lock on POSIX.

The service environment imports its own copy (services/img3d_service/
gpu_lock.py) because the service venv must stay self-contained — keep the
two copies byte-identical.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_TIMEOUT_S = 1800.0  # a queued generation + texturing can run ~15 min
POLL_S = 0.25


class GpuLockError(RuntimeError):
    """GPU lock unobtainable or broken — fail loud (stop condition S3)."""


def _default_path() -> Path:
    env = (os.environ.get("THREED_GPU_LOCK") or "").strip()
    if env:
        return Path(env)
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA") or str(Path.home())
        return Path(base) / "threed-builder" / "gpu.lock"
    return Path("/tmp/threed-builder-gpu.lock")


def _default_timeout() -> float:
    env = (os.environ.get("THREED_GPU_LOCK_TIMEOUT") or "").strip()
    try:
        return float(env) if env else DEFAULT_TIMEOUT_S
    except ValueError:
        return DEFAULT_TIMEOUT_S


class GpuLock:
    """Advisory machine-wide GPU exclusion (see module docstring)."""

    def __init__(self, path: str | Path | None = None, timeout_s: float | None = None):
        self.path = Path(path) if path else _default_path()
        self.timeout_s = timeout_s if timeout_s is not None else _default_timeout()
        self._rlock = threading.RLock()
        self._depth = 0
        self._fd: int | None = None

    # ── public API ───────────────────────────────────────────────────────────

    def acquire(self, timeout_s: float | None = None) -> None:
        timeout = self.timeout_s if timeout_s is None else timeout_s
        # the timeout must bound the WHOLE acquire: an unbounded RLock wait
        # here would hang a second thread forever regardless of timeout_s
        if not self._rlock.acquire(timeout=timeout):
            raise GpuLockError(
                f"GPU lock busy for {timeout:.0f}s — another thread in this "
                "process is holding the card; raise THREED_GPU_LOCK_TIMEOUT "
                "if this wait is legitimately long"
            )
        try:
            if self._depth == 0:
                self._acquire_file(timeout)
            self._depth += 1
        except BaseException:
            self._rlock.release()
            raise

    def release(self) -> None:
        if self._depth == 0:
            raise GpuLockError("release() without a matching acquire()")
        self._depth -= 1
        if self._depth == 0:
            self._release_file()
        self._rlock.release()

    def __enter__(self) -> "GpuLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()

    # ── file lock ────────────────────────────────────────────────────────────

    def _acquire_file(self, timeout: float) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR)
        except OSError as e:
            raise GpuLockError(f"cannot open GPU lock file {self.path}: {e}") from e
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(self._fd, 0, os.SEEK_SET)
                    msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    os.close(self._fd)
                    self._fd = None
                    raise GpuLockError(
                        f"GPU lock busy for {timeout:.0f}s ({self.path}) — another GPU "
                        "tenant (Blender bake / neural generation / vision server) is "
                        "holding the card; raise THREED_GPU_LOCK_TIMEOUT if this wait "
                        "is legitimately long"
                    )
                time.sleep(min(POLL_S, remaining))

    def _release_file(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass  # process death closes fd and drops the lock anyway; never wedge release
        finally:
            os.close(fd)


# ── process-wide singleton ───────────────────────────────────────────────────

_machine_lock: GpuLock | None = None
_machine_lock_guard = threading.Lock()


def machine_gpu_lock() -> GpuLock:
    """The process-wide machine lock — one file lock per process, shared by
    every thread, so nested acquires from different call sites don't
    dead-lock each other."""
    global _machine_lock
    with _machine_lock_guard:
        if _machine_lock is None:
            _machine_lock = GpuLock()
        return _machine_lock


@contextmanager
def gpu_lock(timeout_s: float | None = None) -> Iterator[GpuLock]:
    """Take the machine-wide GPU lock for a block of GPU work."""
    lock = machine_gpu_lock()
    lock.acquire(timeout_s=timeout_s)
    try:
        yield lock
    finally:
        lock.release()
