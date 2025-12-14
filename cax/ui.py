"""Textual-based interactive UI for configuring CAX plans."""
from __future__ import annotations

import itertools
import math
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import psutil
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Checkbox, Footer, Header, Input, ListItem, ListView, Static, TextArea

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree
from rich.align import Align
from rich.console import Group

from . import planner, resume as resume_utils, tree_utils
from .models import Plan, Round, RunSettings, Step
from .planner import PlannedCommand


SUBTREE_MODE_FLAG = "--subtree-mode"


def _is_subtree_mode_round(round_entry: Round) -> bool:
    return round_entry.replace_with_ramax and SUBTREE_MODE_FLAG in round_entry.ramax_opts


def _is_effective_ramax_node(node: tree_utils.AlignmentNode) -> bool:
    """判断节点在执行层面是否等价于 RaMAx。

    说明：
    - `replace_with_ramax=True` 的 round 当然是 RaMAx。
    - 若任一祖先 round 处于 Subtree Mode（`--subtree-mode`），其子树内的 round 虽然会被标记成 Cactus
      以避免混合状态，但执行时会被祖先 RaMAx 吸收，因此 UI 需要按“有效模式”展示为 RaMAx。
    """

    if not node.round:
        return False
    if node.round.replace_with_ramax:
        return True
    current = getattr(node, "parent", None)
    while current:
        if current.round and _is_subtree_mode_round(current.round):
            return True
        current = getattr(current, "parent", None)
    return False


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
        padding: 0 1;
        overflow: hidden;
        background: #0d1117;
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
        Binding("space", "toggle_apply", "Toggle"),
        Binding("b", "toggle_scope", "Scope node/subtree"),
        Binding("/", "open_search", "Search"),
        Binding("n", "search_next", show=False),
        Binding("shift+n", "search_prev", show=False),
    ]

    def __init__(self, root: tree_utils.AlignmentNode, *, id: str = "ascii-phylo"):
        super().__init__("", id=id)
        self._root = root
        self._cursor = root
        self._stack: list[tree_utils.AlignmentNode] = []
        self._mode = "clado"
        self._ascii_only = False
        self._scale_x = 1.0  # Controls horizontal/vertical scaling.
        self._x_gap = 6  # Leaf spacing on the horizontal grid.
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
        self._toggle_scope: str = "subtree"  # node | subtree
        self._bulk_root: tree_utils.AlignmentNode | None = None
        self._bulk_state: bool | None = None

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

    def action_toggle_apply(self) -> None:
        """Toggle apply behavior according to the current scope (single node or subtree)."""
        if self._toggle_scope == "subtree":
            self._toggle_subtree()
        else:
            self._toggle_single()

    def action_toggle_scope(self) -> None:
        """Switch scope mode and highlight the current subtree as a cue."""
        self._toggle_scope = "subtree" if self._toggle_scope == "node" else "node"
        scope_label = "Subtree" if self._toggle_scope == "subtree" else "Single node"
        self._rebuild_visual()
        self.refresh()
        self._notify(self._cursor, f"Scope switched to: {scope_label}")

    def _toggle_single(self) -> None:
        if not self._cursor.round:
            self._notify(self._cursor, "No round on this node; nothing to toggle.")
            return
        # If a parent subtree was toggled in bulk, revert it first to avoid mixed states.
        if self._maybe_revert_bulk(self._cursor):
            return
            
        round_entry = self._cursor.round
        
        # Remove subtree flag if present (switching from Subtree -> Node mode effectively)
        if "--subtree-mode" in round_entry.ramax_opts:
            round_entry.ramax_opts.remove("--subtree-mode")
            
        round_entry.replace_with_ramax = not round_entry.replace_with_ramax
        state = "RaMAx (Node)" if round_entry.replace_with_ramax else "cactus"
        self._rebuild_visual()
        self.refresh()
        self._notify(self._cursor, f"Current round switched to {state}")

    def _toggle_subtree(self) -> None:
        """
        Toggle Subtree Mode (Mode B):
        - Enable RaMAx for this node.
        - 标记内部“subtree-mode”（仅用于 CAX 控制，不会传给 ramax）。
        - Disable RaMAx for all descendant nodes (as they are subsumed).
        """
        if not self._cursor.round:
            self._notify(self._cursor, "No round on this node to apply subtree mode.")
            return
            
        round_entry = self._cursor.round
        
        # Check if currently enabled as subtree
        is_subtree_active = round_entry.replace_with_ramax and "--subtree-mode" in round_entry.ramax_opts
        
        target_state = not is_subtree_active
        
        if target_state:
            # Enable Subtree Mode
            round_entry.replace_with_ramax = True
            if "--subtree-mode" not in round_entry.ramax_opts:
                round_entry.ramax_opts.append("--subtree-mode")
            
            # Disable all descendants
            descendants = self._collect_round_nodes(self._cursor)
            count_disabled = 0
            for desc in descendants:
                if desc is self._cursor: continue
                if desc.round and desc.round.replace_with_ramax:
                    desc.round.replace_with_ramax = False
                    # Also clean their subtree flags if any
                    if "--subtree-mode" in desc.round.ramax_opts:
                        desc.round.ramax_opts.remove("--subtree-mode")
                    count_disabled += 1
            
            msg = f"Enabled Subtree RaMAx. Overridden {count_disabled} descendant(s)."
        else:
            # Disable Subtree Mode
            round_entry.replace_with_ramax = False
            if "--subtree-mode" in round_entry.ramax_opts:
                round_entry.ramax_opts.remove("--subtree-mode")
            msg = "Disabled Subtree RaMAx."

        self._rebuild_visual()
        self.refresh()
        self._notify(self._cursor, msg)

    def _maybe_revert_bulk(self, node: tree_utils.AlignmentNode) -> bool:
        """
        Check if any ancestor is in Subtree Mode. If so, disable that mode to allow node-level edits.
        Returns True if an ancestor was modified (reverted), signaling the caller to stop.
        """
        current = getattr(node, "parent", None)
        ancestor_conflict: tree_utils.AlignmentNode | None = None
        
        while current:
            if current.round and current.round.replace_with_ramax:
                if "--subtree-mode" in current.round.ramax_opts:
                    ancestor_conflict = current
                    break
            current = getattr(current, "parent", None)
            
        if not ancestor_conflict:
            return False

        # Revert the ancestor's Subtree Mode
        if ancestor_conflict.round:
            ancestor_conflict.round.ramax_opts.remove("--subtree-mode")
            # Option: also disable RaMAx entirely? 
            # "Cancel the replacement" implies setting replace_with_ramax = False?
            # Let's stick to degrading to Node Mode first, as it's safer.
            # But user said: "直接取消这个大子树的替换" -> replace_with_ramax = False
            ancestor_conflict.round.replace_with_ramax = False

        self._rebuild_visual()
        self.refresh()
        self._notify(
            node,
            f"Conflict: Subtree mode on ancestor '{ancestor_conflict.name}' has been disabled.",
        )
        
        # Show modal
        try:
            app = self.app  # May raise when not running inside a Textual app (e.g., unit tests).
        except Exception:
            app = None

        if app and hasattr(app, "push_screen"):
            app.push_screen(
                InfoModal(
                    "Subtree Mode Disabled",
                    (
                        f"The ancestor node '{ancestor_conflict.name}' was in Subtree Mode.\n\n"
                        "Since you are modifying a child node independently, the ancestor's "
                        "subtree-wide replacement has been cancelled to avoid conflicts."
                    ),
                )
            )
            
        return True

    def action_toggle_ascii(self) -> None:
        pass

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

        def assign_x(node: tree_utils.AlignmentNode) -> float:
            """Horizontal layout: leaves are evenly spaced; internal nodes take the mean of their children."""
            nonlocal leaf_index
            children = self._ordered_children.get(node, [])
            if not children:
                gap = max(3, int(self._x_gap * self._scale_x))
                self._x_map[node] = float(leaf_index * gap)
                leaf_index += 1
                return self._x_map[node]
            child_xs = [assign_x(child) for child in children]
            center = sum(child_xs) / len(child_xs)
            self._x_map[node] = center
            return center

        assign_x(self._root)

        self._y_map[self._root] = 0.0
        base_step = max(3, int(4 * self._scale_x))
        
        # Find the longest branch to normalize the visual spacing.
        max_len = 0.0
        def find_max_len(node: tree_utils.AlignmentNode) -> None:
            nonlocal max_len
            if node.length is not None:
                max_len = max(max_len, node.length)
            for child in node.children:
                find_max_len(child)
        find_max_len(self._root)
        self._max_branch_length = max_len if max_len > 0 else 1.0

        def assign_y(node: tree_utils.AlignmentNode) -> None:
            base_y = self._y_map[node]
            children = self._ordered_children.get(node, [])
            for child in children:
                if self._mode == "phylo":
                    increment = child.length if child.length is not None else 1.0
                    increment = max(0.1, increment)
                else:
                    increment = 1.0
                delta = max(2, int(math.ceil(base_step * increment)))
                self._y_map[child] = base_y + delta
                assign_y(child)

        assign_y(self._root)

        self._linear = sorted(
            self._y_map.keys(),
            key=lambda node: (self._y_map[node], self._x_map.get(node, 0)),
        )
        if self._cursor not in self._linear:
            self._cursor = self._root
        self._content_width = int(max(self._x_map.values(), default=0)) + 20
        max_y = math.ceil(max(self._y_map.values(), default=0))
        self._content_height = max_y + 6
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
            "tee": "├─",
            "elbow": "└─",
            "top": "┌─",
            "dot": "●",
            "lite": "○",
            "parent": "◈",
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
        glyphs = self._glyphs()
        highlight_subtree = self._toggle_scope == "subtree"
        highlighted_nodes: set[tree_utils.AlignmentNode] = set()
        if highlight_subtree and self._cursor:
            highlighted_nodes = self._collect_subtree_nodes(self._cursor)

        # 计算每个节点“执行层面”的有效 RaMAx 状态：当祖先处于 Subtree Mode 时，后代 round 也视为 RaMAx。
        effective_ramax: dict[tree_utils.AlignmentNode, bool] = {}

        def propagate_effective(node: tree_utils.AlignmentNode, covered: bool) -> None:
            node_effective = bool(node.round and (node.round.replace_with_ramax or covered))
            effective_ramax[node] = node_effective
            subtree_cover = covered or bool(node.round and _is_subtree_mode_round(node.round))
            for child in self._ordered_children.get(node, node.children):
                propagate_effective(child, subtree_cover)

        propagate_effective(self._root, False)

        def label_for(node: tree_utils.AlignmentNode) -> str:
            """Return the full label text without truncation."""
            name = node.name or "(unnamed)"
            parts = [name]
            if node.round:
                # Keep round state only; no extra leaf marker.
                tag = "[RaMAx]" if effective_ramax.get(node, False) else "[Cactus]"
                parts.append(tag)
            return " ".join(parts)

        # Connectors use a fixed four-column indent and consistent heavy glyphs.
        tee = "┣━━ "
        elbow = "┗━━ "
        pipe = "┃   "
        space = "    "

        # Pass 1: Build base lines and calculate max width
        raw_lines: list[tuple[Text, tree_utils.AlignmentNode]] = []
        self._x_map.clear()
        self._y_map.clear()
        max_width = 0

        def walk(node: tree_utils.AlignmentNode, prefix: str, is_last: bool, depth: int) -> None:
            connector = "" if depth == 0 else (elbow if is_last else tee)
            
            # --- Icon Selection ---
            if not node.children:
                # Leaf Node: Nature/Green theme
                icon = "● " 
            else:
                # Ancestor Node: Structure/Blue theme
                icon = "◈ "

            # --- RaMAx State Indicator (Scheme A) ---
            indicator_char = "│" # Default
            indicator_style = "#6272a4" 

            if node.round and effective_ramax.get(node, False):
                indicator_char = "❚" # Golden bar
                indicator_style = "#fcbf49"

            line = Text()
            line.append(indicator_char, style=indicator_style)
            
            # Prefix carries the vertical indentation from ancestors; render it with a uniform cool-gray style.
            line.append(prefix, style="#6272a4")
            if connector:
                line.append(connector, style="#6272a4")
            
            label_text = label_for(node)
            display_text_object = Text()

            # --- Scheme A: Cursor Highlight ---
            if node is self._cursor:
                # High-contrast background (bright purple) and bold brackets
                display_text_object.append(f"【 {icon}{label_text} 】", style="bold #1e1e2e on #bd93f9")
            
            # --- Scheme A: RaMAx State ---
            elif node.round and effective_ramax.get(node, False):
                display_text_object.append(f"{icon}{label_text}", style="bold #1e1e2e on #fcbf49")
            
            # --- Scheme A: Subtree Scope Highlight ---
            elif highlight_subtree and node in highlighted_nodes:
                if self._ascii_only:
                    display_text_object.append(f"[ {icon}{label_text} ]", style="bold #1e1e2e on #94a3b8")
                else:
                    display_text_object.append(f"〔 {icon}{label_text} 〕", style="bold #1e1e2e on #2d3b55")
            
            # --- Default: Leaf vs Ancestor Distinction ---
            elif not node.children:
                # Leaf: Green, lighter weight
                display_text_object.append(f"{icon}{label_text}", style="#a6e3a1") 
            else:
                # Ancestor: Blue, Bold
                display_text_object.append(f"{icon}{label_text}", style="bold #89b4fa")
            
            line.append(display_text_object)

            nonlocal max_width
            max_width = max(max_width, line.cell_len)
            
            y = len(raw_lines)
            x = len(prefix) + (0 if depth == 0 else len(connector))
            self._x_map[node] = x
            self._y_map[node] = y
            raw_lines.append((line, node))

            children = self._ordered_children.get(node, [])
            for idx, child in enumerate(children):
                child_is_last = idx == len(children) - 1
                child_prefix = prefix + (space if is_last else pipe)
                walk(child, child_prefix, child_is_last, depth + 1)

        walk(self._root, "", True, 0)

        # Pass 2: Add dotted leader and branch length
        final_lines: list[Text] = []
        target_width = max_width + 4 # Reserve gap

        for line, node in raw_lines:
            if node.length is not None:
                current_len = line.cell_len
                padding = max(2, target_width - current_len)
                dots = "." * padding
                len_str = f" {node.length:.4g}"
                
                # Append dotted leader and length
                line.append(dots, style="#6272a4")
                line.append(len_str, style="bold cyan")
            final_lines.append(line)

        self._linear = sorted(self._y_map.keys(), key=lambda n: (self._y_map[n], self._x_map[n]))
        self._content_height = len(final_lines)
        self._content_width = max((len(t.plain) for t in final_lines), default=0)
        self._view_x = 0
        self._view_y = 0
        self._visual = Text("\n").join(final_lines)

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


