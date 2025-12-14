"""断点续跑（resume）相关的共享工具函数。

该模块只做“状态解析/预览/匹配”，不直接执行命令，供 runner/CLI/UI 复用。
"""
from __future__ import annotations

import hashlib
import json
import gzip
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from rich.panel import Panel
from rich.table import Table

from . import planner
from .models import Plan


STATUS_PENDING = "Pending"
STATUS_COMPLETED = "Completed"
STATUS_RERUN = "Rerun"


@dataclass
class ResumePreview:
    """断点续跑的预览信息，供 CLI/UI 展示。"""

    plan_matches: bool
    skipped_indices: set[int]
    completed: list[str]
    pending: list[str]
    failed: list[str]
    missing_outputs: list[str]
    total: int
    state_path: Path


@dataclass
class CommandRow:
    index: int
    name: str
    status: str
    note: str


def command_canonical_preview(command: planner.PlannedCommand) -> str:
    """返回用于断点续跑匹配的“规范化命令”字符串。

    目标：允许用户调整线程数或仅修改后续步骤时，已完成步骤仍能被匹配并跳过。
    - cactus*: 忽略 `--maxCores`（线程覆盖由 RunSettings 注入，改动不应导致续跑失效）
    - ramax: 忽略 `--threads`
    """

    return _canonical_shell_preview(command.command)


def command_stable_key(command: planner.PlannedCommand) -> str:
    """基于 display_name + canonical_preview 的稳定键，用于跨运行匹配。"""

    display = command.display_name or ""
    canonical = command_canonical_preview(command)
    return _stable_key(display, canonical)


