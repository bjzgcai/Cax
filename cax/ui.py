"""Textual-based interactive UI for configuring CAX plans."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, ListItem, ListView, Static, Tree

from rich.text import Text

from . import planner, render, tree_utils
from .models import Plan, Round, Step


@dataclass
class UIResult:
    plan: Plan
    action: str
    payload: Optional[Path] = None


@dataclass
class CommandTarget:
    """Represents an editable command associated with a round."""

    key: str
    label: str
    command: str
    kind: str
    step: Step | None = None
    index: int | None = None


class CommandSelectionModal(ModalScreen[CommandTarget | None]):
    """Modal dialog listing all editable commands for a round."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = """
    CommandSelectionModal {
        align: center middle;
    }
    #picker-dialog {
        padding: 1 2;
        width: 80%;
        max-width: 80;
        border: round $accent;
        background: $panel;
    }
    #picker-title {
        padding-bottom: 1;
    }
    #picker-list {
        height: auto;
        max-height: 20;
    }
    #picker-hint {
        padding-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, targets: list[CommandTarget]):
        super().__init__()
        self.targets = targets
        self._list_view: ListView | None = None

    def compose(self) -> ComposeResult:
        with Container(id="picker-dialog"):
            yield Static("Choose a command to edit", id="picker-title")
            items = []
            for target in self.targets:
                text = Text(target.label, style="bold")
                text.append("\n")
                text.append(target.command)
                items.append(ListItem(Static(text, expand=True)))
            list_view = ListView(*items, id="picker-list")
            self._list_view = list_view
            yield list_view
            yield Static("Enter to confirm, Esc to cancel", id="picker-hint")

    def on_mount(self) -> None:
        if self._list_view:
            self._list_view.index = 0
            self.set_focus(self._list_view)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.index < len(self.targets):
            self.dismiss(self.targets[event.index])

    def action_cancel(self) -> None:
        self.dismiss(None)


