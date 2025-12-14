"""Execution engine for running CAX plans."""
from __future__ import annotations

import errno
import json
import os
import shutil
from contextlib import nullcontext
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Optional

import psutil
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TaskID
from rich.text import Text

from . import planner
from .models import Plan, RunSettings
from .resume import (
    command_canonical_preview,
    command_stable_key,
    index_state_commands,
    load_run_state_file,
    outputs_exist,
    plan_signature,
)


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
        self.run_state_path = self.log_root / "run_state.json"

    def run(self, dry_run: Optional[bool] = None) -> None:
        """Execute the plan. When ``dry_run`` is True, commands are only logged."""

        effective_dry = self.plan.dry_run if dry_run is None else dry_run
        planned_commands = planner.build_execution_plan(
            self.plan,
            self.base_dir,
            thread_count=self.thread_count,
        )
        self.log_root.mkdir(parents=True, exist_ok=True)
        state = _RunState(self.run_state_path, planned_commands, self.base_dir, self.thread_count)
        total_commands = len(planned_commands)
        completed_commands = 0
        failure_command: planner.PlannedCommand | None = None

        skip_enabled = self.run_settings.resume
        skipped_indices: set[int] = set()
        resume_start_index: int | None = None
        if skip_enabled:
            if state.mismatched and self.mirror_stdout:
                self.console.print(
                    "[yellow][resume][/yellow] Found run_state.json, but the plan signature differs; will attempt command-matching resume."
                )
            skipped_indices = state.compute_skips(planned_commands, self.base_dir)
            if skipped_indices and self.mirror_stdout:
                self.console.print(
                    f"[yellow][resume][/yellow] Skipping {len(skipped_indices)} successful steps (run_state.json detected)."
                )
            for idx in range(len(planned_commands)):
                if idx not in skipped_indices:
                    resume_start_index = idx
                    break

        progress_cm = (
            Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                TextColumn("({task.completed}/{task.total} done)"),
                TextColumn("[dim]{task.fields[remaining]} left[/dim]"),
                TextColumn("wait {task.fields[wait]}", style="magenta"),
                TextColumn("CPU {task.fields[cpu]}", style="yellow"),
                TextColumn("mem {task.fields[mem]}", style="cyan"),
                TextColumn("peak {task.fields[mem_peak]}", style="cyan"),
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
                        wait="0.0s",
                        cpu="--",
                        mem="--",
                        mem_peak="--",
                    )
                for command_index, command in enumerate(planned_commands):
                    cmd_id = state.command_key(command, command_index)
                    if command_index in skipped_indices:
                        state.mark_skipped(cmd_id, command, command_index)
                        master_log.write(f"[resume] skip {command.display_name}: {command.shell_preview()}\n")
                        master_log.flush()
                        if progress is not None and overall_task is not None:
                            remaining -= 1
                            progress.advance(overall_task)
                            progress.update(
                                overall_task,
                                description=f"[yellow]⏭ {command.display_name} (resume)[/yellow]",
                                remaining=remaining,
                            )
                        elif self.mirror_stdout:
                            self.console.print(f"[yellow][resume][/yellow] Skipping {command.display_name}")
                        completed_commands += 1
                        continue
                    entry = state.state["commands"].get(cmd_id)
                    if skip_enabled:
                        allow_restart = resume_start_index is not None and command_index == resume_start_index
                        self._prepare_toil_jobstore(command, entry, allow_restart=allow_restart)
                    preview = command.shell_preview()
                    task_id: TaskID | None = None
                    if isinstance(progress, Progress):
                        progress.update(
                            overall_task,
                            description=f"[cyan]{command.display_name}[/cyan]",
                            wait="0.0s",
                            cpu="--",
                            mem="--",
                            mem_peak="--",
                        )
                        self._announce_command(preview, progress)
                        task_id = overall_task
                    state.mark_running(cmd_id, command, command_index)
                    success, exit_code = self._run_single(
                        command,
                        master_log,
                        effective_dry,
                        progress if isinstance(progress, Progress) else None,
                        task_id,
                        preview,
                    )
                    state.mark_result(cmd_id, command, command_index, success, exit_code)
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
        preview: Optional[str] = None,
    ) -> tuple[bool, int]:
        start_time = time.time()
        preview = preview or command.shell_preview()
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
                    **_basic_metric_fields(elapsed),
                )
            elif self.mirror_stdout:
                self.console.print(f"[yellow][skip][/yellow] {command.display_name} (dry-run {elapsed:.1f}s)")
            return True, 0

        if command.workdir:
            command.workdir.mkdir(parents=True, exist_ok=True)

        step_log_path = command.log_path or (self.log_root / f"{command.display_name}.log")
        step_log_path.parent.mkdir(parents=True, exist_ok=True)

        telemetry: _CommandTelemetry | None = None
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
                    preview,
                )
            assert proc.stdout is not None
            if progress is not None and task_id is not None:
                telemetry_candidate = _CommandTelemetry(progress, task_id, start_time)
                if telemetry_candidate.start(proc.pid):
                    telemetry = telemetry_candidate
            for line in proc.stdout:
                step_log.write(line)
                master_log.write(line)
                if self.verbose:
                    self._emit_full(line)
                elif self._should_surface(line):
                    self._emit_important(line, progress)
            return_code = proc.wait()
            duration = time.time() - start_time
            telemetry_fields: dict[str, str] = {}
            if progress is not None and task_id is not None:
                if telemetry is not None:
                    telemetry_fields = telemetry.stop(duration)
                else:
                    telemetry_fields = _basic_metric_fields(duration)
            step_log.write(f"\n# Exit code: {return_code} ({duration:.1f}s)\n")
            step_log.flush()
            master_log.write(f"[end] {command.display_name} -> {return_code} ({duration:.1f}s)\n")
            master_log.flush()

            if return_code != 0:
                if progress is not None and task_id is not None:
                    progress.update(
                        task_id,
                        description=f"[red]✖ {command.display_name} ({duration:.1f}s)[/red]",
                        **telemetry_fields,
                    )
                elif self.mirror_stdout:
                    self.console.print(f"[red][end][/red] {command.display_name} -> {return_code} ({duration:.1f}s)")
                return False, return_code

            if progress is not None and task_id is not None:
                progress.update(
                    task_id,
                    description=f"[green]✔ {command.display_name} ({duration:.1f}s)[/green]",
                    **telemetry_fields,
                )
            elif self.mirror_stdout:
                self.console.print(f"[green][end][/green] {command.display_name} ({duration:.1f}s)")
            return True, return_code

    def _log_dry_run(self, command: planner.PlannedCommand, preview: str) -> None:
        if command.log_path:
            command.log_path.parent.mkdir(parents=True, exist_ok=True)
            with command.log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(f"# DRY RUN\n# {preview}\n")

    def _announce_command(self, preview: str, progress: Optional[Progress]) -> None:
        text = Text("command ", style="dim", overflow="fold", no_wrap=False)
        text.append(preview)
        console: Console | None = None
        if progress is not None:
            console = progress.console
        elif self.mirror_stdout:
            console = self.console
        if console is not None:
            console.print(text)

    def _handle_launch_failure(
        self,
        command: planner.PlannedCommand,
        exc: OSError,
        master_log,
        step_log,
        progress: Optional[Progress],
        task_id,
        preview: str,
    ) -> tuple[bool, int]:
        hint = ""
        if exc.errno == errno.EACCES:
            resolved = _resolve_executable(command.command[0], self.env.get("PATH"))
            if resolved and not os.access(resolved, os.X_OK):
                hint = f" (missing execute bit: {resolved})"
        message = f"[error] Failed to launch {command.display_name}: {exc}{hint}\n"
        step_log.write(message)
        master_log.write(message)
        master_log.flush()
        if progress is not None and task_id is not None:
            progress.update(
                task_id,
                description=f"[red]✖ {command.display_name} (launch failed)[/red]",
                **_basic_metric_fields(0.0),
            )
        elif self.mirror_stdout:
            self.console.print(f"[red]{message.rstrip()}[/red]")
        return False, -1

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

    def _prepare_toil_jobstore(
        self,
        command: planner.PlannedCommand,
        entry: Optional[dict[str, Any]],
        *,
        allow_restart: bool,
    ) -> None:
        """在断点续跑场景下处理 Toil jobStore 冲突。

        Cactus 的多数步骤使用 Toil；当 jobStore 目录已存在时：
        - 若上次该步骤处于 running/failed，则追加 `--restart` 以继续执行；
        - 否则（例如：断点回退导致本次需要重跑），清理该 jobStore 以便从头运行，避免复用旧依赖。
        """

        step = command.step
        if step is None or not step.jobstore:
            return

        jobstore_path = _resolve_jobstore_path(step.jobstore, self.base_dir)
        if not jobstore_path.exists():
            return

        status = entry.get("status") if entry else None
        if status in {"running", "failed"} and not allow_restart:
            if self.mirror_stdout:
                self.console.print(
                    f"[yellow][resume][/yellow] To avoid reusing stale Toil jobStore state, cleaning and rerunning: {jobstore_path}"
                )
            try:
                if jobstore_path.is_dir():
                    shutil.rmtree(jobstore_path)
                else:
                    jobstore_path.unlink()
            except OSError as exc:
                if self.mirror_stdout:
                    self.console.print(f"[yellow][resume][/yellow] Failed to clean jobStore (will still try to run): {exc}")
            return

        if status in {"running", "failed"} and allow_restart:
            root_marker = jobstore_path / "files" / "shared" / "rootJobStoreID"
            if not root_marker.exists():
                if self.mirror_stdout:
                    self.console.print(
                        f"[yellow][resume][/yellow] Detected incomplete Toil jobStore (missing rootJobStoreID); cleaning and rerunning: {jobstore_path}"
                    )
                try:
                    if jobstore_path.is_dir():
                        shutil.rmtree(jobstore_path)
                    else:
                        jobstore_path.unlink()
                except OSError as exc:
                    if self.mirror_stdout:
                        self.console.print(f"[yellow][resume][/yellow] Failed to clean jobStore (will still try to run): {exc}")
                return
            if "--restart" not in command.command:
                command.command = [*command.command, "--restart"]
            if self.mirror_stdout:
                self.console.print(
                    f"[yellow][resume][/yellow] Detected existing Toil jobStore; adding --restart for this step: {jobstore_path}"
                )
            return

        if self.mirror_stdout:
            self.console.print(f"[yellow][resume][/yellow] Cleaning Toil jobStore for rerun: {jobstore_path}")
        try:
            if jobstore_path.is_dir():
                shutil.rmtree(jobstore_path)
            else:
                jobstore_path.unlink()
        except OSError as exc:
            if self.mirror_stdout:
                self.console.print(f"[yellow][resume][/yellow] Failed to clean jobStore (will still try to run): {exc}")


