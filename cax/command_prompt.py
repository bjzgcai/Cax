"""Textual prompt for collecting cactus-prepare commands from the user."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
from typing import Literal

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, ListItem, ListView, Static

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

    def __init__(self, defaults: dict[str, str] | None = None) -> None:
        super().__init__()
        self._status: Static | None = None
        self._fields: dict[str, Input] = {}
        self._defaults = defaults or {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Fill out the fields below and press “Generate command” to return.", id="instructions")
        for field_id, label, placeholder in [
            ("spec", "Spec or plan file", "examples/evolverMammals.txt"),
            ("out_dir", "--outDir", "steps-output"),
            ("out_seq", "--outSeqFile", "steps-output/out.txt"),
            ("out_hal", "--outHal", "steps-output/out.hal"),
            ("job_store", "--jobStore", "jobstore"),
            ("extra", "Additional arguments", "--optionA foo --optionB"),
        ]:
            value = self._defaults.get(field_id, "")
            input_widget = Input(value=value, placeholder=placeholder, id=field_id)
            self._fields[field_id] = input_widget
            yield Static(label)
            yield input_widget
        self._status = Static("", id="status")
        yield Button("Generate command", id="submit", variant="success")
        yield Button("Cancel", id="cancel")
        yield self._status
        yield Footer()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:  # type: ignore[override]
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        if event.button.id != "submit":
            return
        spec = self._fields["spec"].value.strip()
        if not spec:
            self._set_status("[red]Spec or plan file cannot be empty[/red]")
            return
        spec_path = Path(spec)
        if not spec_path.exists():
            self._set_status("[red]Specified plan file does not exist[/red]")
            return
        command = ["cactus-prepare", spec]
        out_dir = self._fields["out_dir"].value.strip()
        if out_dir:
            command.extend(["--outDir", out_dir])
        out_seq = self._fields["out_seq"].value.strip()
        if out_seq:
            command.extend(["--outSeqFile", out_seq])
        out_hal = self._fields["out_hal"].value.strip()
        if out_hal:
            command.extend(["--outHal", out_hal])
        job_store = self._fields["job_store"].value.strip()
        if job_store:
            command.extend(["--jobStore", job_store])
        extra = self._fields["extra"].value.strip()
        if extra:
            command.extend(shlex.split(extra))
        self.dismiss(shlex.join(command))

    def _set_status(self, message: str) -> None:
        if self._status:
            self._status.update(message)


class TemplateSelector(Screen[templates.Template | None]):
    """Simple list view that lets the user pick a template."""

    BINDINGS = [
        Binding("escape", "cancel", "Back"),
    ]

    def __init__(self, template_list: list[templates.Template]) -> None:
        super().__init__()
        self._templates = template_list
        self._list_view: ListView | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        if not self._templates:
            yield Static("No templates available.", id="instructions")
        else:
            yield Static("Use the arrow keys to choose a template, then press Enter.", id="instructions")
            items: list[ListItem] = []
            for idx, template in enumerate(self._templates):
                description = f"{template.name}\n{template.spec}"
                item = ListItem(Static(description), id=f"template-{idx}")
                items.append(item)
            list_view = ListView(*items, id="template-list")
            self._list_view = list_view
            yield list_view
        yield Footer()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:  # type: ignore[override]
        if not event.item.id:
            return
        if not event.item.id.startswith("template-"):
            return
        try:
            index = int(event.item.id.split("-", 1)[1])
        except ValueError:
            return
        if index < 0 or index >= len(self._templates):
            return
        self.dismiss(self._templates[index])

    def on_mount(self) -> None:  # type: ignore[override]
        if self._list_view:
            self._list_view.index = 0


class PrepareCommandPrompt(App[PromptResult]):
    """Minimal Textual app that requests a cactus-prepare command."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #instructions {
        padding: 1 2;
    }
    #history {
        padding: 0 2;
        color: #b3b3b3;
    }
    #command {
        margin: 1 2;
    }
    #status {
        padding: 0 2 1 2;
    }
    """

    BINDINGS = [
        Binding("escape", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit"),
        Binding("f2", "open_wizard", "Wizard"),
        Binding("f3", "choose_template", "Templates"),
        Binding("ctrl+shift+w", "open_wizard", "Wizard"),
        Binding("ctrl+shift+t", "choose_template", "Templates"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._command_input: Input | None = None
        self._status: Static | None = None
        self._history_text: Static | None = None
        self._history_entries = history.load_history()
        self._templates = templates.load_templates()
        self._template_defaults: dict[str, str] | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        instructions = (
            "Enter a full cactus-prepare command and press Enter to submit. "
            "To reuse history, type `!N` (for example `!1`) and press Enter to load, then press Enter again to submit. "
            "Press F2 or Ctrl+Shift+W to open the argument wizard, F3 or Ctrl+Shift+T to pick a template, "
            "or type `:wizard` / `:template` directly in the prompt."
        )
        yield Static(instructions, id="instructions")
        history_widget = Static(self._render_history(), id="history")
        self._history_text = history_widget
        yield history_widget
        command_input = Input(placeholder="cactus-prepare …", id="command")
        self._command_input = command_input
        yield command_input
        status = Static("", id="status")
        self._status = status
        yield status
        yield Footer()

    def on_mount(self) -> None:  # type: ignore[override]
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

    def _update_status(self, message: str) -> None:
        if self._status:
            self._status.update(message)

    def _render_history(self) -> str:
        if not self._history_entries:
            return "No history yet."
        lines: list[str] = ["Recent history:"]
        for idx, entry in enumerate(self._history_entries[:5], start=1):
            lines.append(f"{idx}. {entry.command}")
        if len(self._history_entries) > 5:
            lines.append("…")
        return "\n".join(lines)

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
