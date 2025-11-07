"""Simple history helper for cactus-prepare commands."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable, List

HISTORY_FILE = Path.home() / ".cax" / "history.json"
HISTORY_LIMIT = 20


@dataclass
class HistoryEntry:
    """Represents a stored history command."""

    command: str


def load_history() -> list[HistoryEntry]:
    """Load history entries, newest command first."""

    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        entries: list[HistoryEntry] = []
        for item in data:
            if isinstance(item, str) and item.strip():
                entries.append(HistoryEntry(command=item))
        return entries
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError):
        return []


def save_history(commands: Iterable[str]) -> None:
    """Persist the provided command sequence to disk."""

    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    normalized = [cmd for cmd in commands if cmd.strip()]
    HISTORY_FILE.write_text(json.dumps(normalized[:HISTORY_LIMIT], ensure_ascii=False, indent=2), encoding="utf-8")


def add_command(command: str) -> None:
    """Insert a command at the front of history with dedupe and trimming."""

    command = command.strip()
    if not command:
        return
    entries = load_history()
    merged: list[str] = [command]
    for entry in entries:
        if entry.command != command:
            merged.append(entry.command)
    save_history(merged)


def delete_entry(index: int) -> bool:
    """Delete a history entry by its zero-based index."""

    if index < 0:
        return False
    entries = load_history()
    if index >= len(entries):
        return False
    del entries[index]
    save_history(entry.command for entry in entries)
    return True