class _RunState:
    """轻量级运行状态记录，用于断点续跑。"""

    def __init__(
        self,
        path: Path,
        commands: list[planner.PlannedCommand],
        base_dir: Path,
        thread_count: Optional[int],
    ) -> None:
        self.path = path
        self.plan_signature = plan_signature(commands, base_dir, thread_count)
        self.state: dict[str, Any] = {"plan_signature": self.plan_signature, "commands": {}}
        self.mismatched = False
        loaded = self._load()
        loaded_sig = loaded.get("plan_signature") if loaded else None
        if loaded and loaded_sig and loaded_sig != self.plan_signature:
            self.mismatched = True
        if loaded:
            self.state["commands"] = index_state_commands(loaded.get("commands", {}))
        self._write()

    def compute_skips(self, commands: list[planner.PlannedCommand], base_dir: Path) -> set[int]:
        # 续跑策略：只跳过“前缀连续已完成步骤”，一旦某一步需要重跑，则其后的步骤都视为待执行。
        #
        # 原因：计划是顺序执行的，后续步骤通常依赖前面步骤产物；若从中间重跑但仍跳过后续，
        # 会导致产物与依赖不一致（例如上游 HAL 改变但下游 hal2fasta 仍被跳过）。
        skips: set[int] = set()
        for idx, command in enumerate(commands):
            cmd_id = self.command_key(command, idx)
            entry = self.state["commands"].get(cmd_id)
            if entry and entry.get("status") == "success" and outputs_exist(command, base_dir):
                skips.add(idx)
                continue
            break
        return skips

    def command_key(self, command: planner.PlannedCommand, index: int) -> str:
        _ = index
        return command_stable_key(command)

    def mark_running(self, cmd_id: str, command: planner.PlannedCommand, index: int) -> None:
        self.state["commands"][cmd_id] = {
            "index": index,
            "display_name": command.display_name,
            "preview": command.shell_preview(),
            "canonical_preview": command_canonical_preview(command),
            "stable_key": cmd_id,
            "log_path": str(command.log_path) if command.log_path else None,
            "status": "running",
            "updated_at": _now_iso(),
        }
        self._write()

    def mark_result(
        self,
        cmd_id: str,
        command: planner.PlannedCommand,
        index: int,
        success: bool,
        exit_code: int,
    ) -> None:
        self.state["commands"][cmd_id] = {
            "index": index,
            "display_name": command.display_name,
            "preview": command.shell_preview(),
            "canonical_preview": command_canonical_preview(command),
            "stable_key": cmd_id,
            "log_path": str(command.log_path) if command.log_path else None,
            "status": "success" if success else "failed",
            "exit_code": exit_code,
            "updated_at": _now_iso(),
        }
        self._write()

    def mark_skipped(self, cmd_id: str, command: planner.PlannedCommand, index: int) -> None:
        existing = self.state["commands"].get(cmd_id, {})
        status = existing.get("status", "success")
        self.state["commands"][cmd_id] = {
            "index": index,
            "display_name": command.display_name,
            "preview": command.shell_preview(),
            "canonical_preview": command_canonical_preview(command),
            "stable_key": cmd_id,
            "log_path": str(command.log_path) if command.log_path else None,
            "status": status,
            "exit_code": existing.get("exit_code"),
            "updated_at": _now_iso(),
            "skipped": True,
        }
        self._write()

    def _load(self) -> dict[str, Any]:
        return load_run_state_file(self.path)

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(self.path)


