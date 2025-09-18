"""Translate plans into executable command sequences."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shlex
from typing import Iterable, List, Optional

from .models import Plan, Round, Step


@dataclass
class PlannedCommand:
    """Concrete command to be executed as part of a plan."""

    command: List[str]
    category: str
    display_name: str
    log_path: Optional[Path] = None
    round_name: Optional[str] = None
    step: Optional[Step] = None
    is_ramax: bool = False
    fallback_steps: list[Step] = field(default_factory=list)
    workdir: Optional[Path] = None

    def shell_preview(self) -> str:
        """Return a shell-friendly preview of the command."""

        return shlex.join(self.command)


def build_execution_plan(plan: Plan, base_dir: Optional[Path] = None) -> list[PlannedCommand]:
    """Materialise the full list of commands that should be executed."""

    base_dir = base_dir or Path.cwd()
    commands: list[PlannedCommand] = []

    for step in plan.preprocess:
        commands.append(_from_step(step, category="preprocess", base_dir=base_dir))

    for round_entry in plan.rounds:
        commands.extend(_round_commands(plan, round_entry, base_dir))

    for step in plan.hal_merges:
        commands.append(_from_step(step, category="halmerge", base_dir=base_dir))

    return commands


def _round_commands(plan: Plan, round_entry: Round, base_dir: Path) -> list[PlannedCommand]:
    cmds: list[PlannedCommand] = []
    round_name = round_entry.name

    if round_entry.replace_with_ramax:
        cmds.append(
            _ramax_command(plan, round_entry, base_dir, fallback_policy=plan.fallback_policy)
        )
    else:
        if round_entry.blast_step:
            cmds.append(
                _from_step(
                    round_entry.blast_step,
                    category="blast",
                    base_dir=base_dir,
                    round_name=round_name,
                )
            )
        if round_entry.align_step:
            cmds.append(
                _from_step(
                    round_entry.align_step,
                    category="align",
                    base_dir=base_dir,
                    round_name=round_name,
                )
            )

    for hal_step in round_entry.hal2fasta_steps:
        cmds.append(
            _from_step(
                hal_step,
                category="hal2fasta",
                base_dir=base_dir,
                round_name=round_name,
            )
        )

    return cmds


def _from_step(
    step: Step,
    category: str,
    base_dir: Path,
    round_name: Optional[str] = None,
) -> PlannedCommand:
    command = _split_command(step.raw)
    if step.kind == "hal2fasta":
        command = _normalize_hal2fasta(command)
    log_path = Path(step.log_file) if step.log_file else None
    display_name = step.short_label()
    return PlannedCommand(
        command=command,
        category=category,
        display_name=display_name,
        log_path=_resolve_path(log_path, base_dir) if log_path else None,
        round_name=round_name,
        step=step,
    )


def _ramax_command(plan: Plan, round_entry: Round, base_dir: Path, fallback_policy: str) -> PlannedCommand:
    workdir = round_entry.workdir
    if not workdir and plan.out_dir:
        workdir = str(Path(plan.out_dir) / "temps" / f"blast-{round_entry.root}")
    command: list[str] = [
        "RaMAx",
        "-i",
        plan.out_seq_file,
        "-o",
        round_entry.target_hal,
        "--root",
        round_entry.root,
    ]
    if workdir:
        command.extend(["-w", workdir])
    command.extend(plan.global_ramax_opts)
    command.extend(round_entry.ramax_opts)

    log_path = _guess_ramax_log_path(plan, round_entry, base_dir)

    fallback_steps: list[Step] = []
    if fallback_policy == "cactus":
        if round_entry.blast_step:
            fallback_steps.append(round_entry.blast_step)
        if round_entry.align_step:
            fallback_steps.append(round_entry.align_step)

    workdir_path = Path(workdir).expanduser() if workdir else None
    if workdir_path and not workdir_path.is_absolute():
        workdir_path = (base_dir / workdir_path).resolve()
    return PlannedCommand(
        command=command,
        category="ramax",
        display_name=f"RaMAx-{round_entry.root}",
        log_path=log_path,
        round_name=round_entry.name,
        step=None,
        is_ramax=True,
        fallback_steps=fallback_steps,
        workdir=workdir_path,
    )


def _guess_ramax_log_path(plan: Plan, round_entry: Round, base_dir: Path) -> Optional[Path]:
    if round_entry.align_step and round_entry.align_step.log_file:
        align_log = Path(round_entry.align_step.log_file)
        ramax_name = align_log.name.replace("align", "ramax")
        return _resolve_path(align_log.with_name(ramax_name), base_dir)
    if plan.out_dir:
        return _resolve_path(Path(plan.out_dir) / "logs" / f"ramax-{round_entry.root}.log", base_dir)
    return None


def _resolve_path(path: Path, base_dir: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded
    return (base_dir / expanded).resolve()


def _split_command(raw: str) -> List[str]:
    try:
        return shlex.split(raw)
    except ValueError:
        return raw.split()


def _normalize_hal2fasta(command: List[str]) -> List[str]:
    """Normalize a hal2fasta invocation to avoid shell redirection.

    cactus-prepare emits commands like:
        hal2fasta in.hal Anc0 --hdf5InMemory > out.fa

    Since we execute with ``shell=False``, the '>' token is treated as an
    argument and hal2fasta fails. This helper converts the redirection to the
    explicit ``--outFaPath`` option that hal2fasta supports.
    """

    if ">" not in command and ">>" not in command:
        return command

    # Identify redirection token and the output path following it
    redirect_token = ">" if ">" in command else ">>"
    try:
        redirect_index = command.index(redirect_token)
    except ValueError:
        return command

    out_path = command[redirect_index + 1] if redirect_index + 1 < len(command) else None
    # Keep the main part of the command before redirection
    main = command[:redirect_index]

    # Remove any existing --outFaPath occurrences to avoid duplicates
    cleaned: List[str] = []
    skip_next = False
    for token in main:
        if skip_next:
            skip_next = False
            continue
        if token == "--outFaPath":
            skip_next = True
            continue
        cleaned.append(token)

    if out_path:
        cleaned.extend(["--outFaPath", out_path])
    return cleaned
