"""Execution engine for running CAX plans."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import time
from typing import Iterable, Optional

from . import planner
from .models import Plan, Step


class PlanRunner:
    """Run a :class:`~cax.models.Plan` sequentially with logging and fallback handling."""

    def __init__(self, plan: Plan, base_dir: Optional[Path] = None, env: Optional[dict[str, str]] = None, mirror_stdout: bool = True):
        self.plan = plan
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.env = os.environ.copy()
        if env:
            self.env.update(env)
        self.log_root = self._derive_log_root()
        self.master_log_path = self.log_root / "cax-run.log"
        self.mirror_stdout = mirror_stdout

    def run(self, dry_run: Optional[bool] = None) -> None:
        """Execute the plan. When ``dry_run`` is True, commands are only logged."""

        effective_dry = self.plan.dry_run if dry_run is None else dry_run
        planned_commands = planner.build_execution_plan(self.plan, self.base_dir)
        self.log_root.mkdir(parents=True, exist_ok=True)
        with self.master_log_path.open("a", encoding="utf-8") as master_log:
            for command in planned_commands:
                success = self._run_single(command, master_log, effective_dry)
                if success:
                    continue
                if command.is_ramax and command.fallback_steps and self.plan.fallback_policy == "cactus":
                    master_log.write(f"[fallback] {command.display_name} failed – executing cactus steps\n")
                    master_log.flush()
                    success = self._run_fallback(command, master_log, effective_dry)
                if not success:
                    raise RuntimeError(f"Command failed: {command.display_name}")

    def _run_single(self, command: planner.PlannedCommand, master_log, dry_run: bool) -> bool:
        start_time = time.time()
        preview = command.shell_preview()
        master_log.write(f"[start] {command.display_name}: {preview}\n")
        master_log.flush()
        if self.mirror_stdout:
            print(f"[start] {command.display_name}: {preview}", flush=True)

        if dry_run:
            if command.log_path:
                command.log_path.parent.mkdir(parents=True, exist_ok=True)
                with command.log_path.open("a", encoding="utf-8") as log_file:
                    log_file.write(f"# DRY RUN\n# {preview}\n")
            master_log.write(f"[skip] dry-run complete in {time.time() - start_time:.1f}s\n")
            master_log.flush()
            if self.mirror_stdout:
                print(f"[skip] dry-run complete in {time.time() - start_time:.1f}s", flush=True)
            return True

        if command.workdir:
            command.workdir.mkdir(parents=True, exist_ok=True)

        step_log_path = command.log_path or (self.log_root / f"{command.display_name}.log")
        step_log_path.parent.mkdir(parents=True, exist_ok=True)

        with step_log_path.open("a", encoding="utf-8") as step_log:
            step_log.write(f"# Command: {preview}\n")
            step_log.flush()
            try:
                proc = subprocess.Popen(
                    command.command,
                    cwd=self.base_dir,
                    env=self.env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            except OSError as exc:
                message = f"[error] Failed to launch {command.display_name}: {exc}\n"
                step_log.write(message)
                master_log.write(message)
                master_log.flush()
                if self.mirror_stdout:
                    print(message, end="", flush=True)
                return False
            assert proc.stdout is not None
            for line in proc.stdout:
                step_log.write(line)
                master_log.write(line)
                if self.mirror_stdout:
                    print(line, end="", flush=True)
            return_code = proc.wait()
            duration = time.time() - start_time
            step_log.write(f"\n# Exit code: {return_code} ({duration:.1f}s)\n")
            step_log.flush()
            master_log.write(f"[end] {command.display_name} -> {return_code} ({duration:.1f}s)\n")
            master_log.flush()
            if self.mirror_stdout:
                print(f"[end] {command.display_name} -> {return_code} ({duration:.1f}s)", flush=True)
            return return_code == 0

    def _run_fallback(self, command: planner.PlannedCommand, master_log, dry_run: bool) -> bool:
        for step in command.fallback_steps:
            fallback_command = planner.PlannedCommand(
                command=_split(step.raw),
                category=step.kind,
                display_name=step.short_label(),
                log_path=_step_log_path(step, self.base_dir, self.log_root),
                round_name=command.round_name,
                step=step,
                is_ramax=False,
            )
            success = self._run_single(fallback_command, master_log, dry_run)
            if not success:
                return False
        return True

    def _derive_log_root(self) -> Path:
        if self.plan.out_dir:
            return _to_path(self.plan.out_dir, self.base_dir) / "logs"
        return (self.base_dir / "logs").resolve()


def _split(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _to_path(path_like: str, base_dir: Path) -> Path:
    path = Path(path_like).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _step_log_path(step: Step, base_dir: Path, log_root: Path) -> Path:
    if step.log_file:
        path = Path(step.log_file).expanduser()
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        return path
    return (log_root / f"{step.short_label()}").with_suffix('.log').resolve()
