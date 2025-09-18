"""Helpers for detecting environment capabilities relevant to CAX."""
from __future__ import annotations

import platform
import shutil
import subprocess
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
            version = result.stdout.strip() or result.stderr.strip() or None
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
    cactus_prepare = executable_info("cactus-prepare", ["--version"])
    return {
        "ramax_path": ramax.path,
        "ramax_version": ramax.version,
        "cactus_prepare_path": cactus_prepare.path,
        "cactus_prepare_version": cactus_prepare.version,
        "gpu": detect_gpu_summary(),
    }
