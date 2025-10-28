"""Rendering helpers for previews and shell exports."""
from __future__ import annotations

import shlex
from io import StringIO
from typing import Iterable, Optional

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .models import Plan
from .planner import PlannedCommand


def plan_overview(plan: Plan) -> Panel:
    """Return a Rich Panel summarising the plan (auto-resizes in UI)."""

    table = Table(
        title="Cactus → RaMAx Plan",
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("Round", overflow="fold")
    table.add_column("Root", overflow="fold")
    table.add_column("Target HAL", overflow="fold")
    table.add_column("RaMAx?", overflow="fold")
    table.add_column("Workdir", overflow="fold")

    for round_entry in plan.rounds:
        table.add_row(
            round_entry.name,
            round_entry.root,
            round_entry.target_hal,
            "yes" if round_entry.replace_with_ramax else "no",
            round_entry.workdir or "",
        )

    footer = Text(f"Verbose logging: {'on' if plan.verbose else 'off'}", style="dim")
    content = Group(table, footer)
    return Panel(content, border_style="magenta", expand=True)


def environment_summary_card(
    environment: dict[str, Optional[str]],
    resources: dict[str, str],
) -> Panel:
    """Return a Rich Panel that adapts to container width (no pre-rendering)."""

    def value_or_missing(value: Optional[str]) -> str:
        return value if value else "未检测到"

    def oneline(value: Optional[str]) -> str:
        if not value:
            return "未检测到"
        lines = value.splitlines()
        if not lines:
            return value
        return lines[0] if len(lines) == 1 else f"{lines[0]} (＋{len(lines)-1} more)"

    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(ratio=1)

    def entry(name: str, value: Optional[str]) -> Text:
        text = Text()
        text.append(f"{name}: ", style="bold cyan")
        text.append(oneline(value))
        return text

    table.add_row(entry("RaMAx 路径", environment.get("ramax_path")))
    table.add_row(entry("RaMAx 版本", environment.get("ramax_version")))
    table.add_row(entry("cactus 路径", environment.get("cactus_path")))
    table.add_row(entry("cactus 版本", environment.get("cactus_version")))
    table.add_row(entry("GPU", environment.get("gpu")))
    table.add_row(entry("CPU 核心数", resources.get("cpu_count")))
    table.add_row(entry("内存 (GB)", resources.get("memory_gb")))
    table.add_row(entry("磁盘可用 (GB)", resources.get("disk_free_gb")))

    panel = Panel(table, title="环境摘要", border_style="cyan", expand=True)
    return panel


def render_run_script(plan: Plan, commands: Iterable[PlannedCommand]) -> str:
    """Create a bash script representing the execution order."""

    lines = ["#!/usr/bin/env bash", "set -euo pipefail", "", "# Generated from cactus-prepare plan"]
    for command in commands:
        lines.append(f"# {command.display_name}")
        lines.append(shlex.join(command.command))
        lines.append("")
    script = "\n".join(lines).rstrip() + "\n"
    return script
