"""Textual-based interactive UI for configuring CAX plans."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Footer, Header, ListItem, ListView, Static

from . import planner, render
from .models import Plan, Round


@dataclass
class UIResult:
    plan: Plan
    action: str
    payload: Optional[Path] = None


class RoundListItem(ListItem):
    """List item reflecting a round with RaMAx toggle."""

    def __init__(self, round_entry: Round, index: int):
        self.round_entry = round_entry
        self.index = index
        super().__init__(Static(self._text(), expand=True))

    def _text(self) -> str:
        status = "[x]" if self.round_entry.replace_with_ramax else "[ ]"
        return f"{status} {self.round_entry.name} ({self.round_entry.root})\n→ {self.round_entry.target_hal}"

    def update_content(self) -> None:
        static = self.query_one(Static)
        static.update(self._text())


class PlanUIApp(App[UIResult]):
    CSS = """
    Screen {
        layout: vertical;
    }
    #main {
        layout: horizontal;
    }
    #rounds {
        width: 45%;
        height: 1fr;
    }
    #details {
        width: 55%;
        padding: 1 2;
        overflow-y: auto;
    }
    """

    BINDINGS = [
        Binding("space", "toggle_round", "Toggle RaMAx"),
        Binding("e", "edit_round", "Edit workdir (todo)"),
        Binding("p", "preview_plan", "Preview"),
        Binding("s", "save_commands", "Save commands"),
        Binding("r", "run_plan", "Run"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, plan: Plan, base_dir: Optional[Path] = None):
        super().__init__()
        self.plan = plan
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.round_items: list[RoundListItem] = []
        self.round_list: ListView | None = None
        self.detail_panel: Static | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="main"):
            self.round_items = [RoundListItem(round_entry, idx) for idx, round_entry in enumerate(self.plan.rounds)]
            list_view = ListView(*self.round_items, id="rounds")
            self.round_list = list_view
            yield list_view
            detail = Static(render.plan_overview(self.plan), id="details")
            self.detail_panel = detail
            yield detail
        yield Footer()

    def on_mount(self) -> None:
        if self.round_list and self.round_items:
            self.round_list.index = 0
            self._show_round(0)

    def action_toggle_round(self) -> None:
        if not self.round_list:
            return
        index = self.round_list.index or 0
        if index >= len(self.plan.rounds):
            return
        round_entry = self.plan.rounds[index]
        round_entry.replace_with_ramax = not round_entry.replace_with_ramax
        self.round_items[index].update_content()
        self._show_round(index)

    def action_preview_plan(self) -> None:
        if self.detail_panel:
            preview = render.plan_overview(self.plan)
            self.detail_panel.update(preview)

    def action_run_plan(self) -> None:
        self.exit(UIResult(plan=self.plan, action="run"))

    def action_save_commands(self) -> None:
        output_dir = Path(self.plan.out_dir or self.base_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "ramax_commands.txt"
        commands = planner.build_execution_plan(self.plan, self.base_dir)
        lines = [cmd.shell_preview() for cmd in commands]
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if self.detail_panel:
            self.detail_panel.update(f"Saved commands to {output_path}")

    def action_quit(self) -> None:
        self.exit(UIResult(plan=self.plan, action="quit"))

    def action_edit_round(self) -> None:  # placeholder for future editing
        if self.detail_panel:
            self.detail_panel.update("Editing workdir not yet implemented in this preview UI.")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._show_round(event.index)

    def _show_round(self, index: int) -> None:
        if not self.detail_panel or index >= len(self.plan.rounds):
            return
        round_entry = self.plan.rounds[index]
        details = [f"[bold]{round_entry.name}[/bold] root={round_entry.root}"]
        if round_entry.replace_with_ramax:
            commands = planner.build_execution_plan(self.plan, self.base_dir)
            ramax_cmd = next((cmd for cmd in commands if cmd.round_name == round_entry.name and cmd.is_ramax), None)
            if ramax_cmd:
                details.append("\n[green]RaMAx command[/green]")
                details.append(f"{ramax_cmd.shell_preview()}")
        else:
            if round_entry.blast_step:
                details.append("\n[cyan]cactus-blast[/cyan]")
                details.append(round_entry.blast_step.raw)
            if round_entry.align_step:
                details.append("\n[cyan]cactus-align[/cyan]")
                details.append(round_entry.align_step.raw)
        if round_entry.hal2fasta_steps:
            details.append("\n[magenta]hal2fasta[/magenta]")
            details.extend(step.raw for step in round_entry.hal2fasta_steps)
        self.detail_panel.update("\n".join(details))


def launch(plan: Plan, base_dir: Optional[Path] = None) -> UIResult:
    """Run the Textual UI and return the resulting plan/action."""

    app = PlanUIApp(plan, base_dir=base_dir)
    result = app.run()
    if isinstance(result, UIResult):
        return result
    return UIResult(plan=plan, action="quit")
