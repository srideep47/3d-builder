"""Subprocess runner for Blender headless harness operations.

Uses isolated process executions with JSON temp files to guarantee:
1. No scene cross-contamination across operations
2. Safe argument passing on Windows paths containing spaces or special characters
3. Structured output parsing framed between sentinel markers
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .locate import BlenderInstall, locate_blender

SENTINEL_BEGIN = "<<<3DBUILDER_RESULT_BEGIN>>>"
SENTINEL_END = "<<<3DBUILDER_RESULT_END>>>"
HARNESS_SCRIPT_PATH = Path(__file__).parent / "harness_script.py"


class BlenderExecutionError(Exception):
    """Raised when Blender headless execution fails or returns an error."""
    pass


class BlenderRunner:
    def __init__(self, blender_path: str | Path | None = None):
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
                "--python",
                str(HARNESS_SCRIPT_PATH.resolve()),
                "--",
                temp_json_path,
            ]

            # Force UTF-8 in the child so sentinel output survives any locale.
            env = dict(os.environ)
            env.setdefault("PYTHONIOENCODING", "utf-8")

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
