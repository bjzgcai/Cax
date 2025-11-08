"""Execution engine for running CAX plans."""
from __future__ import annotations

import os
from contextlib import nullcontext
from pathlib import Path
import subprocess
import time
from typing import Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TaskID

from . import planner
from .models import Plan, RunSettings


IMPORTANT_KEYWORDS = ("error", "failed", "exception", "critical")


class PlanRunner:
    """Run a :class:`~cax.models.Plan` sequentially with logging."""

    def __init__(
        self,
        plan: Plan,
        base_dir: Optional[Path] = None,
        env: Optional[dict[str, str]] = None,
        mirror_stdout: bool = True,
        run_settings: Optional[RunSettings] = None,
    ):
        self.plan = plan
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.env = os.environ.copy()
        if env:
            self.env.update(env)
        self.log_root = self._derive_log_root()
        self.master_log_path = self.log_root / "cax-run.log"
        self.mirror_stdout = mirror_stdout
        self.console = Console(stderr=True)
        self.run_settings = run_settings or RunSettings()
        self.verbose = self.run_settings.verbose
        self.thread_count = self.run_settings.thread_count

    def run(self, dry_run: Optional[bool] = None) -> None:
        """Execute the plan. When ``dry_run`` is True, commands are only logged."""

        effective_dry = self.plan.dry_run if dry_run is None else dry_run
        planned_commands = planner.build_execution_plan(
            self.plan,
            self.base_dir,
            thread_count=self.thread_count,
        )
        self.log_root.mkdir(parents=True, exist_ok=True)
        total_commands = len(planned_commands)
        completed_commands = 0
        failure_command: planner.PlannedCommand | None = None

        progress_cm = (
            Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                TextColumn("({task.completed}/{task.total} done)"),
                TextColumn("[dim]{task.fields[remaining]} left[/dim]"),
                TimeElapsedColumn(),
                console=self.console,
                transient=True,
            )
            if self.mirror_stdout and not self.verbose
            else nullcontext(None)
        )

        with self.master_log_path.open("a", encoding="utf-8") as master_log:
            with progress_cm as progress:
                overall_task: TaskID | None = None
                remaining = len(planned_commands)
                if isinstance(progress, Progress):
                    overall_task = progress.add_task(
                        "Plan execution",
                        total=remaining,
                        remaining=remaining,
                    )
                for command in planned_commands:
                    task_id: TaskID | None = None
                    if isinstance(progress, Progress):
                        progress.update(
                            overall_task,
                            description=f"[cyan]{command.display_name}[/cyan]",
                        )
                        task_id = overall_task
                    success = self._run_single(
                        command,
                        master_log,
                        effective_dry,
                        progress if isinstance(progress, Progress) else None,
                        task_id,
                    )
                    if not success:
                        if isinstance(progress, Progress):
                            progress.update(
                                overall_task,
                                description=f"[red]✖ {command.display_name}[/red]",
                            )
                        failure_command = command
                        break
                    if isinstance(progress, Progress) and overall_task is not None:
                        remaining -= 1
                        progress.advance(overall_task)
                        progress.update(
                            overall_task,
                            description=f"[green]{command.display_name}[/green]",
                            remaining=remaining,
                        )
                    completed_commands += 1

        if failure_command is not None:
            if self.mirror_stdout:
                self.console.print(
                    f"[red]Plan failed[/red]: {completed_commands}/{total_commands} commands succeeded. "
                    f"Failed step: [bold]{failure_command.display_name}[/bold]."
                )
                if failure_command.log_path:
                    self.console.print(f"  • Step log: {failure_command.log_path}")
                self.console.print(f"  • Master log: {self.master_log_path}")
            raise RuntimeError(f"Command failed: {failure_command.display_name}")

        if self.mirror_stdout:
            self.console.print(
                f"[green]Plan completed[/green]: {completed_commands}/{total_commands} commands succeeded."
            )
            self.console.print(f"Logs written to {self.master_log_path}")

    def _run_single(
        self,
        command: planner.PlannedCommand,
        master_log,
        dry_run: bool,
        progress: Optional[Progress],
        task_id,
    ) -> bool:
        start_time = time.time()
        preview = command.shell_preview()
        master_log.write(f"[start] {command.display_name}: {preview}\n")
        master_log.flush()
        if progress is None and self.mirror_stdout:
            self.console.print(f"[cyan][start][/cyan] {command.display_name}: {preview}")

        if dry_run:
            self._log_dry_run(command, preview)
            elapsed = time.time() - start_time
            master_log.write(f"[skip] dry-run complete in {elapsed:.1f}s\n")
            master_log.flush()
            if progress is not None and task_id is not None:
                progress.update(
                    task_id,
                    description=f"[yellow]⏭ {command.display_name} (dry-run)[/yellow]",
                )
            elif self.mirror_stdout:
                self.console.print(f"[yellow][skip][/yellow] {command.display_name} (dry-run {elapsed:.1f}s)")
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
                return self._handle_launch_failure(
                    command,
                    exc,
                    master_log,
                    step_log,
                    progress,
                    task_id,
                )
            assert proc.stdout is not None
            for line in proc.stdout:
                step_log.write(line)
                master_log.write(line)
                if self.verbose:
                    self._emit_full(line)
                elif self._should_surface(line):
                    self._emit_important(line, progress)
            return_code = proc.wait()
            duration = time.time() - start_time
            step_log.write(f"\n# Exit code: {return_code} ({duration:.1f}s)\n")
            step_log.flush()
            master_log.write(f"[end] {command.display_name} -> {return_code} ({duration:.1f}s)\n")
            master_log.flush()

            if return_code != 0:
                if progress is not None and task_id is not None:
                    progress.update(task_id, description=f"[red]✖ {command.display_name} ({duration:.1f}s)[/red]")
                elif self.mirror_stdout:
                    self.console.print(f"[red][end][/red] {command.display_name} -> {return_code} ({duration:.1f}s)")
                return False

            if progress is not None and task_id is not None:
                progress.update(task_id, description=f"[green]✔ {command.display_name} ({duration:.1f}s)[/green]")
            elif self.mirror_stdout:
                self.console.print(f"[green][end][/green] {command.display_name} ({duration:.1f}s)")
            return True

    def _log_dry_run(self, command: planner.PlannedCommand, preview: str) -> None:
        if command.log_path:
            command.log_path.parent.mkdir(parents=True, exist_ok=True)
            with command.log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(f"# DRY RUN\n# {preview}\n")

    def _handle_launch_failure(
        self,
        command: planner.PlannedCommand,
        exc: OSError,
        master_log,
        step_log,
        progress: Optional[Progress],
        task_id,
    ) -> bool:
        message = f"[error] Failed to launch {command.display_name}: {exc}\n"
        step_log.write(message)
        master_log.write(message)
        master_log.flush()
        if progress is not None and task_id is not None:
            progress.update(task_id, description=f"[red]✖ {command.display_name} (launch failed)[/red]")
        elif self.mirror_stdout:
            self.console.print(f"[red]{message.rstrip()}[/red]")
        return False

    def _emit_important(self, line: str, progress: Optional[Progress]) -> None:
        text = line.rstrip()
        if not text:
            return
        if progress is not None:
            progress.console.log(text)
        elif self.mirror_stdout:
            self.console.log(text)

    def _should_surface(self, line: str) -> bool:
        if self.verbose:
            return True
        lowered = line.lower()
        suppress_phrases = (
            "graph correctness verification",
            "verification summary",
            "pointer_validity",
            "coordinate_overlap",
            "total errors",
            "error breakdown by type",
            "reference species expected to have overlapping segments",
        )
        if any(phrase in lowered for phrase in suppress_phrases):
            return False
        return any(keyword in lowered for keyword in IMPORTANT_KEYWORDS)

    def _emit_full(self, line: str) -> None:
        text = line.rstrip()
        if not text:
            return
        if self.mirror_stdout:
            self.console.print(text)

    def _derive_log_root(self) -> Path:
        if self.plan.out_dir:
            return _to_path(self.plan.out_dir, self.base_dir) / "logs"
        return (self.base_dir / "logs").resolve()


def _to_path(path_like: str, base_dir: Path) -> Path:
    path = Path(path_like).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()
