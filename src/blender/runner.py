"""Subprocess runner for Blender headless harness operations.

Uses isolated process executions with JSON temp files to guarantee:
1. No scene cross-contamination across operations
2. Safe argument passing on Windows paths containing spaces or special characters
3. Structured output parsing framed between sentinel markers

GPU sequencing (GLM_PROMPT_NEURAL_INTAKE.md §4.0): Cycles GPU-capable ops
(bake_maps / bake_materials / render_views) hold the machine-wide GPU lock
(src/neural/gpu_lock.py — shared file with the img3d service, so a neural
generation and a Blender bake never share the card) unless the op is
explicitly pinned to `device: "cpu"`. A lock that cannot be obtained fails
loud as a BlenderExecutionError (stop condition S3) — never run unlocked.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from ..neural.gpu_lock import GpuLockError, gpu_lock
from .locate import BlenderInstall, locate_blender

SENTINEL_BEGIN = "<<<3DBUILDER_RESULT_BEGIN>>>"
SENTINEL_END = "<<<3DBUILDER_RESULT_END>>>"
HARNESS_SCRIPT_PATH = Path(__file__).parent / "harness_script.py"

# Ops that configure a Cycles compute device and may take the GPU
# ("auto" tries GPU first — only an explicit "cpu" opts out of the lock).
GPU_OPS = ("bake_maps", "bake_materials", "render_views")


class BlenderExecutionError(Exception):
    """Raised when Blender headless execution fails or returns an error."""
    pass


def _needs_gpu_lock(op: str, params: dict[str, Any] | None) -> bool:
    if op not in GPU_OPS:
        return False
    device = str((params or {}).get("device", "auto")).strip().lower()
    return device != "cpu"


class BlenderRunner:
    def __init__(self, blender_path: str | Path | None = None, threads: int | None = None):
        if blender_path:
            self.install: BlenderInstall | None = BlenderInstall(
                executable=str(blender_path),
                version="Custom",
                major=4,
                minor=0,
                patch=0,
                source="explicit",
                supported=True,
            )
        else:
            self.install = locate_blender()
        # Thread cap for batch throughput: N concurrent Blenders each
        # grabbing every core oversubscribe the machine and corrupt
        # wall-clock measurements. None = Blender's default (all cores).
        # Explicit argument wins; otherwise THREED_BLENDER_THREADS (set per
        # worker process by the batch driver).
        if threads is None:
            env_threads = (os.environ.get("THREED_BLENDER_THREADS") or "").strip()
            threads = int(env_threads) if env_threads.isdigit() and int(env_threads) > 0 else None
        self.threads = threads

    @property
    def is_available(self) -> bool:
        return self.install is not None and self.install.supported

    def execute_op(self, op: str, params: dict[str, Any] | None = None, timeout_sec: float | None = None) -> dict[str, Any]:
        """Execute a single Blender operation and return structured JSON result."""
        if not self.is_available or not self.install:
            raise BlenderExecutionError(
                "Blender 3.3+ was not found on this system. "
                "Please configure THREED_BLENDER / BLENDER_PATH or install Blender 4.x."
            )

        if timeout_sec is None:
            # Renders and bakes take far longer than geometry ops.
            timeout_sec = 900.0 if op in ("render_views", "bake_materials") else 300.0

        payload = {
            "op": op,
            "params": params or {},
        }

        # Create temporary JSON file for arguments
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
            json.dump(payload, tf, ensure_ascii=False)
            temp_json_path = tf.name

        try:
            cmd = [
                self.install.executable,
                "--background",
                "--factory-startup",
            ]
            if self.threads:
                cmd += ["--threads", str(self.threads)]
            cmd += [
                "--python",
                str(HARNESS_SCRIPT_PATH.resolve()),
                "--",
                temp_json_path,
            ]

            # Force UTF-8 in the child so sentinel output survives any locale.
            env = dict(os.environ)
            env.setdefault("PYTHONIOENCODING", "utf-8")

            # §4.0: GPU-capable Cycles ops run inside the machine GPU lock.
            # The lock wait happens before the child starts, so the op's own
            # timeout still bounds only Blender's run.
            lock_ctx_factory = gpu_lock if _needs_gpu_lock(op, params) else nullcontext
            try:
                with lock_ctx_factory():
                    process = subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=timeout_sec,
                        env=env,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
            except GpuLockError as e:
                raise BlenderExecutionError(
                    f"GPU lock unobtainable for op '{op}' (stop condition S3): {e}"
                ) from e

            stdout = process.stdout or ""
            stderr = process.stderr or ""

            # Extract result framed by sentinel tokens
            if SENTINEL_BEGIN not in stdout or SENTINEL_END not in stdout:
                raise BlenderExecutionError(
                    f"Blender failed to return structured result for op '{op}'.\n"
                    f"Process Exit Code: {process.returncode}\n"
                    f"Stdout:\n{stdout[-1000:]}\n"
                    f"Stderr:\n{stderr[-1000:]}"
                )

            start_idx = stdout.index(SENTINEL_BEGIN) + len(SENTINEL_BEGIN)
            end_idx = stdout.index(SENTINEL_END, start_idx)
            raw_json = stdout[start_idx:end_idx].strip()

            result = json.loads(raw_json)
            if not result.get("success", False):
                err = result.get("error", "Unknown error in Blender op")
                tb = result.get("traceback", "")
                raise BlenderExecutionError(f"Blender op '{op}' failed: {err}\n{tb}")

            return result

        except subprocess.TimeoutExpired:
            raise BlenderExecutionError(f"Blender op '{op}' timed out after {timeout_sec}s")
        finally:
            if os.path.exists(temp_json_path):
                try:
                    os.remove(temp_json_path)
                except Exception:
                    pass
