"""Template management for cactus-prepare argument presets."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shlex

TEMPLATE_FILE = Path("steps-output/cax_templates.json")
EXAMPLE_DIR = Path("examples")
FLAG_MAP = {
    "out_dir": "--outDir",
    "out_seq": "--outSeqFile",
    "out_hal": "--outHal",
    "job_store": "--jobStore",
    "extra": None,
}


@dataclass
class Template:
    """A reusable cactus-prepare template."""

    name: str
    spec: str
    params: dict[str, str]
    source: str = "builtin"

    def to_wizard_defaults(self) -> dict[str, str]:
        defaults = {"spec": self.spec}
        defaults.update(self.params)
        return defaults

    def build_command(self, executable: str = "cactus-prepare") -> str:
        tokens: list[str] = [executable, self.spec]
        for key, flag in FLAG_MAP.items():
            value = self.params.get(key, "").strip()
            if not value:
                continue
            if key == "extra":
                tokens.extend(shlex.split(value))
                continue
            tokens.append(flag)
            tokens.append(value)
        return shlex.join(tokens)


def load_templates() -> list[Template]:
    templates: list[Template] = []
    templates.extend(_load_builtin_templates())
    templates.extend(_load_user_templates())
    return templates


def _load_builtin_templates() -> list[Template]:
    templates: list[Template] = []
    if not EXAMPLE_DIR.exists():
        return templates
    for path in sorted(EXAMPLE_DIR.glob("*.txt")):
        stem = path.stem
        params = {
            "out_dir": "steps-output",
            "out_seq": f"steps-output/{stem}.txt",
            "out_hal": f"steps-output/{stem}.hal",
            "job_store": "jobstore",
        }
        templates.append(
            Template(
                name=f"Example: {stem}",
                spec=str(path),
                params=params,
                source="builtin",
            )
        )
    return templates


def _load_user_templates() -> list[Template]:
    try:
        data = json.loads(TEMPLATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    templates: list[Template] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        spec = str(item.get("spec") or "").strip()
        params = item.get("params") or {}
        if not name or not spec or not isinstance(params, dict):
            continue
        clean_params = {k: str(v) for k, v in params.items() if k in FLAG_MAP}
        templates.append(Template(name=name, spec=spec, params=clean_params, source="user"))
    return templates
