"""Textual-based interactive UI for configuring CAX plans."""
from __future__ import annotations

import math
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Checkbox, Footer, Header, Input, ListItem, ListView, Static, TextArea

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import planner, tree_utils
from .models import Plan, Round, RunSettings, Step
from .planner import PlannedCommand


@dataclass
class UIResult:
    plan: Plan
    action: str
    payload: Optional[Path] = None
    run_settings: RunSettings | None = None


@dataclass
class CommandTarget:
    """Represents an editable command associated with a round."""

    key: str
    label: str
    command: str
    kind: str
    step: Step | None = None
    index: int | None = None


def plan_overview(plan: Plan, run_settings: Optional[RunSettings] = None, compact: bool = False) -> Panel:
    """Return a Rich Panel that summarizes the plan."""

    table = Table(
        title="Cactus → RaMAx Plan",
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    if compact:
        table.add_column("Round", overflow="ellipsis", no_wrap=True, ratio=2)
        table.add_column("Root", overflow="ellipsis", no_wrap=True, ratio=2)
        table.add_column("Target HAL", overflow="ellipsis", no_wrap=True, ratio=3)
        table.add_column("RaMAx?", overflow="ellipsis", no_wrap=True, ratio=1)
    else:
        table.add_column("Round", overflow="fold")
        table.add_column("Root", overflow="fold")
        table.add_column("Target HAL", overflow="fold")
        table.add_column("RaMAx?", overflow="fold")
        table.add_column("Workdir", overflow="fold")

    for round_entry in plan.rounds:
        row = [
            round_entry.name,
            round_entry.root,
            round_entry.target_hal,
            "yes" if round_entry.replace_with_ramax else "no",
        ]
        if not compact:
            row.append(round_entry.workdir or "")
        table.add_row(*row)

    settings = run_settings or RunSettings()
    thread_label = (
        "auto (command defaults)"
        if settings.thread_count is None
        else f"{settings.thread_count} threads (--maxCores/--threads)"
    )
    footer_text = f"Verbose logging: {'on' if settings.verbose else 'off'} | Thread target: {thread_label}"
    footer = Text(footer_text, style="dim")
    content = Group(table, footer)
    return Panel(content, border_style="magenta", expand=True)


def environment_summary_card(environment: dict[str, Optional[str]], resources: dict[str, str]) -> Panel:
    """Build an environment summary card that adapts to the UI width."""

    def oneline(value: Optional[str]) -> str:
        if not value:
            return "Not detected"
        lines = value.splitlines()
        if not lines:
            return value
        return lines[0] if len(lines) == 1 else f"{lines[0]} (+{len(lines)-1} more)"

    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(ratio=1)

    def entry(name: str, value: Optional[str]) -> Text:
        text = Text()
        text.append(f"{name}: ", style="bold cyan")
        text.append(oneline(value))
        return text

    table.add_row(entry("RaMAx path", environment.get("ramax_path")))
    table.add_row(entry("RaMAx version", environment.get("ramax_version")))
    table.add_row(entry("cactus path", environment.get("cactus_path")))
    table.add_row(entry("cactus version", environment.get("cactus_version")))
    table.add_row(entry("GPU", environment.get("gpu")))
    table.add_row(entry("CPU cores", resources.get("cpu_count")))
    table.add_row(entry("Memory (GB)", resources.get("memory_gb")))
    table.add_row(entry("Disk free (GB)", resources.get("disk_free_gb")))

    panel = Panel(table, title="Environment summary", border_style="cyan", expand=True)
    return panel


def render_run_script(plan: Plan, commands: Iterable[PlannedCommand]) -> str:
    """Generate a bash script for the execution plan."""

    lines = ["#!/usr/bin/env bash", "set -euo pipefail", "", "# Generated from cactus-prepare plan"]
    for command in commands:
        lines.append(f"# {command.display_name}")
        lines.append(shlex.join(command.command))
        lines.append("")
    script = "\n".join(lines).rstrip() + "\n"
    return script




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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        elif event.button.id == "cancel":
            self.action_cancel()


class CommandEditModal(ModalScreen[str | None]):
    """Modal dialog allowing the user to edit a command string."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
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
        self._editor: TextArea | None = None
        self._status: Static | None = None

    def compose(self) -> ComposeResult:
        with Container(id="editor-dialog"):
            yield Static(self.title, id="editor-title")
            editor = TextArea(id="editor-command")
            editor.text = self.initial_command
            self._editor = editor
            yield editor
            status = Static("", id="editor-status")
            self._status = status
            yield status
            with Container(id="editor-buttons"):
                yield Button("Save", id="save", variant="success")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        if self._editor:
            self._editor.focus()

    def action_save(self) -> None:
        if not self._editor:
            self.dismiss(None)
            return
        value = self._editor.text.strip()
        if not value:
            if self._status:
                self._status.update("Command cannot be empty")
            return
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class InfoModal(ModalScreen[None]):
    """Read-only modal for displaying multi-line text."""

    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("enter", "dismiss", show=False)]

    CSS = """
    InfoModal {
        align: center middle;
    }
    #info-dialog {
        padding: 1 2;
        width: 80%;
        max-width: 90;
        height: 80%;
        border: round $accent;
        background: $panel;
        layout: vertical;
    }
    #info-title {
        padding-bottom: 1;
    }
    #info-body {
        height: 1fr;
        overflow-y: auto;
    }
    #info-hint {
        padding-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, title: str, body: str):
        super().__init__()
        self.title = title
        self.body = body or "(empty)"

    def compose(self) -> ComposeResult:
        with Container(id="info-dialog"):
            yield Static(self.title, id="info-title")
            yield Static(self.body, id="info-body")
            yield Static("Enter / Esc to close", id="info-hint")

    def action_dismiss(self) -> None:
        self.dismiss(None)