def index_state_commands(entries: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """把 run_state.json 中的 commands 索引为 stable_key -> entry。兼容旧格式键名。"""

    indexed: dict[str, dict[str, Any]] = {}
    for _key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        stable = entry.get("stable_key")
        if not stable:
            display = str(entry.get("display_name") or "")
            preview = str(entry.get("preview") or "")
            canonical = str(entry.get("canonical_preview") or _canonical_shell_preview_from_shell(preview))
            stable = _stable_key(display, canonical)
        indexed[stable] = entry
    return indexed


def preview_resume(
    plan: Plan,
    *,
    base_dir: Optional[Path] = None,
    thread_count: Optional[int] = None,
) -> ResumePreview | None:
    """读取 run_state.json，返回续跑预览；文件缺失或无法解析时返回 None。"""

    base = Path(base_dir) if base_dir else Path.cwd()
    commands = planner.build_execution_plan(plan, base, thread_count=thread_count)
    log_root = _log_root_for_plan(plan, base)
    state_path = log_root / "run_state.json"
    data = load_run_state_file(state_path)
    if not data:
        return None

    current_sig = plan_signature(commands, base, thread_count)
    plan_matches = data.get("plan_signature") == current_sig
    entries = index_state_commands(data.get("commands", {}))

    skipped_indices = _prefix_skipped_indices(commands, entries, base)
    completed: list[str] = []
    failed: list[str] = []
    missing_outputs: list[str] = []

    for idx, cmd in enumerate(commands):
        entry = entries.get(command_stable_key(cmd))
        outputs_ok = outputs_exist(cmd, base)
        if idx in skipped_indices:
            completed.append(cmd.display_name)
            continue
        if not entry:
            continue
        status = entry.get("status")
        if status == "success" and not outputs_ok:
            missing_outputs.append(cmd.display_name)
        elif status == "failed":
            failed.append(cmd.display_name)

    pending = [cmd.display_name for i, cmd in enumerate(commands) if i not in skipped_indices]

    return ResumePreview(
        plan_matches=plan_matches,
        skipped_indices=skipped_indices,
        completed=completed,
        pending=pending,
        failed=failed,
        missing_outputs=missing_outputs,
        total=len(commands),
        state_path=state_path,
    )


def command_rows(
    plan: Plan,
    *,
    base_dir: Optional[Path] = None,
    thread_count: Optional[int] = None,
) -> list[CommandRow]:
    """返回每条命令的状态行，用于 UI 展示。"""

    base = Path(base_dir) if base_dir else Path.cwd()
    commands = planner.build_execution_plan(plan, base, thread_count=thread_count)
    log_root = _log_root_for_plan(plan, base)
    state_path = log_root / "run_state.json"
    data = load_run_state_file(state_path)
    entries = index_state_commands(data.get("commands", {}) if data else {})
    skipped_indices = _prefix_skipped_indices(commands, entries, base)

    rows: list[CommandRow] = []
    for idx, cmd in enumerate(commands):
        entry = entries.get(command_stable_key(cmd))
        outputs_ok = outputs_exist(cmd, base)
        status = STATUS_PENDING
        note = ""

        if idx in skipped_indices:
            status = STATUS_COMPLETED
            note = "Will be skipped (succeeded and outputs exist)"
        elif entry:
            s = entry.get("status")
            status = STATUS_RERUN
            if s == "success" and outputs_ok:
                note = "Previously succeeded, but an earlier step will rerun"
            elif s == "success" and not outputs_ok:
                note = "Outputs missing or inconsistent; will rerun"
            elif s == "failed":
                note = f"Failed last run (exit {entry.get('exit_code')}); will rerun"
            elif s == "running":
                note = "Interrupted/terminated last run; will rerun"
            else:
                note = "Will rerun"
        elif outputs_ok:
            note = "Outputs exist (no recorded success)"
        rows.append(CommandRow(index=idx, name=cmd.display_name, status=status, note=note))
    return rows


def render_summary(preview: ResumePreview, sample_limit: int = 3) -> tuple[Table, Panel]:
    """生成续跑摘要的 Rich 渲染对象。"""

    table = Table(title="Resume Summary", show_header=True, header_style="bold magenta")
    table.add_column("Category", style="cyan", no_wrap=True)
    table.add_column("Count", justify="right")
    table.add_column("Examples", overflow="fold")

    table.add_row("Skippable (completed)", str(len(preview.completed)), _sample_items(preview.completed, sample_limit))
    table.add_row(STATUS_PENDING, str(len(preview.pending)), _sample_items(preview.pending, sample_limit))
    table.add_row("Needs rerun (outputs)", str(len(preview.missing_outputs)), _sample_items(preview.missing_outputs, sample_limit))
    table.add_row("Failed last run", str(len(preview.failed)), _sample_items(preview.failed, sample_limit))

    panel = Panel(f"State file: {preview.state_path}", border_style="yellow")
    return table, panel


def render_command_table(rows: list[CommandRow], *, limit: int = 200) -> Table:
    """渲染每条命令的状态表。默认最多展示 200 行，超出会在末尾提示。"""

    table = Table(title="Resume Commands (in order)", show_header=True, header_style="bold cyan")
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Status", style="magenta", no_wrap=True)
    table.add_column("Command", overflow="fold")
    table.add_column("Note", overflow="fold")

    shown = 0
    for row in rows:
        if shown >= limit:
            break
        table.add_row(str(row.index), row.status, row.name, row.note)
        shown += 1

    if len(rows) > shown:
        table.caption = f"... {len(rows) - shown} more not shown"
    return table


# ---- shared helpers -----------------------------------------------------

def outputs_exist(command: planner.PlannedCommand, base_dir: Path) -> bool:
    step = command.step
    if step is None or not step.out_files:
        return True
    resolved: list[Path] = []
    for out_file in step.out_files:
        path = Path(out_file).expanduser()
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        resolved.append(path)
        if not path.exists():
            return False
    if step.kind == "blast":
        paf_paths = [p for p in resolved if p.name.endswith(".paf")]
        if paf_paths and not _blast_paf_matches_seqfile(command, base_dir, paf_paths):
            return False
    return True


def load_run_state_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except (OSError, json.JSONDecodeError):
        return {}

def plan_signature(
    commands: list[planner.PlannedCommand],
    base_dir: Path,
    thread_count: Optional[int],
) -> str:
    hasher = hashlib.sha1()
    hasher.update(str(base_dir).encode())
    hasher.update(str(thread_count or "").encode())
    for cmd in commands:
        hasher.update(cmd.shell_preview().encode())
        if cmd.workdir:
            hasher.update(str(cmd.workdir).encode())
    return hasher.hexdigest()


def _stable_key(display_name: str, canonical_preview: str) -> str:
    hasher = hashlib.sha1()
    hasher.update(display_name.encode())
    hasher.update(b"\0")
    hasher.update(canonical_preview.encode())
    return hasher.hexdigest()


def _strip_flag(tokens: list[str], flag: str) -> list[str]:
    stripped: list[str] = []
    skip_next = False
    prefix = flag + "="
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token == flag:
            skip_next = True
            continue
        if token.startswith(prefix):
            continue
        stripped.append(token)
    return stripped


def _strip_switch(tokens: list[str], flag: str) -> list[str]:
    """移除不带参数的布尔开关（如 `--restart`）。"""

    stripped: list[str] = []
    prefix = flag + "="
    for token in tokens:
        if token == flag:
            continue
        if token.startswith(prefix):
            continue
        stripped.append(token)
    return stripped


def _canonical_shell_preview(tokens: list[str]) -> str:
    if not tokens:
        return ""
    name = Path(tokens[0]).name.lower()
    canonical_tokens = list(tokens)
    if name.startswith("cactus"):
        canonical_tokens = _strip_flag(canonical_tokens, "--maxCores")
        # Toil 的 `--restart` 属于“运行方式”而非产物语义，不应影响续跑匹配。
        canonical_tokens = _strip_switch(canonical_tokens, "--restart")
    if name == "ramax":
        canonical_tokens = _strip_flag(canonical_tokens, "--threads")
    return shlex.join(canonical_tokens)


def _canonical_shell_preview_from_shell(preview: str) -> str:
    if not preview:
        return ""
    try:
        tokens = shlex.split(preview)
    except ValueError:
        return preview
    return _canonical_shell_preview(tokens)


def _log_root_for_plan(plan: Plan, base_dir: Path) -> Path:
    if plan.out_dir:
        return _to_path(plan.out_dir, base_dir) / "logs"
    return (base_dir / "logs").resolve()


def _to_path(path_like: str, base_dir: Path) -> Path:
    path = Path(path_like).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _sample_items(items: list[str], limit: int) -> str:
    if not items:
        return "(none)"
    head = items[:limit]
    remaining = len(items) - len(head)
    suffix = f" (+{remaining} more)" if remaining > 0 else ""
    return ", ".join(head) + suffix


def _prefix_skipped_indices(
    commands: list[planner.PlannedCommand],
    entries: dict[str, dict[str, Any]],
    base_dir: Path,
) -> set[int]:
    """只跳过“前缀连续已完成步骤”。

    计划是顺序执行的，后续步骤往往依赖前置产物；一旦某一步需要重跑，
    其后的步骤也必须重新执行，避免出现“上游变更但下游仍被跳过”的不一致。
    """

    skipped: set[int] = set()
    for idx, cmd in enumerate(commands):
        entry = entries.get(command_stable_key(cmd))
        if entry and entry.get("status") == "success" and outputs_exist(cmd, base_dir):
            skipped.add(idx)
            continue
        break
    return skipped


def _blast_paf_matches_seqfile(
    command: planner.PlannedCommand,
    base_dir: Path,
    paf_paths: list[Path],
) -> bool:
    """快速校验 blast 产物 PAF 与当前 seqfile/FASTA 是否一致。

    经验问题：Toil jobStore 续跑（--restart）可能复用旧的中间结果，导致生成的 PAF 仍引用旧 FASTA 的 contig 名。
    若随后 internal FASTA 已被重跑并改名，align 步骤会出现 “Could not match contig name …”。

    这里做一个轻量校验：抽样读取 PAF 前若干行的 qname/tname，检查其 contig 名是否存在于 seqfile 指定的 FASTA 头部。
    """

    cmd_name = Path(command.command[0]).name.lower() if command.command else ""
    if cmd_name != "cactus-blast":
        return True
    seqfile_path = _seqfile_from_cactus_blast(command, base_dir)
    if seqfile_path is None or not seqfile_path.exists():
        return True
    mapping = _parse_seqfile_mapping(seqfile_path, base_dir)
    if not mapping:
        return True

    for paf_path in paf_paths:
        needed = _collect_needed_contigs_from_paf(paf_path, sample_limit=200)
        if not needed:
            continue
        for event, contigs in needed.items():
            fasta_path = mapping.get(event)
            if fasta_path is None:
                return False
            if not _fasta_contains_all_contigs(fasta_path, contigs):
                return False
    return True


def _seqfile_from_cactus_blast(command: planner.PlannedCommand, base_dir: Path) -> Path | None:
    # cactus-blast <jobstore> <seqfile> <out.paf> ...
    if len(command.command) < 3:
        return None
    token = command.command[2]
    path = Path(token).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _parse_seqfile_mapping(seqfile_path: Path, base_dir: Path) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    try:
        with seqfile_path.open("r", encoding="utf-8", errors="replace") as handle:
            _ = handle.readline()  # newick line
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                parts = stripped.split()
                if len(parts) < 2:
                    continue
                name, path_like = parts[0], parts[1]
                mapping[name] = _to_path(path_like, base_dir)
    except OSError:
        return {}
    return mapping


def _collect_needed_contigs_from_paf(paf_path: Path, *, sample_limit: int) -> dict[str, set[str]]:
    needed: dict[str, set[str]] = {}
    try:
        with paf_path.open("r", encoding="utf-8", errors="replace") as handle:
            seen = 0
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                fields = stripped.split("\t")
                if len(fields) < 6:
                    continue
                for name in (fields[0], fields[5]):
                    event, contig = _parse_paf_name(name)
                    if not event or not contig:
                        continue
                    needed.setdefault(event, set()).add(contig)
                seen += 1
                if seen >= sample_limit:
                    break
    except OSError:
        return {}
    return needed


def _parse_paf_name(name: str) -> tuple[str | None, str | None]:
    value = name.strip()
    if value.startswith("id="):
        value = value[len("id=") :]
    if "|" not in value:
        return None, None
    event, contig = value.split("|", 1)
    return (event or None), (contig or None)


def _fasta_contains_all_contigs(path: Path, contigs: set[str]) -> bool:
    remaining = set(contigs)
    if not remaining:
        return True
    try:
        opener = gzip.open if path.name.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.startswith(">"):
                    continue
                header = line[1:].strip().split()[0]
                if header in remaining:
                    remaining.remove(header)
                    if not remaining:
                        return True
    except OSError:
        return False
    return False
