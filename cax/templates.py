"""Template management for cactus-prepare argument presets."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shlex

TEMPLATE_FILE = Path.home() / ".cax" / "templates.json"
PACKAGE_EXAMPLE_DIR = Path(__file__).resolve().parent / "examples"
EXAMPLE_DIR = Path("examples")
EXAMPLE_DIRS = (PACKAGE_EXAMPLE_DIR, EXAMPLE_DIR)
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
    seen: set[str] = set()
    for base_dir in EXAMPLE_DIRS:
        if not base_dir.exists():
            continue
        for path in sorted(base_dir.glob("*.txt")):
            if path.name in seen:
                continue
            seen.add(path.name)
            template = _template_from_example(path)
            if template is not None:
                templates.append(template)
    return templates


def _template_from_example(path: Path) -> Template | None:
    try:
        with path.open(encoding="utf-8") as handle:
            first_line = next((line.strip() for line in handle if line.strip()), "")
    except OSError:
        return None
    if not _looks_like_newick(first_line):
        return None
    stem = path.stem
    default_out_dir_path = default_output_dir(stem)
    params = {
        "out_dir": str(default_out_dir_path),
        "out_seq": str(default_out_dir_path / f"{stem}.txt"),
        "out_hal": str(default_out_dir_path / f"{stem}.hal"),
        "job_store": "jobstore",
    }
    return Template(
        name=f"Example: {stem}",
        spec=str(path),
        params=params,
        source="builtin",
    )


def _looks_like_newick(line: str) -> bool:
    if not line:
        return False
    if line.endswith(";") and "(" in line:
        return True
    return False


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


def default_output_dir(stem: str | None = None) -> Path:
    base = Path.home() / ".cax" / "outputs"
    if stem:
        return base / stem
    return base
