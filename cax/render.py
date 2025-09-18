"""Rendering helpers for previews and shell exports."""
from __future__ import annotations

from io import StringIO
import shlex
from typing import Iterable

from rich.console import Console
from rich.table import Table

from .models import Plan
from .planner import PlannedCommand


def plan_overview(plan: Plan) -> str:
    """Return a rich table summarising the plan."""

    buf = StringIO()
    console = Console(file=buf, force_terminal=True, color_system=None)

    table = Table(title="Cactus → RaMAx Plan", show_header=True, header_style="bold magenta")
    table.add_column("Round")
    table.add_column("Root")
    table.add_column("Target HAL")
    table.add_column("RaMAx?")
    table.add_column("Workdir")

    for round_entry in plan.rounds:
        table.add_row(
            round_entry.name,
            round_entry.root,
            round_entry.target_hal,
            "yes" if round_entry.replace_with_ramax else "no",
            round_entry.workdir or "",
        )

    console.print(table)
    return buf.getvalue()


def render_run_script(plan: Plan, commands: Iterable[PlannedCommand]) -> str:
    """Create a bash script representing the execution order."""

    lines = ["#!/usr/bin/env bash", "set -euo pipefail", "", "# Generated from cactus-prepare plan"]
    for command in commands:
        lines.append(f"# {command.display_name}")
        lines.append(shlex.join(command.command))
        lines.append("")
    script = "\n".join(lines).rstrip() + "\n"
    return script
