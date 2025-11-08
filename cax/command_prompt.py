"""Textual prompt for collecting cactus-prepare commands from the user."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
from typing import Literal
from textwrap import dedent

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.events import Resize
from textual.geometry import Size
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, ListItem, ListView, Static

from rich.console import Group
from rich.text import Text
from . import history, templates


@dataclass
class PromptResult:
    """Result returned from the command prompt."""

    executable: str
    args: str
    action: Literal["submit", "quit"]


class PrepareWizard(Screen[str | None]):
    """Popup wizard that collects cactus-prepare arguments field by field."""

    BINDINGS = [
        Binding("escape", "cancel", "Back"),
    ]

    FIELD_DEFINITIONS = [
        ("spec", "Species/plan file", "examples/evolverMammals.txt"),
        ("out_dir", "--outDir", str(templates.default_output_dir("run"))),
        ("out_seq", "--outSeqFile", "steps-output/out.txt"),
        ("out_hal", "--outHal", "steps-output/out.hal"),
        ("job_store", "--jobStore", "jobstore"),
        ("extra", "Extra arguments", "--maxCores 32"),
    ]

    def __init__(self, defaults: dict[str, str] | None = None) -> None:
        super().__init__()
        self._status: Static | None = None
        self._fields: dict[str, Input] = {}
        self._defaults = defaults or {}
        self._instructions: Static | None = None
        self._is_compact: bool | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="wizard-layout"):
            with Container(id="wizard-body"):
                with VerticalScroll(id="wizard-scroll", can_focus=True, can_focus_children=True) as scroll:
                    scroll.show_horizontal_scrollbar = False
                    instructions = Static(self._instructions_text(False), id="wizard-instructions")
                    self._instructions = instructions
                    yield instructions
                    for field_id, label, placeholder in self.FIELD_DEFINITIONS:
                        value = self._defaults.get(field_id, "")
                        input_widget = Input(
                            value=value,
                            placeholder=placeholder,
                            id=f"wizard-{field_id}",
                        )
                        self._fields[field_id] = input_widget
                        yield Static(label, classes="wizard-label")
                        yield input_widget
            with Container(id="wizard-footer"):
                with Container(id="wizard-actions"):
                    yield Button("Generate command", id="submit", variant="success", flat=True)
                    yield Button("Cancel", id="cancel", flat=True)
                status = Static("", id="wizard-status")
                self._status = status
                yield status
        yield Footer()

    def on_mount(self) -> None:  # type: ignore[override]
        spec_input = self._fields.get("spec")
        if spec_input:
            spec_input.focus()
        self._apply_layout_mode(self.size)

    def on_resize(self, event: Resize) -> None:  # type: ignore[override]
        self._apply_layout_mode(event.size)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:  # type: ignore[override]
        if event.button.id == "cancel":
            self.action_cancel()
            return
        if event.button.id == "submit":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:  # type: ignore[override]
        event.stop()
        self._submit()

    def _submit(self) -> None:
        command = self._build_command()
        if command:
            self.dismiss(command)

    def _build_command(self) -> str | None:
        spec = self._fields.get("spec")
        if not spec or not spec.value.strip():
            self._update_status("[red]A species/plan file path is required[/red]")
            return None
        tokens: list[str] = ["cactus-prepare", spec.value.strip()]
        mapping = [
            ("out_dir", "--outDir"),
            ("out_seq", "--outSeqFile"),
            ("out_hal", "--outHal"),
            ("job_store", "--jobStore"),
        ]
        for field_id, flag in mapping:
            value = self._fields[field_id].value.strip()
            if value:
                tokens.extend([flag, value])
        extra = self._fields["extra"].value.strip()
        if extra:
            try:
                tokens.extend(shlex.split(extra))
            except ValueError as exc:
                self._update_status(f"[red]Failed to parse extra arguments: {exc}[/red]")
                return None
        self._update_status("[green]Command generated. Confirm to return to the main view.[/green]")
        return shlex.join(tokens)

    def _update_status(self, message: str) -> None:
        if self._status:
            self._status.update(message)

    def _instructions_text(self, compact: bool) -> Text:
        if compact:
            return Text.from_markup("[bold cyan]Fill the fields below; blank entries are ignored.[/]")
        return Text.from_markup(
            "[bold cyan]Provide the cactus-prepare arguments in each field.[/]\n"
            "Leave a field blank to skip it, then use the buttons below to confirm."
        )

    def _apply_layout_mode(self, size: Size | None) -> None:
        if size is None:
            size = self.size
        compact = size.width < 96 or size.height < 28
        stacked_actions = size.width < 72
        if self._instructions and compact != self._is_compact:
            self._instructions.update(self._instructions_text(compact))
        self._is_compact = compact
        self.set_class(compact, "compact")
        self.set_class(stacked_actions, "stacked-actions")


class TemplateSelector(Screen[templates.Template | None]):
    """Simple screen that lists templates and returns the chosen one."""

    BINDINGS = [
        Binding("escape", "cancel", "Back"),
    ]

    def __init__(self, template_list: list[templates.Template]) -> None:
        super().__init__()
        self._templates = template_list
        self._list_view: ListView | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="template-layout"):
            title = Text.from_markup("[bold cyan]Choose a template[/]")
            yield Static(title, id="template-title")
            items = []
            for template in self._templates:
                text = Text.assemble((template.name, "bold"), "\n", (template.spec, "dim"))
                items.append(ListItem(Static(text), name=template.name))
            list_view = ListView(*items, id="template-list")
            self._list_view = list_view
            yield list_view
        yield Footer()

    def on_mount(self) -> None:  # type: ignore[override]
        if self._list_view:
            self._list_view.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:  # type: ignore[override]
        event.stop()
        template = self._templates[event.index] if 0 <= event.index < len(self._templates) else None
        self.dismiss(template)


class HistoryViewer(Screen[str | None]):
    """Full-screen history viewer that returns the selected command."""

    BINDINGS = [
        Binding("escape", "cancel", "Back"),
        Binding("delete", "delete_entry", "Delete"),
        Binding("d", "delete_entry", "Delete"),
    ]

    def __init__(self, entries: list[history.HistoryEntry]) -> None:
        super().__init__()
        self._entries = entries
        self._list_view: ListView | None = None
        self._content_container: Container | None = None
        self._status: Static | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="history-layout"):
            instructions = Text.from_markup(
                dedent(
                    """
                    [bold cyan]History[/]
                    • Use the arrow keys or PgUp/PgDn to scroll
                    • Press Enter to copy the selected command into the main view
                    • Press [bold magenta]D[/] or [bold magenta]Delete[/] to remove the selected command
                    • Press Esc to go back
                    """
                ).strip()
            )
            yield Static(instructions, id="history-instructions")
            content = Container(*self._build_history_content(), id="history-content")
            self._content_container = content
            yield content
            status = Static("", id="history-status")
            self._status = status
            yield status
        yield Footer()

    def on_mount(self) -> None:  # type: ignore[override]
        if self._list_view:
            self._list_view.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:  # type: ignore[override]
        event.stop()
        if 0 <= event.index < len(self._entries):
            command = self._entries[event.index].command
            self.dismiss(command)
        else:
            self.dismiss(None)

    async def action_delete_entry(self) -> None:
        if not self._entries:
            self._update_status("[yellow]No entries to delete[/yellow]")
            return
        if not self._list_view or self._list_view.index is None:
            self._update_status("[yellow]Select a command to delete first[/yellow]")
            return
        index = self._list_view.index
        if not history.delete_entry(index):
            self._update_status("[red]Delete failed, please try again[/red]")
            return
        del self._entries[index]
        await self._refresh_history_content()
        if self._entries:
            self._update_status(f"[green]Deleted history command #{index + 1}[/green]")
        else:
            self._update_status("[green]History cleared[/green]")

    def _build_history_content(self) -> list[Static | ListView]:
        self._list_view = None
        if not self._entries:
            empty = Text.from_markup(
                "[dim]No history yet. After you run cactus-prepare, the latest commands will appear here.[/]"
            )
            return [Static(empty, id="history-empty")]
        items = []
        for idx, entry in enumerate(self._entries, start=1):
            text = Text.from_markup(f"[bold cyan]#{idx}[/] {entry.command}")
            items.append(ListItem(Static(text), name=str(idx)))
        list_view = ListView(*items, id="history-list")
        self._list_view = list_view
        return [list_view]

    async def _refresh_history_content(self) -> None:
        if not self._content_container:
            return
        await self._content_container.remove_children()
        for widget in self._build_history_content():
            await self._content_container.mount(widget)
        if self._list_view:
            self._list_view.focus()

    def _update_status(self, message: str) -> None:
        if self._status:
            self._status.update(message)


class PrepareCommandPrompt(App[PromptResult]):
    """Minimal Textual app that requests a cactus-prepare command."""

    CSS = """
    Screen { layout: vertical; min-height: 0; }
    #prepare-layout { layout: vertical; padding: 1 2; min-height: 0; width: 1fr; }
    #content { layout: vertical; padding-bottom: 2; width: 1fr; height: 1fr; min-height: 0; }
    #instructions-panel { layout: vertical; width: 1fr; min-height: 0; }
    #instructions-title { padding-bottom: 1; }
    .instructions-block { padding-bottom: 1; }
    .instructions-block:last-child { padding-bottom: 0; }
    #prepare-bottom { layout: vertical; padding: 1 2; width: 1fr; height: auto; min-height: 0; }
    #command-title { color: $accent; }
    #command { margin: 1 0; }
    #status { padding: 0 0 1 0; }

    /* Wizard screen */
    #wizard-layout {
        layout: vertical;
        padding: 1 2;
        min-height: 0;
        width: 1fr;
        height: 1fr;
    }
    #wizard-body {
        layout: vertical;
        min-height: 0;
        width: 1fr;
        height: 1fr;
    }
    #wizard-scroll {
        layout: vertical;
        padding: 0 1;
        min-height: 0;
        width: 1fr;
        height: 1fr;
        overflow-y: auto;
    }
    .wizard-label { padding-top: 1; }
    .wizard-label:first-of-type { padding-top: 0; }
    #wizard-footer { layout: vertical; padding-top: 1; height: auto; min-height: 0; max-height: 7; }
    #wizard-actions { layout: horizontal; padding: 1 0; }
    #wizard-actions Button { margin-right: 1; }
    #wizard-actions Button:last-child { margin-right: 0; }
    #wizard-status { padding: 0 0 1 0; }
    .compact #wizard-layout { padding: 1 1; }
    .compact #wizard-instructions { padding-bottom: 0; }
    .stacked-actions #wizard-actions { layout: vertical; }
    .stacked-actions #wizard-actions Button {
        margin-right: 0;
        margin-bottom: 1;
        width: 1fr;
    }
    .stacked-actions #wizard-actions Button:last-child { margin-bottom: 0; }

    /* Template selection */
    #template-layout { layout: vertical; height: 1fr; padding: 1 2; min-height: 0; }
    #template-title { padding-bottom: 1; }
    #template-list { height: 1fr; min-height: 0; }

    /* History view */
    #history-layout { layout: vertical; height: 1fr; padding: 1 2; min-height: 0; }
    #history-instructions { padding-bottom: 1; }
    #history-content { height: 1fr; min-height: 0; }
    #history-list { height: 1fr; min-height: 0; }
    #history-status { padding-top: 1; }
    """

    BINDINGS = [
        Binding("escape", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit"),
        Binding("f2", "open_wizard", "Wizard"),
        Binding("f3", "choose_template", "Templates"),
        Binding("ctrl+shift+w", "open_wizard", "Wizard"),
        Binding("ctrl+shift+t", "choose_template", "Templates"),
        Binding("f4", "show_history", "History"),
        Binding("ctrl+shift+h", "show_history", "History"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._command_input: Input | None = None
        self._status: Static | None = None
        self._history_entries = history.load_history()
        self._templates = templates.load_templates()
        self._template_defaults: dict[str, str] | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="prepare-layout"):
            with Container(id="content"):
                with Container(id="instructions-panel"):
                    instructions_title = Text.from_markup("[bold cyan]Preparation[/]")
                    yield Static(instructions_title, id="instructions-title")
                    instructions_sections = [
                        Text.from_markup(
                            dedent(
                                """
                                [bold underline]Shortcuts[/]
                                • [bold magenta]F2[/] / [bold magenta]Ctrl+Shift+W[/] open the argument wizard
                                • [bold magenta]F3[/] / [bold magenta]Ctrl+Shift+T[/] choose a template
                                • [bold magenta]F4[/] / [bold magenta]Ctrl+Shift+H[/] view command history
                                • [bold magenta]Esc[/] / [bold magenta]Ctrl+C[/] exit this prompt
                                """
                            ).strip()
                        ),
                        Text.from_markup(
                            dedent(
                                """
                                [bold underline]Command entry[/]
                                • Enter a full `cactus-prepare` command and press Enter to submit it
                                • Type `[reverse]!N[/]` (for example `[reverse]!1[/]`) to load a history entry
                                • Type `[bold magenta]:wizard[/]` to open the argument wizard, or `[bold magenta]:template[/]` to open the template list
                                • The history window keeps the 20 most recent commands
                                """
                            ).strip()
                        ),
                    ]
                    for section in instructions_sections:
                        yield Static(section, classes="instructions-block")

            with Container(id="prepare-bottom"):
                command_title = Text.from_markup("[bold]Enter a cactus-prepare command[/]")
                yield Static(command_title, id="command-title")
                command_input = Input(placeholder="cactus-prepare …", id="command")
                self._command_input = command_input
                yield command_input
                status = Static("", id="status")
                self._status = status
                yield status
        yield Footer()

    def on_mount(self) -> None:  # type: ignore[override]
        self._refresh_history()
        if self._command_input:
            if self._history_entries:
                self._command_input.value = self._history_entries[0].command
            self._command_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:  # type: ignore[override]
        command = event.value.strip()
        if command.startswith("!"):
            if len(command) == 1:
                self._update_status("[red]Provide a history index, e.g. !1[/red]")
                return
            try:
                index = int(command[1:]) - 1
            except ValueError:
                self._update_status("[red]History index must be a number, e.g. !1[/red]")
                return
            if index < 0 or index >= len(self._history_entries):
                self._update_status("[red]History entry not found[/red]")
                return
            selected = self._history_entries[index].command
            if self._command_input:
                self._command_input.value = selected
            self._update_status(f"[green]Loaded history command #{index + 1}[/green]")
            return
        if not command:
            self._update_status("[red]Command cannot be empty[/red]")
            return
        if command == ":wizard":
            self.action_open_wizard()
            return
        if command == ":template":
            self.action_choose_template()
            return
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            self._update_status(f"[red]Failed to parse command: {exc}[/red]")
            return
        if not tokens:
            self._update_status("[red]Command cannot be empty[/red]")
            return
        executable = Path(tokens[0]).name
        if executable != "cactus-prepare":
            self._update_status("[red]Command must start with 'cactus-prepare'[/red]")
            return
        args = shlex.join(tokens[1:])
        self.exit(PromptResult(executable=tokens[0], args=args, action="submit"))

    def action_quit(self) -> None:
        value = self._command_input.value.strip() if self._command_input else ""
        self.exit(PromptResult(executable="", args=value, action="quit"))

    def action_open_wizard(self) -> None:
        defaults = self._suggest_defaults()
        self.push_screen(PrepareWizard(defaults), self._wizard_finished)

    def action_choose_template(self) -> None:
        if not self._templates:
            self._update_status("[yellow]No templates available yet. Save history or add custom templates first.[/yellow]")
            return
        self.push_screen(TemplateSelector(self._templates), self._template_chosen)

    def action_show_history(self) -> None:
        self._refresh_history()
        if not self._history_entries:
            self._update_status("[yellow]No history available yet[/yellow]")
            return
        self.push_screen(HistoryViewer(self._history_entries), self._history_selected)

    def _update_status(self, message: str) -> None:
        if self._status:
            self._status.update(message)

    def _refresh_history(self) -> None:
        self._history_entries = history.load_history()


    def _wizard_finished(self, result: str | None) -> None:
        if not result:
            self._update_status("[yellow]Wizard cancelled[/yellow]")
            return
        if self._command_input:
            self._command_input.value = result
        self._update_status("[green]Wizard generated a command. Press Enter to submit.[/green]")
        self._template_defaults = None

    def _template_chosen(self, template: templates.Template | None) -> None:
        if not template:
            self._update_status("[yellow]Template selection cancelled[/yellow]")
            return
        command = template.build_command()
        if self._command_input:
            self._command_input.value = command
        self._template_defaults = template.to_wizard_defaults()
        self._update_status(f"[green]Applied template: {template.name}[/green]")

    def _history_selected(self, command: str | None) -> None:
        if not command:
            self._update_status("[yellow]History window closed[/yellow]")
            return
        if self._command_input:
            self._command_input.value = command
            self._command_input.cursor_position = len(command)
        self._update_status("[green]History command loaded. Press Enter to submit.[/green]")

    def _suggest_defaults(self) -> dict[str, str]:
        defaults: dict[str, str] = {}
        if self._template_defaults:
            defaults.update(self._template_defaults)
        if self._command_input and self._command_input.value.strip():
            try:
                tokens = shlex.split(self._command_input.value.strip())
            except ValueError:
                tokens = []
            if tokens and Path(tokens[0]).name == "cactus-prepare":
                parsed = tokens[1:]
                defaults.update(_tokens_to_defaults(parsed))
        elif self._history_entries:
            command = self._history_entries[0].command
            try:
                tokens = shlex.split(command)
            except ValueError:
                tokens = []
            defaults.update(_tokens_to_defaults(tokens[1:]))
        return defaults


def prompt_prepare_command() -> PromptResult:
    """Launch the Textual prompt and return the user's command selection."""

    app = PrepareCommandPrompt()
    result = app.run()
    if isinstance(result, PromptResult):
        return result
    return PromptResult(executable="", args="", action="quit")


def _tokens_to_defaults(tokens: list[str]) -> dict[str, str]:
    """Helper that infers wizard defaults from an existing command."""

    defaults: dict[str, str] = {}
    if not tokens:
        return defaults
    # Spec is the first non-flag token
    for token in tokens:
        if not token.startswith("-"):
            defaults["spec"] = token
            break
    flag_map = {
        "--outDir": "out_dir",
        "--outSeqFile": "out_seq",
        "--outHal": "out_hal",
        "--jobStore": "job_store",
        "--jobstore": "job_store",
    }
    extra: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("--") and "=" in token:
            flag, value = token.split("=", 1)
            field = flag_map.get(flag)
            if field:
                defaults[field] = value
            else:
                extra.append(token)
            i += 1
            continue
        if token in flag_map:
            field = flag_map[token]
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                defaults[field] = tokens[i + 1]
                i += 2
                continue
            extra.append(token)
            i += 1
            continue
        if not token.startswith("-"):
            i += 1
            continue
        extra.append(token)
        i += 1
    if extra:
        defaults["extra"] = " ".join(extra)
    return defaults
