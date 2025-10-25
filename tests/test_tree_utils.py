"""Tests for the alignment tree utilities."""
from __future__ import annotations

from datetime import datetime

from cax import tree_utils
from cax.models import Plan, PrepareHeader, Round, Step


def _make_round(name: str) -> Round:
    blast = Step(
        raw=f"cactus-blast jobstore/1 input.txt output_{name}.paf --root {name}",
        kind="blast",
        root=name,
        log_file=f"logs/blast-{name}.log",
    )
    align = Step(
        raw=f"cactus-align jobstore/2 input.txt output_{name}.paf {name}.hal --root {name}",
        kind="align",
        root=name,
        out_files=[f"{name}.hal"],
        log_file=f"logs/align-{name}.log",
    )
    return Round(
        name=name,
        root=name,
        target_hal=f"{name}.hal",
        blast_step=blast,
        align_step=align,
    )


def _make_plan(out_seq_path: str) -> Plan:
    header = PrepareHeader(
        generated_by="cactus-prepare tests/tree.txt --outSeqFile tree.txt",
        date=datetime(2024, 1, 1, 0, 0, 0),
    )
    rounds = [
        _make_round("N1"),
        _make_round("N2"),
        _make_round("Root"),
    ]
    return Plan(
        header=header,
        preprocess=[],
        rounds=rounds,
        hal_merges=[],
        out_seq_file=out_seq_path,
    )


def test_build_alignment_tree(tmp_path) -> None:
    out_seq_file = tmp_path / "tree.txt"
    out_seq_file.write_text(
        "((A:0.1,B:0.2)N1:0.3,(C:0.4,D:0.5)N2:0.6)Root;\n",
        encoding="utf-8",
    )
    plan = _make_plan(str(out_seq_file))

    tree = tree_utils.build_alignment_tree(plan)
    assert tree is not None
    assert tree.root.name == "Root"
    assert tree.root.round is plan.rounds[2]

    child_names = sorted(child.name for child in tree.root.children)
    assert child_names == ["N1", "N2"]

    node_n1 = tree.find("N1")
    assert node_n1 is not None
    assert node_n1.round is plan.rounds[0]
    leaf_names = sorted(child.name for child in node_n1.children)
    assert leaf_names == ["A", "B"]
    assert all(child.round is None for child in node_n1.children)

    node_n2 = tree.find("N2")
    assert node_n2 is not None
    assert node_n2.round is plan.rounds[1]

    all_rounds = list(tree.iter_rounds())
    assert len(all_rounds) == 3
    assert sorted(round_entry.root for round_entry in all_rounds) == sorted(
        round_entry.root for round_entry in plan.rounds
    )
