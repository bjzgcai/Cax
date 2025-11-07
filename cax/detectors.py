"""Helpers for detecting environment capabilities relevant to CAX."""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

import psutil


@dataclass
class ExecutableInfo:
    name: str
    path: Optional[str]
    version: Optional[str]


def which(executable: str) -> Optional[str]:
    """Return the absolute path of *executable* if found on PATH."""

    return shutil.which(executable)


def executable_info(executable: str, version_args: list[str] | None = None) -> ExecutableInfo:
    path = which(executable)
    version = None
    if path and version_args is not None:
        try:
            result = subprocess.run([path, *version_args], check=False, capture_output=True, text=True)
            version_output = result.stdout.strip() or result.stderr.strip() or None
            if version_output:
                cleaned_lines = []
                for raw_line in version_output.splitlines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    if "failed to" in line.lower():
                        continue
                    cleaned_lines.append(line)
                version = cleaned_lines[0] if cleaned_lines else None
        except OSError:
            version = None
    return ExecutableInfo(name=executable, path=path, version=version)


def detect_gpu_summary() -> Optional[str]:
    """Return a short GPU summary using ``nvidia-smi`` if available."""

    nvidia_smi = which("nvidia-smi")
    if not nvidia_smi:
        return None
    try:
        result = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=name,memory.total,memory.used,driver_version",
                "--format=csv,noheader",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def system_resources() -> dict[str, str]:
    """Collect lightweight resource metrics to display in the UI."""

    virtual = psutil.virtual_memory()
    disk = psutil.disk_usage(".")
    return {
        "platform": platform.platform(),
        "cpu_count": str(psutil.cpu_count(logical=True) or 0),
        "memory_gb": f"{virtual.total / 1e9:.1f}",
        "disk_free_gb": f"{disk.free / 1e9:.1f}",
    }


def environment_summary() -> dict[str, Optional[str]]:
    """Return a dictionary summarising key binaries and hardware."""

    ramax = executable_info("RaMAx", ["--version"])
    cactus_exec = executable_info("cactus", ["--version"])
    # Prefer user-specified commands when deriving the cactus version
    cactus_version = detect_cactus_version() or (cactus_exec.version)
    return {
        "ramax_path": ramax.path,
        "ramax_version": ramax.version,
        "cactus_path": cactus_exec.path,
        "cactus_version": cactus_version,
        "gpu": detect_gpu_summary(),
    }


def detect_cactus_version() -> Optional[str]:
    """Return cactus version using: ``pip show cactus | grep -i ^Version``.

    Falls back to ``python -m pip show cactus`` parsing when the pipeline isn't available.
    """

    # Pipeline per user instruction
    try:
        pipe_cmd = "pip show cactus | grep -i ^Version"
        result = subprocess.run(
            pipe_cmd,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout:
            for raw in result.stdout.splitlines():
                line = raw.strip()
                if not line:
                    continue
                if line.lower().startswith("version"):
                    # Accept either "Version: x" or "version x"
                    if ":" in line:
                        return line.split(":", 1)[1].strip() or None
                    parts = line.split()
                    return parts[-1] if parts else None
    except OSError:
        pass

    # Fallback without grep/pipes
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", "cactus"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            if line.lower().startswith("version:"):
                return line.split(":", 1)[1].strip() or None
    except OSError:
        return None
    return None