class SearchModal(ModalScreen[str | None]):
    """Single-line search input modal."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = """
    SearchModal {
        align: center middle;
    }
    #search-dialog {
        padding: 1 2;
        min-width: 40;
        border: round $accent;
        background: $panel;
    }
    #search-title {
        padding-bottom: 1;
    }
    #search-hint {
        padding-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, initial: str = ""):
        super().__init__()
        self.initial = initial
        self._input: Input | None = None

    def compose(self) -> ComposeResult:
        with Container(id="search-dialog"):
            yield Static("Enter a node keyword", id="search-title")
            self._input = Input(value=self.initial, placeholder="e.g. human / panTro")
            yield self._input
            yield Static("Enter to confirm, Esc to cancel", id="search-hint")

    def on_mount(self) -> None:
        if self._input:
            self.set_focus(self._input)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


@dataclass
class _DetailCallback:
    """Wrapper to forward detail updates from AsciiPhylo to the host app."""

    handler: Optional[Callable[[tree_utils.AlignmentNode, Optional[str]], None]] = None

    def __call__(self, node: tree_utils.AlignmentNode, status: Optional[str] = None) -> None:
        if self.handler:
            self.handler(node, status=status)


class AsciiPhylo(Static):
    """Full-screen ASCII phylogenetic tree widget."""

    DEFAULT_CSS = """
    AsciiPhylo {
        width: 1fr;
        height: 1fr;
        border: round $panel-darken-2;
        padding: 0 1;
        overflow: hidden;
        background: $panel;
    }
    """

    can_focus = True

    BINDINGS = [
        Binding("up", "move_up", show=False),
        Binding("down", "move_down", show=False),
        Binding("left", "move_parent", show=False),
        Binding("right", "move_child", show=False),
        Binding("h", "move_parent", show=False),
        Binding("j", "move_down", show=False),
        Binding("k", "move_up", show=False),
        Binding("l", "move_child", show=False),
        Binding("space", "toggle_subtree", "Toggle RaMAx"),
        Binding("f", "focus_here", "Focus"),
        Binding("b", "focus_back", "Back"),
        Binding("g", "toggle_mode", "Clado/Phylo"),
        Binding("+", "zoom_in", "Zoom in"),
        Binding("-", "zoom_out", "Zoom out"),
        Binding("/", "open_search", "Search"),
        Binding("n", "search_next", show=False),
        Binding("shift+n", "search_prev", show=False),
        Binding("a", "toggle_ascii", "ASCII mode"),
    ]

    def __init__(self, root: tree_utils.AlignmentNode, *, id: str = "ascii-phylo"):
        super().__init__("", id=id)
        self._root = root
        self._cursor = root
        self._stack: list[tree_utils.AlignmentNode] = []
        self._mode = "clado"
        self._ascii_only = False
        self._scale_x = 1.0
        self._y_gap = 2
        self._view_x = 0
        self._view_y = 0
        self._ordered_children: dict[tree_utils.AlignmentNode, list[tree_utils.AlignmentNode]] = {}
        self._y_map: dict[tree_utils.AlignmentNode, float] = {}
        self._x_map: dict[tree_utils.AlignmentNode, int] = {}
        self._linear: list[tree_utils.AlignmentNode] = []
        self._state_cache: dict[tree_utils.AlignmentNode, str] = {}
        self._search_term: Optional[str] = None
        self._hits: list[tree_utils.AlignmentNode] = []
        self._hit_index = 0
        self._detail_callback = _DetailCallback()
        self._content_width = 0
        self._content_height = 0
        self._visual = Text()

    def set_detail_callback(
        self,
        callback: Optional[Callable[[tree_utils.AlignmentNode, Optional[str]], None]],
    ) -> None:
        self._detail_callback.handler = callback

    def current_node(self) -> tree_utils.AlignmentNode:
        return self._cursor

    def on_mount(self) -> None:
        self.focus()
        self._layout()

    def on_resize(self, event: events.Resize) -> None:  # type: ignore[override]
        self._rebuild_visual()
        self.refresh()

    def action_move_up(self) -> None:
        self._move_cursor(-1)

    def action_move_down(self) -> None:
        self._move_cursor(+1)

    def action_move_parent(self) -> None:
        parent = getattr(self._cursor, "parent", None)
        if parent:
            self._set_cursor(parent, ensure_visible=True)

    def action_move_child(self) -> None:
        children = self._ordered_children.get(self._cursor, self._cursor.children)
        if not children:
            return
        for child in children:
            if not self._is_species_leaf(child):
                self._set_cursor(child, ensure_visible=True)
                return
        self._notify(self._cursor, "Only species leaves under this node.")

    def action_toggle_subtree(self) -> None:
        rounds = list(self._cursor.iter_rounds())
        if not rounds:
            self._notify(self._cursor, "No rounds in this subtree can be toggled.")
            return
        target_state = not all(r.replace_with_ramax for r in rounds)
        for round_entry in rounds:
            round_entry.replace_with_ramax = target_state
        message = (
            f"Toggled {len(rounds)} round(s) to RaMAx"
            if target_state
            else f"Restored {len(rounds)} round(s) to cactus"
        )
        self._rebuild_visual()
        self.refresh()
        self._notify(self._cursor, message)

    def action_focus_here(self) -> None:
        if self._cursor is self._root:
            return
        self._stack.append(self._root)
        self._root = self._cursor
        self._layout()
        self._notify(self._cursor, "Focused on this subtree.")

    def action_focus_back(self) -> None:
        if not self._stack:
            return
        self._root = self._stack.pop()
        self._layout()
        self._notify(self._cursor, "Returned to previous view.")

    def action_toggle_mode(self) -> None:
        self._mode = "phylo" if self._mode == "clado" else "clado"
        self._layout()
        self._notify(self._cursor, f"Switched to {self._mode} mode.")

    def action_zoom_in(self) -> None:
        self._scale_x = min(8.0, self._scale_x * 1.2)
        self._layout()

    def action_zoom_out(self) -> None:
        self._scale_x = max(0.3, self._scale_x / 1.2)
        self._layout()

    def action_toggle_ascii(self) -> None:
        self._ascii_only = not self._ascii_only
        self._rebuild_visual()
        self.refresh()
        self._notify(self._cursor, "ASCII mode enabled" if self._ascii_only else "Box drawing restored.")

    def action_open_search(self) -> None:
        prompt = SearchModal(self._search_term or "")
        self.app.push_screen(prompt, self._apply_search_term)

    def action_search_next(self) -> None:
        self._jump_hit(+1)

    def action_search_prev(self) -> None:
        self._jump_hit(-1)

    def _apply_search_term(self, term: str | None) -> None:
        if term is None:
            return
        cleaned = term.strip().lower()
        if not cleaned:
            self._search_term = None
            self._hits.clear()
            self._rebuild_visual()
            self.refresh()
            return
        self._search_term = cleaned
        self._hits = [
            node for node in self._linear if cleaned in (node.name or "").lower()
        ]
        self._hit_index = 0
        if self._hits:
            self._set_cursor(self._hits[0], ensure_visible=True)
            self._notify(self._cursor, f"Found {len(self._hits)} match(es).")
        else:
            self._notify(self._cursor, "No matching nodes found.")

    def _jump_hit(self, delta: int) -> None:
        if not self._hits:
            return
        self._hit_index = (self._hit_index + delta) % len(self._hits)
        self._set_cursor(self._hits[self._hit_index], ensure_visible=True)

    def _is_species_leaf(self, node: tree_utils.AlignmentNode) -> bool:
        return not node.children and node.round is None

    def _collect_subtree_nodes(self, node: tree_utils.AlignmentNode) -> set[tree_utils.AlignmentNode]:
        nodes: set[tree_utils.AlignmentNode] = set()
        stack = [node]
        while stack:
            current = stack.pop()
            if current in nodes:
                continue
            nodes.add(current)
            for child in self._ordered_children.get(current, current.children):
                stack.append(child)
        return nodes

    def _collect_round_nodes(self, node: tree_utils.AlignmentNode) -> set[tree_utils.AlignmentNode]:
        if node is None:
            return set()
        return {candidate for candidate in self._collect_subtree_nodes(node) if candidate.round}

    def _move_cursor(self, delta: int) -> None:
        if not self._linear:
            return
        try:
            index = self._linear.index(self._cursor)
        except ValueError:
            index = 0
        direction = 1 if delta >= 0 else -1
        next_index = max(0, min(len(self._linear) - 1, index + delta))
        while 0 <= next_index < len(self._linear):
            candidate = self._linear[next_index]
            if not self._is_species_leaf(candidate):
                self._set_cursor(candidate, ensure_visible=True)
                return
            next_index += direction

    def _set_cursor(self, node: tree_utils.AlignmentNode, ensure_visible: bool = False) -> None:
        self._cursor = node
        if ensure_visible:
            self._ensure_visible(node)
        self._rebuild_visual()
        self.refresh()
        self._notify(node)

    def _notify(self, node: tree_utils.AlignmentNode, status: Optional[str] = None) -> None:
        self._detail_callback(node, status=status)

    def _layout(self) -> None:
        if not self._root:
            self.update("No tree structure found")
            return
        size_map: dict[tree_utils.AlignmentNode, int] = {}

        def compute_size(node: tree_utils.AlignmentNode) -> int:
            total = 1
            for child in node.children:
                total += compute_size(child)
            size_map[node] = total
            return total

        compute_size(self._root)

        self._ordered_children.clear()

        def order_children(node: tree_utils.AlignmentNode) -> None:
            ordered = sorted(
                node.children,
                key=lambda c: size_map.get(c, 1),
                reverse=True,
            )
            self._ordered_children[node] = ordered
            for child in ordered:
                order_children(child)

        order_children(self._root)

        self._y_map.clear()
        self._x_map.clear()
        self._linear.clear()

        leaf_index = 0

        def assign_y(node: tree_utils.AlignmentNode) -> float:
            nonlocal leaf_index
            children = self._ordered_children.get(node, [])
            if not children:
                self._y_map[node] = float(leaf_index * self._y_gap)
                leaf_index += 1
                return self._y_map[node]
            child_ys = [assign_y(child) for child in children]
            top = min(child_ys)
            bottom = max(child_ys)
            self._y_map[node] = (top + bottom) / 2
            return self._y_map[node]

        assign_y(self._root)

        step = max(3, int(6 * self._scale_x))
        self._x_map[self._root] = 0

        def assign_x(node: tree_utils.AlignmentNode) -> None:
            base = self._x_map[node]
            children = self._ordered_children.get(node, [])
            for child in children:
                if self._mode == "phylo":
                    increment = child.length if child.length is not None else 1.0
                    increment = max(0.1, increment)
                else:
                    increment = 2.0
                delta = max(2, int(increment * step))
                self._x_map[child] = base + delta
                assign_x(child)

        assign_x(self._root)

        self._linear = sorted(
            self._y_map.keys(),
            key=lambda node: (self._y_map[node], self._x_map.get(node, 0)),
        )
        if self._cursor not in self._linear:
            self._cursor = self._root
        self._content_width = max(self._x_map.values(), default=0) + 40
        max_y = math.ceil(max(self._y_map.values(), default=0))
        self._content_height = max_y + 10
        self._ensure_visible(self._cursor)
        self._rebuild_visual()
        self.refresh()

    def _ensure_visible(self, node: tree_utils.AlignmentNode) -> None:
        width = max(40, self.size.width - 2)
        height = max(10, self.size.height - 2)
        x = self._x_map.get(node, 0)
        y = int(round(self._y_map.get(node, 0)))
        margin = 2
        if x < self._view_x + margin:
            self._view_x = max(0, x - margin)
        elif x >= self._view_x + width - margin:
            self._view_x = x - (width - margin - 1)
        if y < self._view_y + margin:
            self._view_y = max(0, y - margin)
        elif y >= self._view_y + height - margin:
            self._view_y = y - (height - margin - 1)
        max_x = max(0, self._content_width - width)
        max_y = max(0, self._content_height - height)
        self._view_x = max(0, min(self._view_x, max_x))
        self._view_y = max(0, min(self._view_y, max_y))

    def _glyphs(self) -> dict[str, str]:
        if self._ascii_only:
            return {
                "h": "-",
                "v": "|",
                "tee": "+",
                "elbow": "+",
                "top": "+",
                "dot": "*",
                "lite": "o",
                "parent": "O",
            }
        return {
            "h": "─",
            "v": "│",
            "tee": "├",
            "elbow": "└",
            "top": "┌",
            "dot": "●",
            "lite": "◇",
            "parent": "◎",
        }

    def _compute_states(self) -> None:
        self._state_cache.clear()

        def helper(current: tree_utils.AlignmentNode) -> str:
            children = self._ordered_children.get(current, current.children)
            child_states = [helper(child) for child in children]
            child_has_round = any(state != "leaf" for state in child_states)
            descendant_enabled = any(state in ("checked", "mixed") for state in child_states)

            if current.round:
                if current.round.replace_with_ramax:
                    result = "checked"
                elif descendant_enabled:
                    result = "mixed"
                else:
                    result = "unchecked"
            else:
                if not child_has_round:
                    result = "leaf"
                elif descendant_enabled:
                    result = "mixed"
                else:
                    result = "unchecked"
            self._state_cache[current] = result
            return result

        helper(self._root)

    def _node_state(self, node: tree_utils.AlignmentNode) -> str:
        return self._state_cache.get(node, "leaf")

    def _state_color(self, node: tree_utils.AlignmentNode) -> str:
        state = self._node_state(node)
        if state == "checked":
            return "#2ecc71"
        if state == "mixed":
            return "#f39c12"
        if state == "unchecked":
            return "#94a3b8"
        return "#aeb8cc"

    def _connector_highlight(self, parent_kind: str | None, child_kind: str | None) -> str | None:
        if parent_kind == "ramax" and child_kind == "ramax":
            return "ramax"
        if parent_kind == "selected" or child_kind == "selected":
            return "selected"
        return None

    def _rebuild_visual(self) -> None:
        if not self._y_map:
            self._visual = Text("")
            return
        self._compute_states()
        width = max(40, self.size.width - 2)
        height = max(10, self.size.height - 2)
        glyphs = self._glyphs()
        grid = [[" " for _ in range(width)] for _ in range(height)]
        styles = [["" for _ in range(width)] for _ in range(height)]
        label_margin = 2

        selected_highlight: set[tree_utils.AlignmentNode] = set()
        if self._cursor:
            selected_highlight = self._collect_round_nodes(self._cursor)
        ramax_highlight: set[tree_utils.AlignmentNode] = {
            node
            for node in self._y_map.keys()
            if node.round and node.round.replace_with_ramax
        }

        def highlight_kind(node: tree_utils.AlignmentNode) -> str | None:
            if node in selected_highlight:
                return "selected"
            if node in ramax_highlight:
                return "ramax"
            return None

        def highlight_color(kind: str | None, default: str) -> str:
            if kind == "ramax":
                return "#fcbf49"
            if kind == "selected":
                return "#4cc9f0"
            return default

        def draw_char(x: int, y: int, ch: str, style: str = "") -> None:
            vx = x - self._view_x
            vy = y - self._view_y
            if 0 <= vx < width and 0 <= vy < height:
                grid[vy][vx] = ch
                styles[vy][vx] = style

        def draw_branch(parent: tree_utils.AlignmentNode) -> None:
            px = self._x_map[parent]
            py = int(round(self._y_map[parent]))
            children = self._ordered_children.get(parent, [])
            if children:
                child_ys = [int(round(self._y_map[child])) for child in children]
                y0, y1 = min(child_ys), max(child_ys)
                parent_kind = highlight_kind(parent)
                vertical_style = highlight_color(parent_kind, "#3c445c")
                for y in range(y0, y1 + 1):
                    draw_char(px, y, glyphs["v"], vertical_style)
                last_index = len(children) - 1
                for index, child in enumerate(children):
                    cx = self._x_map[child]
                    cy = int(round(self._y_map[child]))
                    if len(children) == 1:
                        joint = glyphs["elbow"]
                    elif index == 0:
                        joint = glyphs.get("top", glyphs["tee"])
                    elif index == last_index:
                        joint = glyphs["elbow"]
                    else:
                        joint = glyphs["tee"]
                    child_kind = highlight_kind(child)
                    joint_kind = self._connector_highlight(parent_kind, child_kind)
                    connector_style = highlight_color(joint_kind, vertical_style)
                    draw_char(px, cy, joint, connector_style)
                    for x in range(min(px + 1, cx), cx + 1):
                        draw_char(x, cy, glyphs["h"], connector_style)
                    if self._mode == "clado" and child.length is not None:
                        text = f"{child.length:.3f}"
                        available = max(0, cx - px - 1)
                        if available >= len(text):
                            start = px + 1 + max(0, (available - len(text)) // 2)
                            start = min(start, cx - len(text))
                            for offset, ch in enumerate(text):
                                draw_char(start + offset, cy, ch, "#6b768f")
                    draw_branch(child)
            style = "#ffffff" if parent is self._cursor else self._state_color(parent)
            style = highlight_color(highlight_kind(parent), style)
            support = parent.support if parent.support is not None else 100.0
            if parent.children:
                node_char = glyphs.get("parent", "◎")
            else:
                node_char = glyphs["dot"] if support >= 70 else glyphs["lite"]
            draw_char(px, py, node_char, style)
            label = parent.name or "(unnamed)"
            label = f"> {label}" if parent is self._cursor else f"  {label}"
            x0 = px + label_margin
            for offset, ch in enumerate(label):
                draw_char(x0 + offset, py, ch, style)

        draw_branch(self._root)
        lines: list[Text] = []
        for row_chars, row_styles in zip(grid, styles):
            line = Text()
            for ch, style in zip(row_chars, row_styles):
                if style:
                    line.append(ch, style=style)
                else:
                    line.append(ch)
            line.rstrip()
            lines.append(line)
        rendered = Text("\n").join(lines)
        self._visual = rendered

    def render(self) -> Text:  # type: ignore[override]
        return self._visual


class RoundPickerModal(ModalScreen[int | None]):
    """Modal dialog for picking a round when no node is focused."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = """
    RoundPickerModal {
        align: center middle;
    }
    #round-picker {
        padding: 1 2;
        width: 70%;
        max-width: 80;
        border: round $accent;
        background: $panel;
    }
    #round-picker-list {
        height: auto;
        max-height: 24;
    }
    #round-picker-hint {
        padding-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, rounds: list[Round]):
        super().__init__()
        self.rounds = rounds
        self._list: ListView | None = None

    def compose(self) -> ComposeResult:
        with Container(id="round-picker"):
            items = []
            for round_entry in self.rounds:
                label = Text(round_entry.name, style="bold")
                label.append(f" ({round_entry.root})")
                label.append("\n")
                label.append(round_entry.target_hal)
                items.append(ListItem(Static(label, expand=True)))
            self._list = ListView(*items, id="round-picker-list")
            yield self._list
            yield Static("Enter to confirm, Esc to cancel", id="round-picker-hint")

    def on_mount(self) -> None:
        if self._list:
            self._list.index = 0
            self.set_focus(self._list)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(event.index)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DetailBuffer:
    """Stores the latest detail text and mirrors a short summary to the subtitle."""

    def __init__(self, app: "PlanUIApp"):
        self.app = app
        self.text: str = ""
        self.renderable: RenderableType | str = ""

    def update(self, message: RenderableType | str) -> None:
        self.renderable = message
        if isinstance(message, str):
            plain = message
        else:
            # Render rich content into plain text for later inspection.
            temp_console = Console(width=120, record=True, color_system=None)
            with temp_console.capture() as capture:
                temp_console.print(message)
            plain = capture.get()
        self.text = plain
        summary = plain.splitlines()[0] if plain else ""
        if summary:
            try:
                summary_plain = Text.from_markup(summary).plain
            except Exception:
                summary_plain = summary
        else:
            summary_plain = ""
        self.app.sub_title = summary_plain[:80]

