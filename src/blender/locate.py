"""Blender executable discovery and version verification.

Searches:
1. Explicit environment variables (BLENDER_PATH, BLENDER_EXECUTABLE, ARCHVIZ_BLENDER)
2. Local project directory (tools/blender/blender.exe)
3. System PATH (where.exe blender)
4. Standard Windows install locations (Program Files, LocalAppData, Scoop, Steam)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

MIN_BLENDER_MAJOR = 3
MIN_BLENDER_MINOR = 3


@dataclass
class BlenderInstall:
    executable: str
    version: str
    major: int
    minor: int
    patch: int
    source: str
    supported: bool


def parse_blender_version(output: str) -> tuple[str, int, int, int] | None:
    """Parse 'Blender 4.2.1' or similar from blender --version output."""
    match = re.search(r"Blender\s+(\d+)\.(\d+)(?:\.(\d+))?", output, re.IGNORECASE)
    if not match:
        return None
    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3) or 0)
    version_str = f"Blender {major}.{minor}.{patch}"
    return version_str, major, minor, patch


def probe_blender(executable_path: str | Path, source: str = "custom") -> BlenderInstall | None:
    """Run `blender --version` on a candidate path to verify compatibility."""
    path_str = str(executable_path)
    if not os.path.isabs(path_str) and not shutil.which(path_str):
        # Not a command in PATH or absolute path
        if not Path(path_str).exists():
            return None

    try:
        res = subprocess.run(
            [path_str, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        combined = (res.stdout or "") + (res.stderr or "")
        parsed = parse_blender_version(combined)
        if not parsed:
            return None

        ver_str, major, minor, patch = parsed
        supported = (
            major > MIN_BLENDER_MAJOR
            or (major == MIN_BLENDER_MAJOR and minor >= MIN_BLENDER_MINOR)
        )
        return BlenderInstall(
            executable=str(Path(path_str).resolve()) if os.path.exists(path_str) else path_str,
            version=ver_str,
            major=major,
            minor=minor,
            patch=patch,
            source=source,
            supported=supported,
        )
    except Exception:
        return None


def get_candidate_locations() -> list[str]:
    """Gather list of potential install locations on the host system."""
    candidates: list[str] = []

    # 1. Local tools/ portable installs (tools/blender, tools/blender-4.5.13, ...)
    root_dir = Path(__file__).resolve().parents[2]
    tools_dir = root_dir / "tools"
    exe_name = "blender.exe" if os.name == "nt" else "blender"
    if tools_dir.is_dir():
        patterns = (f"blender/{exe_name}", f"blender-*/{exe_name}", f"*/{exe_name}")
        seen = set()
        for pattern in patterns:
            for hit in tools_dir.glob(pattern):
                if hit not in seen:
                    seen.add(hit)
                    candidates.append(str(hit))

    # 2. Windows paths
    if os.name == "nt":
        prog_files = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs"),
        ]
        for pf in prog_files:
            if not pf:
                continue
            bf_base = Path(pf) / "Blender Foundation"
            if bf_base.is_dir():
                try:
                    # Newest versions first (e.g. Blender 4.5 > Blender 4.2 > Blender 3.6)
                    for item in sorted(bf_base.iterdir(), reverse=True):
                        exe = item / "blender.exe"
                        if exe.exists():
                            candidates.append(str(exe))
                except Exception:
                    pass
            direct_exe = Path(pf) / "Blender" / "blender.exe"
            if direct_exe.exists():
                candidates.append(str(direct_exe))

        # Check Scoop
        scoop_exe = Path(os.path.expanduser("~")) / "scoop" / "apps" / "blender" / "current" / "blender.exe"
        if scoop_exe.exists():
            candidates.append(str(scoop_exe))
    else:
        # Linux / MacOS paths
        candidates.extend([
            "/usr/bin/blender",
            "/usr/local/bin/blender",
            "/snap/bin/blender",
            "/Applications/Blender.app/Contents/MacOS/Blender",
        ])

    return candidates


_CACHED_INSTALL: BlenderInstall | None = None


def locate_blender(force_rescan: bool = False) -> BlenderInstall | None:
    """Locate a working Blender installation in priority order."""
    global _CACHED_INSTALL
    if _CACHED_INSTALL and not force_rescan:
        return _CACHED_INSTALL

    # 1. Environment variables
    for env_var in ("THREED_BLENDER", "BLENDER_PATH", "BLENDER_EXECUTABLE", "ARCHVIZ_BLENDER"):
        val = os.environ.get(env_var)
        if val:
            found = probe_blender(val, source=f"env:{env_var}")
            if found and found.supported:
                _CACHED_INSTALL = found
                return found

    # 2. PATH
    path_hit = shutil.which("blender") or shutil.which("blender4") or shutil.which("Blender")
    if path_hit:
        found = probe_blender(path_hit, source="path")
        if found and found.supported:
            _CACHED_INSTALL = found
            return found

    # 3. Known directory sweep
    for candidate in get_candidate_locations():
        found = probe_blender(candidate, source="known-location")
        if found and found.supported:
            _CACHED_INSTALL = found
            return found

    return None
