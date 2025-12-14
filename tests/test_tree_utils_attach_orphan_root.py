from datetime import datetime
from pathlib import Path

from cax import parser, tree_utils
from cax.models import Plan, PrepareHeader, Round, Step


def _round(root: str) -> Round:
    blast = Step(raw=f"cactus-blast jobstore/1 seq.fa {root}.paf --root {root}", kind="blast", out_files=[f"{root}.paf"], root=root)
    align = Step(raw=f"cactus-align jobstore/2 seq.fa {root}.paf {root}.hal --root {root}", kind="align", out_files=[f"{root}.hal"], root=root)
    return Round(name=root, root=root, target_hal=f"{root}.hal", blast_step=blast, align_step=align)


def _plan(tmp_path: Path) -> Plan:
    tree_path = tmp_path / "seq.fa"
    tree_path.write_text("(a,(b,c)cb) ;", encoding="utf-8")
    header = PrepareHeader(generated_by="cactus-prepare --outSeqFile seq.fa", date=datetime.now())
    return Plan(
        header=header,
        preprocess=[],
        rounds=[_round("cb"), _round("Anc0")],
        hal_merges=[],
        out_seq_file=str(tree_path),
    )


def test_orphan_round_attached_to_unnamed_root(tmp_path: Path):
    plan = _plan(tmp_path)
    tree = tree_utils.build_alignment_tree(plan, base_dir=tmp_path)
    assert tree is not None
    # unnamed root should carry the unmatched round
    assert tree.root.round is not None
    assert tree.root.round.root == "Anc0"
    # named child still attached correctly
    child = tree.find("cb")
    assert child and child.round and child.round.root == "cb"


def test_multiple_orphans_added_as_children(tmp_path: Path):
    tree_path = tmp_path / "seq.fa"
    tree_path.write_text("(a,b);", encoding="utf-8")
    header = PrepareHeader(generated_by="cactus-prepare --outSeqFile seq.fa", date=datetime.now())
    plan = Plan(
        header=header,
        preprocess=[],
        rounds=[_round("Anc0"), _round("Anc1")],
        hal_merges=[],
        out_seq_file=str(tree_path),
    )
    tree = tree_utils.build_alignment_tree(plan, base_dir=tmp_path)
    assert tree is not None
    names = {child.name for child in tree.root.children}
    assert {"Anc0", "Anc1"}.issubset(names)
