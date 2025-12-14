from datetime import datetime
from pathlib import Path

from cax import planner
from cax.models import Plan, PrepareHeader, Round, Step


def _round(root: str) -> Round:
    blast = Step(raw=f"blast {root}", kind="blast", out_files=[f"{root}.paf"], root=root)
    align = Step(raw=f"align {root}", kind="align", out_files=[f"{root}.hal"], root=root)
    return Round(name=root, root=root, target_hal=f"{root}.hal", blast_step=blast, align_step=align)


def _plan(tmp_path: Path) -> Plan:
    tree_path = tmp_path / "tree.nwk"
    tree_path.write_text("((a,b)Anc1)Anc0;", encoding="utf-8")
    header = PrepareHeader(generated_by="cactus-prepare --outSeqFile tree.nwk", date=datetime.now())
    root_round = Round(
        name="Anc0",
        root="Anc0",
        target_hal="Anc0.hal",
        replace_with_ramax=True,
        ramax_opts=["--subtree-mode"],
    )
    child_round = _round("Anc1")
    return Plan(
        header=header,
        preprocess=[],
        rounds=[root_round, child_round],
        hal_merges=[],
        out_seq_file=str(tree_path),
        out_dir=str(tmp_path),
    )


def test_subtree_mode_skips_descendant_rounds(tmp_path: Path):
    plan = _plan(tmp_path)

    commands = planner.build_execution_plan(plan, base_dir=tmp_path)

    # Only the ancestor RaMAx command should remain; descendant rounds are absorbed.
    assert any(cmd.is_ramax and cmd.round_name == "Anc0" for cmd in commands)
    assert not any(cmd.round_name == "Anc1" for cmd in commands)
