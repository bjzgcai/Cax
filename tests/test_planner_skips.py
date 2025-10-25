"""Tests covering planner behaviour for subtree replacements."""
from __future__ import annotations

from datetime import datetime

from cax import planner
from cax.models import Plan, PrepareHeader, Round, Step


def _make_step(raw: str, kind: str, root: str | None = None) -> Step:
    return Step(
        raw=raw,
        kind=kind,
        jobstore=None,
        out_files=[],
        root=root,
    )


def _make_round(
    name: str,
    root: str,
    target_hal: str,
    replace_with_ramax: bool,
) -> Round:
    blast = Step(
        raw=f"cactus-blast jobstore/1 seq.txt {root}.paf --root {root}",
        kind="blast",
        root=root,
    )
    align = Step(
        raw=f"cactus-align jobstore/2 seq.txt {root}.paf {root}.hal --root {root}",
        kind="align",
        root=root,
        out_files=[f"{root}.hal"],
    )
    return Round(
        name=name,
        root=root,
        target_hal=f"{target_hal}",
        blast_step=None if replace_with_ramax else blast,
        align_step=None if replace_with_ramax else align,
        replace_with_ramax=replace_with_ramax,
    )


def test_descendant_rounds_skipped_when_ancestor_replaced(tmp_path) -> None:
    out_seq_file = tmp_path / "tree.txt"
    out_seq_file.write_text("((A:0.1,B:0.2)N1:0.3,C:0.4)Root;\n", encoding="utf-8")

    header = PrepareHeader(
        generated_by="cactus-prepare example --outSeqFile tree.txt",
        date=datetime(2024, 1, 1, 0, 0, 0),
    )
    round_child = _make_round("Round child", "N1", "N1.hal", replace_with_ramax=False)
    round_root = _make_round("Round root", "Root", "Root.hal", replace_with_ramax=True)

    plan = Plan(
        header=header,
        preprocess=[],
        rounds=[round_child, round_root],
        hal_merges=[
            _make_step(
                "halAppendSubtree Root.hal N1.hal N1 N1 --merge",
                kind="halmerge",
                root="N1",
            ),
            _make_step(
                "halAppendSubtree Root.hal Root.hal Root Root --merge",
                kind="halmerge",
                root="Root",
            ),
        ],
        out_seq_file=str(out_seq_file),
    )

    commands = planner.build_execution_plan(plan, base_dir=tmp_path)

    # Expect the child round to be skipped entirely (no blast/align/ramax commands)
    categories = {cmd.category for cmd in commands}
    assert "blast" not in categories
    assert "align" not in categories

    # Hal merge for the child should also be skipped, root merge retained.
    merge_targets = [cmd.command[3] for cmd in commands if cmd.category == "halmerge"]
    assert merge_targets == ["Root"]