class DashboardHUD(Static):
    """Bottom HUD panel that shows the current node status and summary."""

    def __init__(self) -> None:
        super().__init__()
        self._current_node: tree_utils.AlignmentNode | None = None
        self._metrics: dict[str, object] = {}
        self._gpu_disabled = False
        self._spinner = itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")

    def on_mount(self) -> None:  # type: ignore[override]
        # Warm up CPU sampling so the first reading is not zero.
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass
        self._metrics = self._collect_metrics()
        self.update(self._render_empty())
        self.set_interval(1.0, self._refresh_metrics)

    def update_node(self, node: tree_utils.AlignmentNode) -> None:
        self._current_node = node
        self.update(self._render_dashboard(node))

    def update_node_placeholder(self, renderable: RenderableType | str) -> None:
        self._current_node = None
        self.update(renderable)

    def update_message(self, renderable: RenderableType | str) -> None:
        self.update(renderable)

    def _refresh_metrics(self) -> None:
        self._metrics = self._collect_metrics()
        if self._current_node:
            self.update(self._render_dashboard(self._current_node))

    def _render_empty(self) -> Panel:
        return Panel(Align.center("Waiting for selection...", vertical="middle"), title="System Status")

    def _draw_bar(self, percentage: float, width: int = 15, color: str = "blue") -> Text:
        percentage = max(0.0, min(1.0, percentage))
        filled_len = int(percentage * width)
        bar = "❚" * filled_len + "·" * (width - filled_len)
        return Text(bar, style=color)

    def _info_block(self, label: str, body: RenderableType, *, accent: str = "white") -> RenderableType:
        title = Text(label, style=f"bold {accent}")
        return Group(title, body)

    def _subtree_stats(self, node: tree_utils.AlignmentNode) -> dict[str, object]:
        rounds = list(node.iter_rounds())
        total_rounds = len(rounds)
        hal2fasta = sum(len(r.hal2fasta_steps) for r in rounds)

        # 子树模式（Subtree Mode）下，后代 round 会被标记成 Cactus 以避免混合状态，但执行上会被祖先 RaMAx 吸收；
        # 这里统计覆盖率时应使用“有效 RaMAx”口径，否则用户看到的覆盖率会与实际执行计划不一致。
        ramax_rounds = 0
        stack: list[tuple[tree_utils.AlignmentNode, bool]] = [(node, False)]
        while stack:
            current, covered = stack.pop()
            if current.round and (current.round.replace_with_ramax or covered):
                ramax_rounds += 1
            subtree_cover = covered or bool(current.round and _is_subtree_mode_round(current.round))
            for child in current.children:
                stack.append((child, subtree_cover))

        def _depth(n: tree_utils.AlignmentNode) -> int:
            if not n.children:
                return 1
            return 1 + max(_depth(c) for c in n.children)

        def _leaves(n: tree_utils.AlignmentNode) -> int:
            if not n.children:
                return 1
            return sum(_leaves(c) for c in n.children)

        jobstore = None
        for r in rounds:
            for step in (r.blast_step, r.align_step):
                if step and step.jobstore:
                    jobstore = step.jobstore
                    break
            if jobstore:
                break
        return {
            "total_rounds": total_rounds,
            "ramax_rounds": ramax_rounds,
            "hal2fasta": hal2fasta,
            "leaves": _leaves(node),
            "depth": _depth(node),
            "jobstore": jobstore,
        }

    def _render_dashboard(self, node: tree_utils.AlignmentNode) -> Table:
        # Mode and theme color
        if node.round:
            if _is_effective_ramax_node(node):
                mode_icon = "⚡"
                mode_name = "RaMAx Accelerated"
                theme_color = "yellow"
            else:
                mode_icon = "🌵"
                mode_name = "Cactus Classic"
                theme_color = "cyan"
            file_type = "HAL"
            target_file = Path(node.round.target_hal).name
        else:
            mode_icon = "🌿"
            mode_name = "Leaf Genome"
            theme_color = "green"
            file_type = "FASTA"
            target_file = node.name or "(leaf)"

        base_dir = getattr(self.app, "base_dir", Path.cwd())
        grid = Table.grid(expand=True, padding=(0, 2))
        grid.add_column(ratio=1)
        grid.add_column(ratio=2)
        grid.add_column(ratio=1)

        # --- Left column: identity card ---
        id_table = Table.grid(expand=True, padding=(0, 1))
        id_table.add_column(justify="right", style="dim #6272a4", width=8)
        id_table.add_column(justify="left", ratio=1)

        # Row 1: Node Name
        id_table.add_row("Node", Text(f"{mode_icon} {node.name or 'Unknown'}", style="bold white"))
        
        # Row 2: Parent & Length
        parent_name = node.parent.name if getattr(node, "parent", None) else "None (Root)"
        length_val = getattr(node, "length", None)
        length_str = f"{length_val:.4g}" if length_val is not None else "-"
        
        meta_info = Text.assemble(
            (parent_name, "white"),
            ("  Len: ", "dim #6272a4"),
            (length_str, "cyan")
        )
        id_table.add_row("Parent", meta_info)

        # Row 3: Mode
        id_table.add_row("Mode", Text(mode_name, style=theme_color))
        
        # Output Check
        out_status = "white"
        out_info = ""
        if node.round and node.round.target_hal:
            out_path = base_dir / node.round.target_hal
            if out_path.exists():
                 out_status = "green"
                 try:
                     size = out_path.stat().st_size
                     for unit in ["B", "KB", "MB", "GB", "TB"]:
                         if size < 1024:
                             break
                         size /= 1024
                     out_info = f" ({size:.1f}{unit})"
                 except Exception:
                     out_info = " (Ready)"
            else:
                 out_status = "dim white"
                 out_info = " (Pending)"
        
        # Row 4: Output File
        id_table.add_row("Output", Text(f"{target_file}{out_info}", overflow="ellipsis", style=out_status))

        # Row 5: Workdir / Root
        if node.round:
            wd_status = "white"
            if node.round.workdir:
                wd_path = base_dir / node.round.workdir
                if wd_path.exists() and wd_path.is_dir():
                    wd_status = "green"
                else:
                    wd_status = "dim white"
            workdir_text = node.round.workdir or "N/A"
            id_table.add_row("Workdir", Text(workdir_text, overflow="ellipsis", style=wd_status))
            
            if node.round.manual_ramax_command:
                id_table.add_row("Custom", Text("Manual Command Set", style="bold yellow"))

        identity_panel = Panel(id_table, title="[Identity]", border_style=f"dim {theme_color}", padding=(0, 1), height=11)

        # --- Middle column: statistics overview ---
        stats = self._subtree_stats(node)
        
        # Get whole-tree statistics
        total_stats = stats
        if hasattr(self.app, "alignment_tree") and self.app.alignment_tree:
             total_stats = self._subtree_stats(self.app.alignment_tree.root)

        def _make_section(title: str, data: dict, color: str) -> RenderableType:
             cov = (data["ramax_rounds"] / data["total_rounds"]) if data["total_rounds"] else 0.0
             # Dynamically adjust progress-bar width
             mid_width = max(20, self.size.width // 3)
             bar_w = max(6, min(15, mid_width - 22))
             
             bar = self._metric_bar(cov * 100, bar_width=bar_w, accent=color)
             cov_txt = f"{data['ramax_rounds']}/{data['total_rounds']}"
             
             # First line: title + progress bar + value
             header = Table.grid(expand=True, padding=(0, 1))
             header.add_column(style=f"bold {color}", width=8)
             header.add_column()
             header.add_column(justify="right", width=len(cov_txt))
             header.add_row(title, bar, Text(cov_txt, style="white"))
             
             # Second line: detail metrics
             details = Text.assemble(
                 ("Leaves: ", "dim #6272a4"), (str(data['leaves']), "white"), "  ",
                 ("Depth: ", "dim #6272a4"), (str(data['depth']), "white"), "  ",
                 ("H2F: ", "dim #6272a4"), (str(data['hal2fasta']), "white")
             )
             return Group(header, details)

        sub_group = _make_section("Subtree", stats, "yellow")
        tot_group = _make_section("Total", total_stats, "cyan")
        
        # Combine sections with an empty line in between
        content = Group(sub_group, Text(" "), tot_group)
        
        config_panel = Panel(content, title="[Statistics]", border_style="dim white", padding=(0, 1), height=11)

        # Right column: live system metrics
        metrics_panel = self._render_metrics_panel()

        grid.add_row(identity_panel, config_panel, metrics_panel)
        return grid

    def _render_metrics_panel(self) -> Panel:
        metrics = self._metrics or {}
        cpu_percent = metrics.get("cpu_percent")
        mem = metrics.get("mem")
        gpus = metrics.get("gpus")
        disk = metrics.get("disk")
        spinner = next(self._spinner)

        # Adjust bar width to work on narrow terminals.
        right_width = max(30, self.size.width // 3)
        bar_width = max(10, min(30, right_width - 10))

        blocks: list[RenderableType] = []

        blocks.append(
            self._metric_block("CPU", cpu_percent, f"{cpu_percent:.0f}%" if isinstance(cpu_percent, (int, float)) else "-", "cyan", bar_width)
        )

        if isinstance(mem, dict):
            m_percent = mem.get("percent", 0.0)
            used = mem.get("used_gb")
            total = mem.get("total_gb")
            usage = f"{used:.1f}/{total:.1f} GB" if used is not None and total is not None else ""
            blocks.append(
                self._metric_block("Memory", m_percent, f"{m_percent:.0f}% {usage}".strip(), "green", bar_width)
            )
        else:
            blocks.append(self._metric_block("Memory", None, "N/A", "green", bar_width))

        if isinstance(gpus, list) and gpus:
            gpu = gpus[0]
            g_util = gpu.get("util", 0.0)
            g_mem_percent = gpu.get("mem_percent", 0.0)
            g_mem = f"{gpu.get('mem_used', 0):.1f}/{gpu.get('mem_total', 0):.1f} GB"
            detail = f"{g_util:.0f}% {g_mem} ({g_mem_percent:.0f}%)"
            blocks.append(self._metric_block("GPU", g_util, detail, "yellow", bar_width))
        else:
            blocks.append(self._metric_block("GPU", None, "Not detected", "yellow", bar_width))

        if isinstance(disk, dict):
            d_percent = disk.get("percent", 0.0)
            d_text = f"{d_percent:.0f}% {disk.get('used_gb', 0):.1f}/{disk.get('total_gb', 0):.1f} GB"
            blocks.append(self._metric_block("Disk", d_percent, d_text, "magenta", bar_width))

        table = Table.grid(padding=(0, 0), expand=True)
        table.add_column(ratio=1)
        for block in blocks:
            table.add_row(block)

        title = Text.assemble(
            ("[Live] ", "dim"),
            ("System resources ", "white"),
            (spinner, "cyan"),
        )
        return Panel(table, title=title, border_style="bright_blue", padding=(0, 1), height=11)

    def _bar_color(self, percent: float) -> str:
        if percent >= 85:
            return "red"
        if percent >= 60:
            return "yellow"
        return "green"

    def _metric_bar(self, percent: float | None, bar_width: int = 22, accent: str = "green") -> Text:
        if percent is None:
            return Text("N/A", style="dim")
        pct = max(0.0, min(100.0, float(percent)))
        return self._draw_bar(pct / 100.0, width=bar_width, color=self._bar_color(pct))

    def _metric_block(
        self,
        label: str,
        percent: float | None,
        detail: str,
        accent: str,
        bar_width: int,
    ) -> RenderableType:
        title = Text(label, style=f"bold {accent}")
        bar = self._metric_bar(percent, bar_width=bar_width, accent=accent)
        value = Text(detail, style="white")
        bar_line = Text.assemble(bar, "  ", value, no_wrap=True)
        return Group(title, bar_line)

    def _metric_text(self, percent: float | None, suffix: str = "") -> Text:
        if percent is None:
            return Text("-", style="dim")
        return Text(f"{percent:4.0f}{suffix}", style="white")

    def _collect_metrics(self) -> dict[str, object]:
        data: dict[str, object] = {}
        try:
            cpu_percent = psutil.cpu_percent(interval=None)
            data["cpu_percent"] = cpu_percent
        except Exception:
            pass

        try:
            mem = psutil.virtual_memory()
            data["mem"] = {
                "percent": mem.percent,
                "used_gb": mem.used / (1024**3),
                "total_gb": mem.total / (1024**3),
            }
        except Exception:
            pass

        try:
            disk = psutil.disk_usage(Path.cwd())
            data["disk"] = {
                "percent": disk.percent,
                "used_gb": disk.used / (1024**3),
                "total_gb": disk.total / (1024**3),
            }
        except Exception:
            pass

        gpu_stats = self._collect_gpu_metrics()
        if gpu_stats:
            data["gpus"] = gpu_stats
        return data

    def _collect_gpu_metrics(self) -> list[dict[str, float]] | None:
        if self._gpu_disabled:
            return None
        if shutil.which("nvidia-smi") is None:
            self._gpu_disabled = True
            return None
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=0.3,
                check=True,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            self._gpu_disabled = True
            return None

        gpus: list[dict[str, float]] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                util = float(parts[0])
                mem_used = float(parts[1])
                mem_total = float(parts[2])
            except ValueError:
                continue
            mem_percent = (mem_used / mem_total * 100) if mem_total else 0.0
            gpus.append(
                {
                    "util": util,
                    "mem_used": mem_used / 1024 if mem_used else 0.0,
                    "mem_total": mem_total / 1024 if mem_total else 0.0,
                    "mem_percent": mem_percent,
                }
            )

        if not gpus:
            self._gpu_disabled = True
            return None
        return gpus

class RunSettingsScreen(Screen[RunSettings | None]):
    """Dedicated screen for confirming run-time configuration."""

    BINDINGS = [
        Binding("escape", "cancel", "Back"),
        Binding("ctrl+enter", "save", "Run"),
        Binding("ctrl+r", "save", "Run"),
        Binding("v", "toggle_verbose", "Toggle verbose"),
        Binding("f6", "toggle_view", "View"),
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

    def __init__(
        self,
        plan: Plan,
        current: RunSettings,
        compact: bool,
        resume_available: bool = False,
    ):
        super().__init__()
        self.plan = plan
        self.current = current
        self.compact = compact
        self.resume_available = resume_available
        self._summary: Static | None = None
        self._input: Input | None = None
        self._verbose: Checkbox | None = None
        self._status: Static | None = None
        self._view_mode: str = "resume" if (resume_available and current.resume) else "flow"  # resume | flow | table

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="run-root"):
            yield Static("Plan is ready. Review run settings before execution:", id="run-title")
            with Container(id="run-body"):
                summary = Static(id="run-summary")
                self._summary = summary
                summary.update(self._render_summary(self.current))
                yield summary
                with Container(id="run-form"):
                    yield Static("• Tab/Shift+Tab to move between controls\n• Ctrl+Enter to run immediately\n• V toggles verbose logging\n• F6 toggles overview view", id="run-instructions")
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
        settings = self._current_settings_preview()
        # 替换线程数为已校验的值，避免在预览中使用旧值
        settings.thread_count = threads
        self.dismiss(settings)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_toggle_verbose(self) -> None:
        if self._verbose:
            self._verbose.value = not self._verbose.value
            self._refresh_summary()

    def action_toggle_view(self) -> None:
        # 切换左侧总览的呈现方式：有续跑状态时支持 resume/flow/table 三态，否则保持 flow/table 二态。
        if self.resume_available:
            modes = ("resume", "flow", "table")
            try:
                idx = modes.index(self._view_mode)
            except ValueError:
                idx = 0
            self._view_mode = modes[(idx + 1) % len(modes)]
        else:
            self._view_mode = "table" if self._view_mode == "flow" else "flow"
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
        return RunSettings(verbose=verbose, thread_count=thread_val, resume=self.current.resume)

    def _refresh_summary(self) -> None:
        if not self._summary:
            return
        settings = self._current_settings_preview()
        self._summary.update(self._render_summary(settings))

    def _render_summary(self, settings: RunSettings) -> RenderableType:
        if self._view_mode == "resume":
            return self._render_resume_overview(settings)
        if self._view_mode == "flow":
            return self._render_flow_overview(settings)
        return plan_overview(self.plan, run_settings=settings, compact=self.compact)

    def _render_resume_overview(self, settings: RunSettings) -> RenderableType:
        app = self.app
        base_dir = app.base_dir if isinstance(app, PlanUIApp) else Path.cwd()

        preview = resume_utils.preview_resume(
            self.plan,
            base_dir=base_dir,
            thread_count=settings.thread_count,
        )
        rows = resume_utils.command_rows(
            self.plan,
            base_dir=base_dir,
            thread_count=settings.thread_count,
        )

        next_row = next((row for row in rows if row.status != resume_utils.STATUS_COMPLETED), None)
        header = Text()
        header.append("Resume: ", style="bold cyan")
        if not settings.resume:
            header.append("disabled (full run)", style="dim")
        elif next_row is None:
            header.append("all steps are skippable (outputs present)", style="bold green")
        else:
            header.append(f"starts at step {next_row.index}: ", style="dim")
            header.append(next_row.name, style="bold white")

        if preview is None:
            message = Panel(
                "Unable to read run_state.json (missing or invalid). The plan will run in full; to resume, ensure the state file exists and is readable.",
                border_style="yellow",
            )
            return Group(header, Text(""), message, resume_utils.render_command_table(rows, limit=200))

        summary_table, state_panel = resume_utils.render_summary(preview)
        warning: Panel | None = None
        if not preview.plan_matches:
            warning = Panel(
                "Note: the plan signature in the state file does not match the current plan (thread count / edits can cause this).\n"
                "Resume will skip only the contiguous completed prefix using command matching + output checks; once a step needs rerun, all subsequent steps will rerun.",
                border_style="yellow",
            )
        parts: list[RenderableType] = [header, Text(""), summary_table, state_panel]
        if warning is not None:
            parts.insert(2, warning)
        parts.append(resume_utils.render_command_table(rows, limit=200))
        return Group(*parts)

    def _render_flow_overview(self, settings: RunSettings) -> Panel:
        # Header
        header = Text()
        header.append("Threads: ", style="dim")
        header.append("auto" if settings.thread_count is None else str(settings.thread_count), style="bold white")
        header.append("  Verbose: ", style="dim")
        header.append("on" if settings.verbose else "off", style="bold green" if settings.verbose else "bold #aaaaaa")
        
        canvas_text = self._draw_dependency_tree()

        content = Group(header, Text(""), canvas_text)
        return Panel(content, title="[Execution Dependency Tree]", border_style="magenta", padding=(0, 1))

    def _draw_dependency_tree(self) -> Text:
        """
        Builds a visual dependency tree of the Rounds based on input/output relationships.
        Returns a Rich Text object containing the ASCII art.
        """
        if not self.plan.rounds:
            return Text("No rounds planned.", style="dim red")

        node_map: dict[str, Round] = {r.root: r for r in self.plan.rounds}
        phylo_root = getattr(self.app, "alignment_tree", None)
        if not phylo_root:
             return Text("Phylogeny tree missing.", style="dim red")
        
        @dataclass
        class TreeNode:
            round_entry: Round | None
            name: str
            children: list["TreeNode"]
            width: int = 0
            x: int = 0
            y: int = 0
            is_clustered: bool = False
            
        def build_node(phylo_node: tree_utils.AlignmentNode) -> TreeNode:
            r = node_map.get(phylo_node.name)
            c_nodes = [build_node(c) for c in phylo_node.children]
            return TreeNode(round_entry=r, name=phylo_node.name, children=c_nodes)

        root_tree_node = build_node(phylo_root.root)

        def is_relevant(tn: TreeNode) -> bool:
            if tn.round_entry: return True
            return any(is_relevant(c) for c in tn.children)

        if not is_relevant(root_tree_node):
             return Text("No active rounds in tree.", style="dim yellow")

        # Analyze Connectivity and Propagate Subtree Mode
        def analyze_connectivity(tn: TreeNode, override_as_input: bool = False):
            if override_as_input:
                # If a parent is in Subtree Mode, this node ceases to be a Round
                # and becomes a raw input source for the parent.
                tn.round_entry = None
            
            is_ramax = tn.round_entry and tn.round_entry.replace_with_ramax
            is_subtree_mode = is_ramax and "--subtree-mode" in tn.round_entry.ramax_opts
            
            if is_subtree_mode:
                tn.is_clustered = True
                # Propagate the override to all children
                for c in tn.children:
                    analyze_connectivity(c, override_as_input=True)
            else:
                tn.is_clustered = False
                # Continue normal recursion
                for c in tn.children:
                    analyze_connectivity(c, override_as_input=override_as_input)

        analyze_connectivity(root_tree_node)

        # Layout constants
        BOX_WIDTH = 14
        H_GAP = 2
        V_GAP = 2

        def measure_width(tn: TreeNode) -> int:
            if not tn.children:
                tn.width = BOX_WIDTH
                return BOX_WIDTH
            c_width = sum(measure_width(c) for c in tn.children) + (len(tn.children) - 1) * H_GAP
            tn.width = max(BOX_WIDTH, c_width)
            return tn.width

        measure_width(root_tree_node)

        def layout(tn: TreeNode, start_x: int, depth: int):
            tn.y = depth * (3 + V_GAP)
            tn.x = start_x + tn.width // 2
            
            total_c_width = sum(c.width for c in tn.children) + (max(0, len(tn.children)-1) * H_GAP)
            c_start_x = tn.x - total_c_width // 2
            
            for c in tn.children:
                layout(c, c_start_x, depth + 1)
                c_start_x += c.width + H_GAP

        layout(root_tree_node, 0, 0)

        max_w = root_tree_node.width
        max_h = 0
        def get_max_h(tn):
            nonlocal max_h
            max_h = max(max_h, tn.y + 3)
            for c in tn.children: get_max_h(c)
        get_max_h(root_tree_node)

        # Pixel buffer: (x, y) -> (char, style)
        pixels: dict[tuple[int, int], tuple[str, str]] = {}

        def put(x, y, char, style="white"):
            if 0 <= y < max_h + 10 and 0 <= x < max_w + 10:
                pixels[(x, y)] = (char, style)

        def draw_node_recursive(tn: TreeNode):
            left = tn.x - BOX_WIDTH // 2
            top = tn.y
            
            is_ramax = tn.round_entry and tn.round_entry.replace_with_ramax
            
            if tn.round_entry:
                if is_ramax:
                    color = "yellow"
                    border_color = "yellow"
                    icon = "R"
                    use_double = tn.is_clustered
                else:
                    color = "cyan"
                    border_color = "blue"
                    icon = "C"
                    use_double = False
            else:
                color = "green"
                border_color = "dim green"
                icon = "L"
                use_double = False
            
            # Box Chars
            if use_double:
                tl, tr, bl, br = "╔", "╗", "╚", "╝"
                h, v = "═", "║"
            else:
                tl, tr, bl, br = "┌", "┐", "└", "┘"
                h, v = "─", "│"
            
            # Box Drawing
            put(left, top, tl, border_color)
            for i in range(1, BOX_WIDTH-1): put(left+i, top, h, border_color)
            put(left+BOX_WIDTH-1, top, tr, border_color)
            
            put(left, top+1, v, border_color)
            
            raw_label = tn.name
            content_space = BOX_WIDTH - 2
            full_str = f"{icon} {raw_label}"
            if len(full_str) > content_space:
                full_str = f"{icon} {raw_label[:content_space-4]}.."
            
            padding_left = (content_space - len(full_str)) // 2
            start_x = left + 1 + padding_left
            
            put(start_x, top+1, icon, color)
            for i, char in enumerate(full_str):
                if i == 0: continue
                put(start_x + i, top+1, char, "bold white")
                
            put(left+BOX_WIDTH-1, top+1, v, border_color)
            
            put(left, top+2, bl, border_color)
            for i in range(1, BOX_WIDTH-1): put(left+i, top+2, h, border_color)
            put(left+BOX_WIDTH-1, top+2, br, border_color)

            # Connections
            if tn.children:
                put(tn.x, top+2, "┴", border_color)
                
                mid_y = top + 3
                put(tn.x, mid_y, "│", border_color)
                
                min_cx = min(c.x for c in tn.children)
                max_cx = max(c.x for c in tn.children)
                
                for x in range(min_cx, max_cx + 1):
                    char = "─"
                    line_style = "dim white"
                    
                    if x == tn.x: char = "┼"
                    elif x == min_cx: char = "┌"
                    elif x == max_cx: char = "┐"
                    
                    is_child_stem = any(c.x == x for c in tn.children)
                    if is_child_stem:
                        if char == "─": char = "┬"
                        if char == "┌": char = "┌"
                        if char == "┐": char = "┐"
                        if char == "┼": char = "┼"
                    
                    put(x, mid_y, char, line_style)

                for c in tn.children:
                    child_is_ramax = c.round_entry and c.round_entry.replace_with_ramax
                    if is_ramax and child_is_ramax:
                        conn_style = "yellow"
                    else:
                        conn_style = "dim white"
                        
                    put(c.x, mid_y+1, "↓", conn_style)

            for c in tn.children:
                draw_node_recursive(c)

        draw_node_recursive(root_tree_node)

        if not pixels: return Text("Empty Tree", style="red")

        sorted_pixels = sorted(pixels.items(), key=lambda item: (item[0][1], item[0][0]))
        final_text = Text()
        row_map: dict[int, list[tuple[int, str, str]]] = {}
        
        for (x, y), (char, style) in sorted_pixels:
            if y not in row_map: row_map[y] = []
            row_map[y].append((x, char, style))

        min_x = min(k[0] for k in pixels.keys())
        
        for y in sorted(row_map.keys()):
            row_pixels = row_map[y]
            cursor = min_x
            for x, char, style in row_pixels:
                if x > cursor:
                    final_text.append(" " * (x - cursor))
                final_text.append(char, style=style)
                cursor = x + 1
            final_text.append("\n")

        return final_text

    def _flow_preview_width(self) -> int:
        try:
            width = self.size.width
        except Exception:
            width = 80
        return max(40, min(90, width - 18))

    def _shorten(self, text: str, width: int) -> str:
        if len(text) <= width:
            return text
        return text[: width - 1] + "…"


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
    /* Deep Space HUD theme */
    $bg-deep: #0f111a;
    $bg-panel: #1a1d2e;
    $border-bright: #444b6a;
    $text-main: #e0def4;
    $accent-gold: #f6c177;
    $accent-blue: #9ccfd8;
    $accent-green: #31748f;

    Screen {
        layout: vertical;
        min-height: 0;
        background: $bg-deep;
        color: $text-main;
    }
    #tree-container {
        height: 1fr;
        width: 100%;
        background: $bg-deep;
        overflow: hidden;
    }
    AsciiPhylo {
        width: 100%;
        height: 100%;
        background: $bg-deep;
        padding: 1 2;
    }
    #ascii-phylo-empty {
        align: center middle;
        color: #6b768f;
    }
    DashboardHUD {
        dock: bottom;
        height: 13;
        width: 100%;
        background: $bg-panel;
        border-top: heavy $border-bright;
    }
    #editor-command { height: 10; }

    ModalScreen {
        background: rgba(0, 0, 0, 0.6);
        align: center middle;
    }
    #picker-dialog, #editor-dialog, #info-dialog, #search-dialog, #round-picker, #options-dialog, #run-form {
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    Input {
        background: $bg-panel;
        border: none;
        color: $text-main;
    }
    Input:focus {
        border: tall $accent-blue;
    }
    Button {
        border: none;
        background: $bg-panel;
        color: $text-main;
    }
    Button:hover {
        background: $accent-blue;
        color: #111;
    }
    Button.variant-success {
        background: #a6e3a1;
        color: #111;
    }
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
        self._run_state_path = self._resolve_run_state_path()
        self.resume_available = self._run_state_path.exists()
        self.canvas: AsciiPhylo | None = None
        self.run_settings = run_settings or RunSettings()
        self.hud: DashboardHUD | None = None
        self._last_detail_text: str = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="tree-container"):
            if self.alignment_tree:
                canvas = AsciiPhylo(self.alignment_tree.root)
                canvas.set_detail_callback(self._on_node_selected)
                self.canvas = canvas
                yield canvas
            else:
                yield Static("Alignment tree not found; nothing to render.", id="ascii-phylo-empty")
        hud = DashboardHUD()
        self.hud = hud
        yield hud
        yield Footer()

    def on_mount(self) -> None:
        self.detail_panel = DetailBuffer(self)
        if self.canvas:
            self.canvas.focus()
            self._on_node_selected(self.canvas.current_node())
        else:
            preview = plan_overview(self.plan, run_settings=self.run_settings, compact=self._is_compact())
            self.detail_panel.update(preview)
        
        if self.run_settings.resume and self.resume_available:
            # 断点续跑专属入口：直接进入运行设置/续跑摘要界面。
            self.set_timer(0.05, self.action_run_plan)
        else:
            # Delay the welcome overlay slightly so the UI renders first.
            self.set_timer(0.3, self._show_welcome_guide)

    def _resolve_run_state_path(self) -> Path:
        if self.plan.out_dir:
            out_dir = Path(self.plan.out_dir).expanduser()
            if not out_dir.is_absolute():
                out_dir = (self.base_dir / out_dir).resolve()
            return (out_dir / "logs" / "run_state.json").resolve()
        return (self.base_dir / "logs" / "run_state.json").resolve()

    def _show_welcome_guide(self) -> None:
        welcome_text = (
            "Welcome to the Cactus-RaMAx Planner!\n\n"
            "This interactive UI allows you to inspect and configure the phylogenetic alignment plan.\n\n"
            "• [bold]Navigation[/]: Use Arrow keys or h/j/k/l to browse the tree.\n"
            "• [bold]Toggle RaMAx[/]: Press [bold]SPACE[/] on a node to enable/disable acceleration.\n"
            "• [bold]Edit[/]: Press [bold]Enter[/] or [bold]E[/] to customize commands and options.\n"
            "• [bold]Search[/]: Press [bold]/[/] to find species or nodes.\n"
            "• [bold]Run[/]: Press [bold]R[/] to review settings and start execution.\n"
        )
        self.push_screen(InfoModal("Quick Start Guide", welcome_text))

    def _is_compact(self) -> bool:
        return self.size.width <= 100

    def action_show_info(self) -> None:
        content = self._last_detail_text or "(empty)"
        self.push_screen(InfoModal("Current node details", content))

    def action_edit_round(self) -> None:
        if not self.plan.rounds:
            self._last_detail_text = "No rounds found in this plan."
            if self.hud:
                self.hud.update_message(self._last_detail_text)
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
            self._last_detail_text = "No editable commands for this round."
            if self.hud:
                self.hud.update_message(self._last_detail_text)
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
        screen = RunSettingsScreen(
            self.plan,
            self.run_settings,
            compact=self._is_compact(),
            resume_available=self.resume_available,
        )
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
        if notify_detail:
            self._last_detail_text = f"[green]Commands saved to {output_path}[/green]"
            if self.hud:
                self.hud.update_message(self._last_detail_text)
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
        if index >= len(self.plan.rounds):
            return
        round_entry = self.plan.rounds[index]
        details = self._round_details(round_entry)
        if status:
            details.extend(["", f"[green]{status}[/green]"])
        border_style = "green" if round_entry.replace_with_ramax else "cyan"
        panel = Panel("\n".join(details), title=round_entry.name, border_style=border_style, padding=(1, 1))
        self._last_detail_text = "\n".join(details)
        if self.hud:
            self.hud.update_message(panel)

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
        self._last_detail_text = "\n".join(details)
        if self.hud:
            self.hud.update_message(Panel(self._last_detail_text, title=node.round.name if node.round else (node.name or "Node"), border_style="green" if node.round and node.round.replace_with_ramax else "cyan", padding=(1, 1)))

    def _handle_command_selection(self, round_index: int, target: CommandTarget | None) -> None:
        if target is None:
            return
        self._open_command_editor(round_index, target)

    def _on_node_selected(
        self, node: tree_utils.AlignmentNode, status: str | None = None
    ) -> None:
        """Tree navigation callback that drives HUD updates."""
        self._show_alignment_node(node, status=status)
        if self.hud:
            self.hud.update_node(node)

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
