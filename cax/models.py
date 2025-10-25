"""Core data models for the Cactus-RaMAx workflow."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


StepKind = Literal[
    "preprocess",
    "blast",
    "align",
    "hal2fasta",
    "halmerge",
    "ramax",
    "other",
]


class PrepareHeader(BaseModel):
    """Metadata captured from the cactus-prepare output header."""

    generated_by: str
    date: datetime
    cactus_commit: Optional[str] = None

    @field_validator("generated_by")
    @classmethod
    def _strip_generated_by(cls, value: str) -> str:
        return value.strip()


class Step(BaseModel):
    """Represents a single command step from the cactus/RaMAx workflow."""

    raw: str
    kind: StepKind
    jobstore: Optional[str] = None
    out_files: list[str] = Field(default_factory=list)
    root: Optional[str] = None
    log_file: Optional[str] = None
    label: Optional[str] = None

    @field_validator("raw")
    @classmethod
    def _ensure_raw(cls, value: str) -> str:
        return value.strip()

    def short_label(self) -> str:
        """Return a concise label suitable for log names or UI display."""

        if self.label:
            return self.label
        if self.kind in {"blast", "align", "ramax"} and self.root:
            return f"{self.kind}-{self.root}"
        return self.raw.split()[0] if self.raw else "step"


class Round(BaseModel):
    """Encapsulates the data for a logical cactus alignment round."""

    name: str
    root: str
    target_hal: str
    blast_step: Optional[Step] = None
    align_step: Optional[Step] = None
    hal2fasta_steps: list[Step] = Field(default_factory=list)
    replace_with_ramax: bool = False
    workdir: Optional[str] = None
    ramax_opts: list[str] = Field(default_factory=list)
    manual_ramax_command: Optional[str] = None

    @model_validator(mode="after")
    def _validate_round(self) -> "Round":
        if not self.target_hal:
            raise ValueError("Round.target_hal cannot be empty")
        if (self.blast_step is None or self.align_step is None) and not self.replace_with_ramax:
            raise ValueError("blast_step and align_step required when not replacing with RaMAx")
        return self

class Plan(BaseModel):
    """Full execution plan assembled from the parsed cactus-prepare script."""

    header: PrepareHeader
    preprocess: list[Step] = Field(default_factory=list)
    rounds: list[Round] = Field(default_factory=list)
    hal_merges: list[Step] = Field(default_factory=list)
    out_seq_file: str
    out_dir: Optional[str] = None
    dry_run: bool = False
    global_ramax_opts: list[str] = Field(default_factory=list)

    @field_validator("out_seq_file")
    @classmethod
    def _validate_out_seq_file(cls, value: str) -> str:
        if not value:
            raise ValueError("out_seq_file cannot be empty")
        return value
