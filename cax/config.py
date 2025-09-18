"""YAML configuration helpers for persisting CAX plans."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import Plan


def plan_to_dict(plan: Plan) -> dict[str, Any]:
    """Convert a :class:`Plan` into a YAML-friendly dictionary."""

    return plan.model_dump(mode="json", exclude_none=True)


def save_plan(plan: Plan, path: Path) -> None:
    """Persist *plan* to *path* as YAML."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(plan_to_dict(plan), handle, sort_keys=False)


def load_plan(path: Path) -> Plan:
    """Load a :class:`Plan` from YAML stored at *path*."""

    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if data is None:
        raise ValueError(f"Empty YAML in {path}")
    return Plan.model_validate(data)


def plan_from_yaml(yaml_text: str) -> Plan:
    """Load a plan from a YAML string payload."""

    data = yaml.safe_load(yaml_text)
    if data is None:
        raise ValueError("Empty YAML payload")
    return Plan.model_validate(data)


def plan_to_yaml(plan: Plan) -> str:
    """Serialize *plan* to a YAML string."""

    return yaml.safe_dump(plan_to_dict(plan), sort_keys=False)