class RunSettingsScreen(Screen[RunSettings | None]):
    """Dedicated screen for confirming run-time configuration."""

    BINDINGS = [
        Binding("escape", "cancel", "Back"),
        Binding("ctrl+enter", "save", "Run"),
        Binding("ctrl+r", "save", "Run"),
        Binding("v", "toggle_verbose", "Toggle verbose"),
    ]

    CSS = """
    RunSettingsScreen > .screen {
        layout: vertical;
    }
    #run-root {
        padding: 1 2;
        height: 1fr;
        width: 100%;
        layout: vertical;
        min-height: 0;
    }
    #run-title {
        padding-bottom: 1;
    }
    #run-body {
        layout: horizontal;
        min-height: 0;
    }
    #run-summary {
        width: 60%;
        min-width: 40;
        margin-right: 2;
        height: 1fr;
        overflow-y: auto;
    }
    #run-form {
        width: 40%;
        min-width: 32;
        height: 1fr;
        border: round $accent;
        padding: 1;
        background: $panel;
        layout: vertical;
    }
    #run-instructions {
        color: $text-muted;
        padding-bottom: 1;
    }
    #run-verbose {
        margin-bottom: 1;
    }
    #run-threads {
        width: 100%;
    }
    #run-hint {
        padding-top: 1;
        color: $text-muted;
    }
    #run-status {
        padding-top: 1;
        color: $error;
    }
    #run-buttons {
        padding-top: 1;
        layout: horizontal;
        content-align: right middle;
    }
    #run-buttons Button {
        margin-left: 1;
    }
    #run-buttons Button:first-child {
        margin-left: 0;
    }
    """

    def __init__(self, plan: Plan, current: RunSettings, compact: bool):
        super().__init__()
        self.plan = plan
        self.current = current
        self.compact = compact
        self._summary: Static | None = None
        self._input: Input | None = None
        self._verbose: Checkbox | None = None
        self._status: Static | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="run-root"):
            yield Static("Plan is ready. Review run settings before execution:", id="run-title")
            with Container(id="run-body"):
                summary = Static(id="run-summary")
                self._summary = summary
                summary.update(plan_overview(self.plan, run_settings=self.current, compact=self.compact))
                yield summary
                with Container(id="run-form"):
                    yield Static("• Tab/Shift+Tab to move between controls\n• Ctrl+Enter to run immediately\n• V toggles verbose logging", id="run-instructions")
                    verbose_box = Checkbox(
                        "Verbose logging (stream every command output)",
                        value=self.current.verbose,
                        id="run-verbose",
                    )
                    self._verbose = verbose_box
                    yield verbose_box
                    threads_input = Input(
                        value="" if self.current.thread_count is None else str(self.current.thread_count),
                        placeholder="Leave blank to keep each command's thread defaults",
                        id="run-threads",
                    )
                    self._input = threads_input
                    yield threads_input
                    yield Static("Threads propagate to cactus --maxCores and RaMAx --threads.", id="run-hint")
                    status = Static("", id="run-status")
                    self._status = status
                    yield status
                    with Container(id="run-buttons"):
                        yield Button("Save command list", id="run-save")
                        yield Button("Run plan (Ctrl+Enter)", id="run-confirm", variant="success")
                        yield Button("Back to plan (Esc)", id="run-cancel")
        yield Footer()

    def on_mount(self) -> None:
        if self._input:
            self.set_focus(self._input)

    def _validate_threads(self) -> tuple[bool, Optional[int], Optional[str]]:
        if not self._input:
            return True, None, None
        text = self._input.value.strip()
        if not text:
            return True, None, None
        try:
            value = int(text)
        except ValueError:
            return False, None, "Thread count must be a positive integer."
        if value <= 0:
            return False, None, "Thread count must be at least 1."
        return True, value, None

    def _update_status(self, message: str | None) -> None:
        if self._status is not None:
            self._status.update(message or "")

    def action_save(self) -> None:
        ok, threads, error = self._validate_threads()
        if not ok:
            self._update_status(error)
            return
        self._update_status("")
        verbose = self._verbose.value if self._verbose else False
        self.dismiss(RunSettings(verbose=verbose, thread_count=threads))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_toggle_verbose(self) -> None:
        if self._verbose:
            self._verbose.value = not self._verbose.value
            self._refresh_summary()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "run-threads":
            self.action_save()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "run-threads":
            self._refresh_summary()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "run-verbose":
            self._refresh_summary()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-save":
            self._handle_save_commands()
        elif event.button.id == "run-confirm":
            self.action_save()
        elif event.button.id == "run-cancel":
            self.action_cancel()

    def _handle_save_commands(self) -> None:
        app = self.app
        if not isinstance(app, PlanUIApp):
            self._update_status("Cannot save commands: unknown host app")
            return
        settings = self._current_settings_preview()
        path = app.export_commands(settings, notify_detail=False)
        if path:
            path_str = str(path)
            self._update_status(f"Commands saved to {path_str}")
        else:
            self._update_status("Failed to save commands")

    def _current_settings_preview(self) -> RunSettings:
        verbose = self._verbose.value if self._verbose else self.current.verbose
        ok, threads, _ = self._validate_threads()
        thread_val = threads if ok else self.current.thread_count
        return RunSettings(verbose=verbose, thread_count=thread_val)

    def _refresh_summary(self) -> None:
        if not self._summary:
            return
        settings = self._current_settings_preview()
        self._summary.update(
            plan_overview(self.plan, run_settings=settings, compact=self.compact)
        )


