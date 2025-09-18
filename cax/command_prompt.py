"""Textual prompt for collecting cactus-prepare commands from the user."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
from typing import Literal

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, Static


@dataclass
class PromptResult:
    """Result returned from the command prompt."""

    executable: str
    args: str
    action: Literal["submit", "quit"]


class PrepareCommandPrompt(App[PromptResult]):
    """Minimal Textual app that requests a cactus-prepare command."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #instructions {
        padding: 1 2;
    }
    #command {
        margin: 1 2;
    }
    #status {
        padding: 0 2 1 2;
    }
    """

    BINDINGS = [
        Binding("escape", "quit", "退出"),
        Binding("ctrl+c", "quit", "退出"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._command_input: Input | None = None
        self._status: Static | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("请输入完整的 cactus-prepare 指令，按 Enter 提交。", id="instructions")
        command_input = Input(placeholder="cactus-prepare ...", id="command")
        self._command_input = command_input
        yield command_input
        status = Static("", id="status")
        self._status = status
        yield status
        yield Footer()

    def on_mount(self) -> None:  # type: ignore[override]
        if self._command_input:
            self._command_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:  # type: ignore[override]
        command = event.value.strip()
        if not command:
            self._update_status("[red]命令不能为空[/red]")
            return
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            self._update_status(f"[red]命令解析失败：{exc}[/red]")
            return
        if not tokens:
            self._update_status("[red]命令不能为空[/red]")
            return
        executable = Path(tokens[0]).name
        if executable != "cactus-prepare":
            self._update_status("[red]请以 'cactus-prepare' 开头[/red]")
            return
        args = shlex.join(tokens[1:])
        self.exit(PromptResult(executable=tokens[0], args=args, action="submit"))

    def action_quit(self) -> None:
        value = self._command_input.value.strip() if self._command_input else ""
        self.exit(PromptResult(executable="", args=value, action="quit"))

    def _update_status(self, message: str) -> None:
        if self._status:
            self._status.update(message)


def prompt_prepare_command() -> PromptResult:
    """Launch the Textual prompt and return the user's command selection."""

    app = PrepareCommandPrompt()
    result = app.run()
    if isinstance(result, PromptResult):
        return result
    return PromptResult(executable="", args="", action="quit")