class CommandEditModal(ModalScreen[str | None]):
    """Modal dialog allowing the user to edit a command string."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
        Binding("enter", "save", "Save"),
    ]

    CSS = """
    CommandEditModal {
        align: center middle;
    }
    #editor-dialog {
        padding: 1 2;
        width: 80%;
        max-width: 90;
        border: round $accent;
        background: $panel;
    }
    #editor-title {
        padding-bottom: 1;
    }
    #editor-command {
        margin-bottom: 1;
    }
    #editor-buttons {
        layout: horizontal;
        height: auto;
        padding-top: 1;
    }
    #editor-buttons Button {
        margin-right: 1;
    }
    #editor-status {
        color: $error;
    }
    """

    def __init__(self, title: str, initial_command: str):
        super().__init__()
        self.title = title
        self.initial_command = initial_command
        self._input: Input | None = None
        self._status: Static | None = None

    def compose(self) -> ComposeResult:
        with Container(id="editor-dialog"):
            yield Static(self.title, id="editor-title")
            input_widget = Input(value=self.initial_command, id="editor-command")
            self._input = input_widget
            yield input_widget
            status = Static("", id="editor-status")
            self._status = status
            yield status
            with Container(id="editor-buttons"):
                yield Button("Save", id="save", variant="success")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        if self._input:
            self._input.focus()
            self._input.cursor_position = len(self._input.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_save()

    def action_save(self) -> None:
        if not self._input:
            self.dismiss(None)
            return
        value = self._input.value.strip()
        if not value:
            if self._status:
                self._status.update("Command cannot be empty")
            return
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        else:
            self.action_cancel()

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


class AlignmentTreeWidget(Tree[tree_utils.AlignmentNode]):
    """Tree widget with modified key bindings to cooperate with the global space toggle."""

    BINDINGS = [
        binding
        for binding in Tree.BINDINGS
        if binding.key != "space"
    ] + [
        Binding("space", "toggle_subtree", "Toggle subtree", show=False),
        Binding("ctrl+space", "toggle_node", "Expand/Collapse", show=False),
    ]

    def action_toggle_subtree(self) -> None:
        toggle_action = getattr(self.app, "action_toggle_round", None)
        if callable(toggle_action):
            toggle_action()


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
        Binding("space", "toggle_round", "Toggle subtree"),
        Binding("e", "edit_round", "Edit command"),
        Binding("p", "preview_plan", "Preview"),
        Binding("s", "save_commands", "Save commands"),
        Binding("r", "run_plan", "Run"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, plan: Plan, base_dir: Optional[Path] = None):
        super().__init__()
        self.plan = plan
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.alignment_tree = tree_utils.build_alignment_tree(plan, base_dir=self.base_dir)
        self.tree_widget: AlignmentTreeWidget | None = None
        self.round_items: list[RoundListItem] = []
        self.round_list: ListView | None = None
        self.detail_panel: Static | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="main"):
            if self.alignment_tree:
                root_node = self.alignment_tree.root
                tree_widget = AlignmentTreeWidget(
                    self._format_node_label(root_node),
                    data=root_node,
                    id="rounds",
                )
                self.tree_widget = tree_widget
                self._populate_tree(tree_widget.root, root_node)
                self._refresh_tree_labels(tree_widget.root)
                yield tree_widget
            else:
                self.round_items = [
                    RoundListItem(round_entry, idx)
                    for idx, round_entry in enumerate(self.plan.rounds)
                ]
                list_view = ListView(*self.round_items, id="rounds")
                self.round_list = list_view
                yield list_view
            detail = Static(render.plan_overview(self.plan), id="details")
            self.detail_panel = detail
            yield detail
        yield Footer()

    def on_mount(self) -> None:
        if self.tree_widget and self.alignment_tree:
            self.tree_widget.root.expand_all()
            self.tree_widget.move_cursor(self.tree_widget.root, animate=False)
            self.set_focus(self.tree_widget)
            self._show_alignment_node(self.alignment_tree.root)
        elif self.round_list and self.round_items:
            self.round_list.index = 0
            self._show_round(0)

    def action_toggle_round(self) -> None:
        if self.alignment_tree and self.tree_widget:
            node = self._selected_alignment_node()
            if node is None:
                return
            rounds = list(node.iter_rounds())
            if not rounds:
                if self.detail_panel:
                    self.detail_panel.update("This node has no cactus steps that can be replaced.")
                return
            target_state = not all(round_entry.replace_with_ramax for round_entry in rounds)
            for round_entry in rounds:
                round_entry.replace_with_ramax = target_state
            self._refresh_tree_labels(self.tree_widget.root)
            status = (
                f"Switched {len(rounds)} rounds in this subtree to RaMAx"
                if target_state
                else f"Restored {len(rounds)} rounds in this subtree to cactus"
            )
            self._show_alignment_node(node, status=status)
            return

        if not self.round_list:
            return
        index = self.round_list.index or 0
        if index >= len(self.plan.rounds):
            return
        round_entry = self.plan.rounds[index]
        round_entry.replace_with_ramax = not round_entry.replace_with_ramax
        self.round_items[index].update_content()
        status = "Switched to RaMAx" if round_entry.replace_with_ramax else "Restored to cactus"
        self._show_round(index, status=status)

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
            self.detail_panel.update(f"Commands saved to {output_path}")

    def action_quit(self) -> None:
        self.exit(UIResult(plan=self.plan, action="quit"))

    def action_edit_round(self) -> None:
        if self.alignment_tree and self.tree_widget:
            node = self._selected_alignment_node()
            if node is None or node.round is None:
                if self.detail_panel:
                    self.detail_panel.update("Select a node with cactus rounds before pressing E.")
                return
            try:
                round_index = self.plan.rounds.index(node.round)
            except ValueError:
                return
            targets = self._gather_command_targets(node.round)
            if not targets:
                if self.detail_panel:
                    self.detail_panel.update("No editable commands in this node.")
                return
            if len(targets) == 1:
                self._open_command_editor(round_index, targets[0])
            else:
                self.push_screen(
                    CommandSelectionModal(targets),
                    lambda target: self._handle_command_selection(round_index, target),
                )
            return

        if not self.round_list:
            return
        index = self.round_list.index or 0
        if index >= len(self.plan.rounds):
            return
        round_entry = self.plan.rounds[index]
        targets = self._gather_command_targets(round_entry)
        if not targets:
            if self.detail_panel:
                self.detail_panel.update("No editable commands for this round.")
            return
        if len(targets) == 1:
            self._open_command_editor(index, targets[0])
        else:
            self.push_screen(
                CommandSelectionModal(targets),
                lambda target: self._handle_command_selection(index, target),
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._show_round(event.index)

    def on_alignmenttreewidget_node_highlighted(
        self, event: AlignmentTreeWidget.NodeHighlighted
    ) -> None:
        node = event.node.data
        if node is not None:
            self._show_alignment_node(node)

    def on_alignmenttreewidget_node_selected(
        self, event: AlignmentTreeWidget.NodeSelected
    ) -> None:
        node = event.node.data
        if node is not None:
            self._show_alignment_node(node)

    def _round_details(self, round_entry: Round) -> list[str]:
        details = [f"[bold]{round_entry.name}[/bold] root={round_entry.root}"]
        if round_entry.replace_with_ramax:
            ramax_preview = self._ramax_command_preview(round_entry)
            if ramax_preview:
                details.extend(["", "[green]RaMAx command[/green]", ramax_preview])
        else:
            if round_entry.blast_step:
                details.extend(["", "[cyan]cactus-blast[/cyan]", round_entry.blast_step.raw])
            if round_entry.align_step:
                details.extend(["", "[cyan]cactus-align[/cyan]", round_entry.align_step.raw])
        if round_entry.hal2fasta_steps:
            details.append("")
            details.append("[magenta]hal2fasta[/magenta]")
            details.extend(step.raw for step in round_entry.hal2fasta_steps)
        return details

    def _show_round(self, index: int, status: str | None = None) -> None:
        if not self.detail_panel or index >= len(self.plan.rounds):
            return
        round_entry = self.plan.rounds[index]
        details = self._round_details(round_entry)
        if status:
            details.extend(["", f"[green]{status}[/green]"])
        self.detail_panel.update("\n".join(details))

    def _gather_command_targets(self, round_entry: Round) -> list[CommandTarget]:
        targets: list[CommandTarget] = []
        if round_entry.replace_with_ramax:
            ramax_preview = self._ramax_command_preview(round_entry)
            targets.append(
                CommandTarget(
                    key="ramax",
                    label="RaMAx",
                    command=ramax_preview,
                    kind="ramax",
                )
            )
        else:
            if round_entry.blast_step:
                targets.append(
                    CommandTarget(
                        key="blast",
                        label="cactus-blast",
                        command=round_entry.blast_step.raw,
                        kind="blast",
                        step=round_entry.blast_step,
                    )
                )
            if round_entry.align_step:
                targets.append(
                    CommandTarget(
                        key="align",
                        label="cactus-align",
                        command=round_entry.align_step.raw,
                        kind="align",
                        step=round_entry.align_step,
                    )
                )
        for idx, step in enumerate(round_entry.hal2fasta_steps):
            label = "hal2fasta" if len(round_entry.hal2fasta_steps) == 1 else f"hal2fasta #{idx + 1}"
            targets.append(
                CommandTarget(
                    key=f"hal2fasta-{idx}",
                    label=label,
                    command=step.raw,
                    kind="hal2fasta",
                    step=step,
                    index=idx,
                )
            )
        return targets

    def _show_alignment_node(
        self,
        node: tree_utils.AlignmentNode,
        status: str | None = None,
    ) -> None:
        if not self.detail_panel:
            return
        details: list[str] = []
        if node.round:
            details.extend(self._round_details(node.round))
        else:
            title = node.name or "(unnamed node)"
            details.append(f"[bold]{title}[/bold]")
        subtree_rounds = list(node.iter_rounds())
        if subtree_rounds:
            replaced = sum(1 for round_entry in subtree_rounds if round_entry.replace_with_ramax)
            details.extend(
                [
                    "",
                    f"Subtree summary: RaMAx {replaced}/{len(subtree_rounds)} rounds",
                ]
            )
        else:
            details.extend(["", "No cactus rounds in this subtree (leaf node)."])
        if status:
            details.extend(["", f"[green]{status}[/green]"])
        self.detail_panel.update("\n".join(details))

    def _selected_alignment_node(self) -> tree_utils.AlignmentNode | None:
        if not self.tree_widget:
            return None
        cursor = self.tree_widget.cursor_node
        return cursor.data if cursor is not None else None

    def _find_node_for_round(self, round_entry: Round) -> tree_utils.AlignmentNode | None:
        if not self.alignment_tree:
            return None
        return self.alignment_tree.find(round_entry.root)

    def _populate_tree(
        self,
        tree_node,
        alignment_node: tree_utils.AlignmentNode,
    ) -> None:
        for child in alignment_node.children:
            label = self._format_node_label(child)
            child_node = tree_node.add(label, data=child)
            if child.children:
                self._populate_tree(child_node, child)

    def _refresh_tree_labels(self, tree_node) -> None:
        alignment_node = tree_node.data
        if alignment_node is not None:
            tree_node.set_label(self._format_node_label(alignment_node))
        for child in tree_node.children:
            self._refresh_tree_labels(child)

    def _format_node_label(self, node: tree_utils.AlignmentNode) -> str:
        state = self._node_state(node)
        name = node.name or "(unnamed)"
        if state == "leaf":
            return f"    {name}"
        marker = {"checked": "[x]", "unchecked": "[ ]", "mixed": "[-]"}[state]
        return f"{marker} {name}"

    def _node_state(self, node: tree_utils.AlignmentNode) -> str:
        has_round = node.round is not None
        if node.round is not None:
            enabled = node.round.replace_with_ramax
            any_enabled = enabled
            all_enabled = enabled
        else:
            any_enabled = False
            all_enabled = True
        for child in node.children:
            child_state = self._node_state(child)
            if child_state == "leaf":
                continue
            has_round = True
            if child_state == "checked":
                any_enabled = True
            elif child_state == "mixed":
                any_enabled = True
                all_enabled = False
            elif child_state == "unchecked":
                all_enabled = False
        if not has_round:
            return "leaf"
        if all_enabled and any_enabled:
            return "checked"
        if any_enabled:
            return "mixed"
        return "unchecked"

    def _handle_command_selection(self, round_index: int, target: CommandTarget | None) -> None:
        if target is None:
            return
        self._open_command_editor(round_index, target)

    def _open_command_editor(self, round_index: int, target: CommandTarget) -> None:
        editor = CommandEditModal(f"Edit {target.label} command", target.command)
        self.push_screen(
            editor,
            lambda new_command: self._apply_command_edit(round_index, target, new_command),
        )

    def _apply_command_edit(
        self, round_index: int, target: CommandTarget, new_command: str | None
    ) -> None:
        if new_command is None or round_index >= len(self.plan.rounds):
            return
        round_entry = self.plan.rounds[round_index]
        if target.kind == "ramax":
            round_entry.manual_ramax_command = new_command
        elif target.step is not None:
            target.step.raw = new_command
        status = f"Updated {target.label} command"
        if self.alignment_tree and self.tree_widget:
            node = self._find_node_for_round(round_entry)
            if node:
                self._refresh_tree_labels(self.tree_widget.root)
                self._show_alignment_node(node, status=status)
                return
        self._show_round(round_index, status=status)

    def _ramax_command_preview(self, round_entry: Round) -> str:
        if round_entry.manual_ramax_command:
            return round_entry.manual_ramax_command
        commands = planner.build_execution_plan(self.plan, self.base_dir)
        for command in commands:
            if command.is_ramax and command.round_name == round_entry.name:
                return command.shell_preview()
        return ""


def launch(plan: Plan, base_dir: Optional[Path] = None) -> UIResult:
    """Run the Textual UI and return the resulting plan/action."""

    app = PlanUIApp(plan, base_dir=base_dir)
    result = app.run()
    if isinstance(result, UIResult):
        return result
    return UIResult(plan=plan, action="quit")