def _format_duration(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{sec:02d}s"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _format_bytes(value: int) -> str:
    if value <= 0:
        return "0B"
    units = ("B", "KB", "MB", "GB", "TB")
    num = float(value)
    for unit in units:
        if num < 1024 or unit == units[-1]:
            return f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}TB"


def _resolve_executable(binary: str, path_env: Optional[str]) -> Optional[Path]:
    """Return the first PATH entry containing *binary* (even if non-executable)."""

    if os.path.isabs(binary):
        candidate = Path(binary)
        return candidate if candidate.exists() else None

    if not path_env:
        path_env = os.environ.get("PATH", "")
    for entry in path_env.split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / binary
        if candidate.exists():
            return candidate
    return None


def _format_cpu(value: Optional[float]) -> str:
    if value is None or value < 0:
        return "--"
    return f"{value:.1f}%"


def _basic_metric_fields(elapsed: float) -> dict[str, str]:
    return {
        "wait": _format_duration(elapsed),
        "cpu": "--",
        "mem": "--",
        "mem_peak": "--",
    }


class _CommandTelemetry:
    """Collect per-step CPU and memory stats for the progress bar."""

    def __init__(
        self,
        progress: Progress,
        task_id: TaskID,
        start_time: float,
        interval: float = 0.5,
    ) -> None:
        self.progress = progress
        self.task_id = task_id
        self.start_time = start_time
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: psutil.Process | None = None
        self._peak_bytes = 0
        self._latest: dict[str, str] = _basic_metric_fields(0.0)

    def start(self, pid: int) -> bool:
        try:
            self._process = psutil.Process(pid)
        except psutil.Error:
            return False
        self._prime_cpu_counters(self._process)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self, final_duration: float) -> dict[str, str]:
        if self._thread is not None:
            self._stop_event.set()
            self._thread.join(timeout=2.0)
        final_fields = dict(self._latest)
        final_fields["wait"] = _format_duration(final_duration)
        return final_fields

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval):
            self._update_fields()
        self._update_fields()

    def _update_fields(self) -> None:
        fields = dict(self._latest)
        elapsed = time.time() - self.start_time
        fields["wait"] = _format_duration(elapsed)
        process = self._process
        if process is not None:
            cpu_value, mem_bytes = self._collect_stats(process)
            if cpu_value is not None:
                fields["cpu"] = _format_cpu(cpu_value)
            if mem_bytes is not None:
                self._peak_bytes = max(self._peak_bytes, mem_bytes)
                fields["mem"] = _format_bytes(mem_bytes)
                fields["mem_peak"] = _format_bytes(self._peak_bytes)
        self._latest = fields
        self.progress.update(self.task_id, **fields)

    def _collect_stats(self, root: psutil.Process) -> tuple[Optional[float], Optional[int]]:
        total_cpu = 0.0
        total_mem = 0
        sampled = False
        try:
            processes = [root, *root.children(recursive=True)]
        except psutil.Error:
            return None, None
        for proc in processes:
            try:
                with proc.oneshot():
                    cpu_part = proc.cpu_percent(interval=None)
                    mem_info = proc.memory_info()
            except psutil.Error:
                continue
            sampled = True
            total_cpu += cpu_part
            total_mem += mem_info.rss
        if not sampled:
            return None, None
        return total_cpu, total_mem

    def _prime_cpu_counters(self, process: psutil.Process) -> None:
        try:
            processes = [process, *process.children(recursive=True)]
        except psutil.Error:
            processes = [process]
        for proc in processes:
            try:
                proc.cpu_percent(interval=None)
            except psutil.Error:
                continue


def _to_path(path_like: str, base_dir: Path) -> Path:
    path = Path(path_like).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _resolve_jobstore_path(jobstore: str, base_dir: Path) -> Path:
    """解析 Toil jobStore 路径（兼容 `file:` 前缀）。"""

    value = jobstore
    if value.startswith("file:"):
        value = value.split(":", 1)[1]
    return _to_path(value, base_dir)
