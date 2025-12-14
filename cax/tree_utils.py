"""Utilities for parsing cactus alignment trees and mapping them to plan rounds."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from .models import Plan, Round


class NewickParseError(RuntimeError):
    """Raised when a Newick tree cannot be parsed correctly."""


@dataclass(eq=False)
class AlignmentNode:
    """Node within the cactus alignment tree."""

    name: str
    children: list["AlignmentNode"] = field(default_factory=list)
    round: Optional[Round] = None
    parent: Optional["AlignmentNode"] = field(default=None, repr=False)
    length: Optional[float] = None
    support: Optional[float] = None

    def walk(self) -> Iterator["AlignmentNode"]:
        """Yield this node and all descendants."""

        yield self
        for child in self.children:
            yield from child.walk()

    def iter_rounds(self) -> Iterator[Round]:
        """Iterate over all rounds contained within this subtree."""

        if self.round is not None:
            yield self.round
        for child in self.children:
            yield from child.iter_rounds()

    def has_round(self) -> bool:
        """Return ``True`` if this subtree contains at least one round."""

        if self.round is not None:
            return True
        return any(child.has_round() for child in self.children)


@dataclass
class AlignmentTree:
    """Full cactus alignment tree rooted at ``root``."""

    root: AlignmentNode
    nodes_by_name: dict[str, AlignmentNode] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.nodes_by_name = {
            node.name: node for node in self.root.walk() if node.name
        }

    def find(self, name: str) -> Optional[AlignmentNode]:
        """Return the node with the given ``name`` if present."""

        return self.nodes_by_name.get(name)

    def iter_rounds(self) -> Iterator[Round]:
        """Iterate over rounds contained in the tree."""

        return self.root.iter_rounds()


def build_alignment_tree(plan: Plan, base_dir: Optional[Path] = None) -> Optional[AlignmentTree]:
    """Construct an alignment tree from a parsed ``Plan``.

    Returns ``None`` when the underlying ``--outSeqFile`` is missing or lacks
    a valid Newick tree definition.
    """

    newick = _read_newick(plan, base_dir=base_dir)
    if not newick:
        return None
    parser = _NewickParser(newick)
    try:
        root = parser.parse()
    except NewickParseError:
        return None
    round_map = {round_entry.root: round_entry for round_entry in plan.rounds}
    _attach_rounds(root, round_map)
    _attach_orphans_to_root(root, round_map)
    return AlignmentTree(root)


def _read_newick(plan: Plan, base_dir: Optional[Path]) -> str | None:
    """Return the Newick string for *plan*.

    Preferred source: ``plan.out_seq_file``. If missing, fall back to the input
    file path found in the first cactus-preprocess step.
    """

    # Primary: out_seq_file
    path = _resolve_path(plan.out_seq_file, base_dir)
    newick = _read_first_nonempty_line(path)
    if newick:
        return newick

    # Fallback: try to infer input file from preprocess step
    for step in plan.preprocess:
        tokens = step.raw.split()
        candidates = _candidate_paths_from_tokens(tokens, base_dir)
        for candidate in candidates:
            newick = _read_first_nonempty_line(candidate)
            if newick:
                return newick
        break  # only need first preprocess step
    return None


def _candidate_paths_from_tokens(tokens: list[str], base_dir: Optional[Path]) -> list[Path]:
    paths: list[Path] = []
    for tok in tokens:
        if tok.startswith("-"):
            continue
        candidate = _resolve_path(tok, base_dir)
        if candidate.exists() and candidate.is_file():
            paths.append(candidate)
    return paths


def _resolve_path(path_like: str, base_dir: Optional[Path]) -> Path:
    path = Path(path_like).expanduser()
    if path.is_absolute():
        return path
    base = Path(base_dir) if base_dir else Path.cwd()
    return (base / path).resolve()


def _read_first_nonempty_line(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    return stripped
    except OSError:
        return None
    return None


def _attach_rounds(node: AlignmentNode, round_map: dict[str, Round]) -> None:
    if node.name in round_map:
        node.round = round_map[node.name]
    for child in node.children:
        child.parent = node
        _attach_rounds(child, round_map)


def _attach_orphans_to_root(root: AlignmentNode, round_map: dict[str, Round]) -> None:
    """Attach a single unmatched round to an unnamed root so it can be toggled in the UI.

    Some cactus-prepare outputs leave the outermost Newick node unnamed, while the last
    round (e.g. Anc0) targets that implied root. If exactly one round remains unattached
    and the parsed root currently lacks a round, bind it there to keep ancestor/descendant
    logic intact.
    """

    attached_roots: set[str] = set()

    def _collect(node: AlignmentNode) -> None:
        if node.round:
            attached_roots.add(node.round.root)
        for child in node.children:
            _collect(child)

    _collect(root)
    unmatched = [rnd for rnd in round_map.values() if rnd.root not in attached_roots]
    if len(unmatched) == 1 and root.round is None and (not root.name):
        root.round = unmatched.pop()

    if unmatched:
        existing_names = {child.name for child in root.children if child.name}
        for rnd in unmatched:
            if rnd.root in existing_names:
                continue
            child = AlignmentNode(name=rnd.root, children=[], round=rnd, parent=root)
            root.children.append(child)


class _NewickParser:
    """Minimal recursive-descent parser for Newick tree strings."""

    def __init__(self, text: str):
        self.text = text.strip()
        self.length = len(self.text)
        self.index = 0

    def parse(self) -> AlignmentNode:
        node = self._parse_subtree()
        self._skip_ws()
        if self._peek() == ";":
            self.index += 1
        self._skip_ws()
        if self.index != self.length:
            raise NewickParseError(f"Unexpected trailing data at position {self.index}")
        return node

    def _parse_subtree(self) -> AlignmentNode:
        self._skip_ws()
        if self._peek() == "(":
            self.index += 1
            children: list[AlignmentNode] = []
            while True:
                children.append(self._parse_subtree())
                self._skip_ws()
                token = self._peek()
                if token == ",":
                    self.index += 1
                    continue
                if token == ")":
                    self.index += 1
                    break
                raise NewickParseError(f"Expected ',' or ')' at position {self.index}")
            label = self._parse_label()
            length = self._parse_branch_length_value()
            name, support = self._split_name_support(label, internal=True)
            node = AlignmentNode(name=name or "", children=children, length=length, support=support)
            for child in children:
                child.parent = node
            return node

        label = self._parse_label()
        if not label:
            raise NewickParseError(f"Missing leaf label at position {self.index}")
        length = self._parse_branch_length_value()
        name, _ = self._split_name_support(label, internal=False)
        return AlignmentNode(name=name, length=length)

    def _parse_label(self) -> str:
        self._skip_ws()
        start = self.index
        while self.index < self.length:
            char = self.text[self.index]
            if char in ":,();":
                break
            if char.isspace():
                break
            self.index += 1
        label = self.text[start:self.index].strip()
        self._skip_ws()
        return label

    def _parse_branch_length_value(self) -> Optional[float]:
        self._skip_ws()
        if self._peek() != ":":
            return None
        self.index += 1
        start = self.index
        while self.index < self.length and self.text[self.index] not in ",(); \t\r\n":
            self.index += 1
        token = self.text[start:self.index].strip()
        self._skip_ws()
        if not token:
            return None
        try:
            return float(token)
        except ValueError:
            return None

    def _split_name_support(self, label: str, internal: bool) -> tuple[str, Optional[float]]:
        text = (label or "").strip()
        if internal and text and all(ch.isdigit() or ch == "." for ch in text):
            try:
                return "", float(text)
            except ValueError:
                return "", None
        return text, None

    def _skip_ws(self) -> None:
        while self.index < self.length and self.text[self.index].isspace():
            self.index += 1

    def _peek(self) -> str | None:
        if self.index >= self.length:
            return None
        return self.text[self.index]