class RamaxOptionsModal(ModalScreen[tuple[list[str], list[str]] | None]):
    """Modal dialog for editing global and per-round RaMAx options."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
        Binding("enter", "save", "Save"),
    ]

    CSS = """
    RamaxOptionsModal {
        align: center middle;
    }
    #options-dialog {
        padding: 1 2;
        width: 80%;
        max-width: 90;
        border: round $accent;
        background: $panel;
    }
    #options-title {
        padding-bottom: 1;
    }
    .section-label {
        padding-top: 1;
        padding-bottom: 0;
    }
    .options-list {
        layout: vertical;
        gap: 1;
        max-height: 12;
        overflow-y: auto;
        padding-top: 1;
    }
    .option-input {
        width: 100%;
    }
    .option-empty {
        color: $text-muted;
    }
    .button-row {
        layout: horizontal;
        gap: 1;
        padding-top: 1;
    }
    #options-buttons {
        layout: horizontal;
        gap: 1;
        padding-top: 1;
        justify: end;
    }
    #options-status {
        padding-top: 1;
        color: $error;
    }
    """

    def __init__(self, global_opts: list[str], round_opts: list[str]):
        super().__init__()
        self._global_values = list(global_opts)
        self._round_values = list(round_opts)
        self._global_container: Container | None = None
        self._round_container: Container | None = None
        self._status: Static | None = None

    def compose(self) -> ComposeResult:
        with Container(id="options-dialog"):
            yield Static("Edit RaMAx options", id="options-title")
            yield Static("Global options (plan.global_ramax_opts)", classes="section-label")
            global_container = Container(id="global-options", classes="options-list")
            self._global_container = global_container
            yield global_container
            with Container(id="global-buttons", classes="button-row"):
                yield Button("Add global option", id="add-global", variant="success")
                yield Button("Remove last global", id="remove-global", variant="warning")
            yield Static("Current Round options (round.ramax_opts)", classes="section-label")
            round_container = Container(id="round-options", classes="options-list")
            self._round_container = round_container
            yield round_container
            with Container(id="round-buttons", classes="button-row"):
                yield Button("Add Round option", id="add-round", variant="success")
                yield Button("Remove last Round", id="remove-round", variant="warning")
            status = Static("", id="options-status")
            self._status = status
            yield status
            with Container(id="options-buttons"):
                yield Button("Save", id="save-options", variant="success")
                yield Button("Cancel", id="cancel-options")

    def on_mount(self) -> None:
        self._refresh_inputs()

    def _refresh_inputs(self) -> None:
        if self._global_container:
            for child in list(self._global_container.children):
                child.remove()
            if self._global_values:
                inputs = [
                    Input(value=value, placeholder="e.g. --threads=8", classes="option-input", id=f"global-{idx}")
                    for idx, value in enumerate(self._global_values)
                ]
                self._global_container.mount(*inputs)
            else:
                self._global_container.mount(Static("(no global options)", classes="option-empty"))
        if self._round_container:
            for child in list(self._round_container.children):
                child.remove()
            if self._round_values:
                inputs = [
                    Input(value=value, placeholder="e.g. --input {}", classes="option-input", id=f"round-{idx}")
                    for idx, value in enumerate(self._round_values)
                ]
                self._round_container.mount(*inputs)
            else:
                self._round_container.mount(Static("(no Round options)", classes="option-empty"))

    def _sync_from_inputs(self) -> None:
        if self._global_container:
            self._global_values = self._collect_values(self._global_container, strip_empty=False)
        if self._round_container:
            self._round_values = self._collect_values(self._round_container, strip_empty=False)

    def _collect_values(self, container: Container, strip_empty: bool) -> list[str]:
        values: list[str] = []
        for widget in container.query(Input):
            value = widget.value if not strip_empty else widget.value.strip()
            if strip_empty:
                if value:
                    values.append(value)
            else:
                values.append(value)
        return values

    def action_save(self) -> None:
        global_values = self._collect_values(self._global_container, strip_empty=True) if self._global_container else []
        round_values = self._collect_values(self._round_container, strip_empty=True) if self._round_container else []
        self.dismiss((global_values, round_values))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "save-options":
            self.action_save()
            return
        if button_id == "cancel-options":
            self.action_cancel()
            return
        self._sync_from_inputs()
        if button_id == "add-global":
            self._global_values.append("")
        elif button_id == "remove-global":
            if self._global_values:
                self._global_values.pop()
        elif button_id == "add-round":
            self._round_values.append("")
        elif button_id == "remove-round":
            if self._round_values:
                self._round_values.pop()
        else:
            return
        self._refresh_inputs()

class PlanUIApp(App[UIResult]):
    CSS = """
    Screen {
        layout: vertical;
        min-height: 0;
    }
    #main {
        width: 100%;
        height: 1fr;
        min-height: 0;
        padding: 0 1;
    }
    #ascii-phylo, #ascii-phylo-empty {
        width: 100%;
        height: 1fr;
    }
    #ascii-phylo-empty {
        align: center middle;
        color: $text-muted;
    }
    #editor-command { height: 10; }
    """

    BINDINGS = [
        Binding("e", "edit_round", "Edit command"),
        Binding("r", "run_plan", "Run"),
        Binding("q", "quit", "Quit"),
        Binding("i", "show_info", "Info"),
    ]

    def __init__(self, plan: Plan, base_dir: Optional[Path] = None, run_settings: Optional[RunSettings] = None):
        super().__init__()
        self.plan = plan
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.alignment_tree = tree_utils.build_alignment_tree(plan, base_dir=self.base_dir)
        self.canvas: AsciiPhylo | None = None
        self.detail_panel: DetailBuffer | None = None
        self.run_settings = run_settings or RunSettings()

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="main"):
            if self.alignment_tree:
                canvas = AsciiPhylo(self.alignment_tree.root)
                canvas.set_detail_callback(self._show_alignment_node)
                self.canvas = canvas
                yield canvas
            else:
                yield Static("Alignment tree not found; nothing to render.", id="ascii-phylo-empty")
        yield Footer()

    def on_mount(self) -> None:
        self.detail_panel = DetailBuffer(self)
        if self.canvas:
            self.canvas.focus()
            self._show_alignment_node(self.canvas.current_node())
        else:
            preview = plan_overview(self.plan, run_settings=self.run_settings, compact=self._is_compact())
            self.detail_panel.update(preview)

    def _is_compact(self) -> bool:
        return self.size.width <= 100

    def action_show_info(self) -> None:
        if not self.detail_panel:
            return
        content = self.detail_panel.text or "(empty)"
        self.push_screen(InfoModal("Current node details", content))

    def action_edit_round(self) -> None:
        if not self.plan.rounds:
            if self.detail_panel:
                self.detail_panel.update("No rounds found in this plan.")
            return
        node_round = None
        if self.canvas:
            node = self.canvas.current_node()
            node_round = node.round
        if node_round and node_round in self.plan.rounds:
            round_index = self.plan.rounds.index(node_round)
            self._start_round_edit(round_index)
            return
        picker = RoundPickerModal(self.plan.rounds)
        self.push_screen(picker, self._handle_round_pick)

    def _handle_round_pick(self, index: int | None) -> None:
        if index is None:
            return
        if index >= len(self.plan.rounds):
            return
        self._start_round_edit(index)

    def _start_round_edit(self, round_index: int) -> None:
        round_entry = self.plan.rounds[round_index]
        targets = self._gather_command_targets(round_entry)
        if not targets:
            if self.detail_panel:
                self.detail_panel.update("No editable commands for this round.")
            return
        if len(targets) == 1:
            self._open_command_editor(round_index, targets[0])
        else:
            self.push_screen(
                CommandSelectionModal(targets),
                lambda target: self._handle_command_selection(round_index, target),
            )
        self._show_round(round_index)

    def action_run_plan(self) -> None:
        screen = RunSettingsScreen(self.plan, self.run_settings, compact=self._is_compact())
        self.push_screen(screen, self._finalize_run_settings)

    def export_commands(self, settings: RunSettings | None = None, *, notify_detail: bool = True) -> Path | None:
        output_dir = Path(self.plan.out_dir or self.base_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "ramax_commands.txt"
        commands = planner.build_execution_plan(
            self.plan,
            self.base_dir,
            thread_count=(settings.thread_count if settings else self.run_settings.thread_count),
        )
        lines = [cmd.shell_preview() for cmd in commands]
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if notify_detail and self.detail_panel:
            self.detail_panel.update(f"[green]Commands saved to {output_path}[/green]")
        return output_path

    def action_quit(self) -> None:
        self.exit(UIResult(plan=self.plan, action="quit", run_settings=self.run_settings))

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
        details.append("")
        details.append("[yellow]RaMAx options[/yellow]")
        details.append(f"Global: {self._format_option_list(self.plan.global_ramax_opts)}")
        details.append(f"Round: {self._format_option_list(round_entry.ramax_opts)}")
        return details

    def _format_option_list(self, options: list[str]) -> str:
        return ", ".join(options) if options else "(empty)"

    def _ramax_options_summary(self, round_entry: Round) -> str:
        global_summary = self._format_option_list(self.plan.global_ramax_opts)
        round_summary = self._format_option_list(round_entry.ramax_opts)
        return f"Global: {global_summary}\nRound: {round_summary}"

    def _show_round(self, index: int, status: str | None = None) -> None:
        if not self.detail_panel or index >= len(self.plan.rounds):
            return
        round_entry = self.plan.rounds[index]
        details = self._round_details(round_entry)
        if status:
            details.extend(["", f"[green]{status}[/green]"])
        self.detail_panel.update("\n".join(details))

    def _gather_command_targets(self, round_entry: Round) -> list[CommandTarget]:
        targets: list[CommandTarget] = [
            CommandTarget(
                key="ramax-options",
                label="RaMAx options",
                command=self._ramax_options_summary(round_entry),
                kind="ramax-options",
            )
        ]
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

    def _handle_command_selection(self, round_index: int, target: CommandTarget | None) -> None:
        if target is None:
            return
        self._open_command_editor(round_index, target)

    def _open_command_editor(self, round_index: int, target: CommandTarget) -> None:
        if target.kind == "ramax-options":
            if round_index >= len(self.plan.rounds):
                return
            options_modal = RamaxOptionsModal(
                self.plan.global_ramax_opts,
                self.plan.rounds[round_index].ramax_opts,
            )
            self.push_screen(
                options_modal,
                lambda result: self._apply_ramax_options(round_index, result),
            )
            return
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
        self._show_round(round_index, status=status)

    def _apply_ramax_options(
        self,
        round_index: int,
        result: tuple[list[str], list[str]] | None,
    ) -> None:
        if result is None or round_index >= len(self.plan.rounds):
            return
        global_opts, round_opts = result
        self.plan.global_ramax_opts = global_opts
        round_entry = self.plan.rounds[round_index]
        round_entry.ramax_opts = round_opts
        status = "RaMAx options updated"
        self._show_round(round_index, status=status)

    def _finalize_run_settings(self, result: RunSettings | None) -> None:
        if result is None:
            return
        self.run_settings = result
        self.exit(UIResult(plan=self.plan, action="run", run_settings=self.run_settings))

    def _ramax_command_preview(self, round_entry: Round) -> str:
        if round_entry.manual_ramax_command:
            return round_entry.manual_ramax_command
        commands = planner.build_execution_plan(
            self.plan,
            self.base_dir,
            thread_count=self.run_settings.thread_count,
        )
        for command in commands:
            if command.is_ramax and command.round_name == round_entry.name:
                return command.shell_preview()
        return ""


def launch(
    plan: Plan,
    base_dir: Optional[Path] = None,
    run_settings: Optional[RunSettings] = None,
) -> UIResult:
    """Run the Textual UI and return the resulting plan/action."""

    app = PlanUIApp(plan, base_dir=base_dir, run_settings=run_settings)
    result = app.run()
    if isinstance(result, UIResult):
        return result
    return UIResult(plan=plan, action="quit", run_settings=run_settings)
