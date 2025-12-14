from datetime import datetime
from pathlib import Path

from cax import tree_utils
from cax.models import Plan, PrepareHeader, Round, Step


def _round(root: str) -> Round:
    blast = Step(raw=f"cactus-blast jobstore/1 seq.fa {root}.paf --root {root}", kind="blast", out_files=[f"{root}.paf"], root=root)
    align = Step(raw=f"cactus-align jobstore/2 seq.fa {root}.paf {root}.hal --root {root}", kind="align", out_files=[f"{root}.hal"], root=root)
    return Round(name=root, root=root, target_hal=f"{root}.hal", blast_step=blast, align_step=align)


def test_build_tree_uses_preprocess_input_when_outseq_missing(tmp_path: Path):
    # Write only the input file (with Newick) and point out_seq_file to a non-existent path.
    input_path = tmp_path / "input.txt"
    input_path.write_text("(a,b)c;", encoding="utf-8")

    header = PrepareHeader(generated_by="cactus-prepare --outSeqFile missing.txt", date=datetime.now())
    preprocess = Step(
        raw=f"cactus-preprocess jobstore/0 {input_path} missing.txt --logFile preprocess.log",
        kind="preprocess",
        out_files=["missing.txt"],
    )
    plan = Plan(
        header=header,
        preprocess=[preprocess],
        rounds=[_round("c")],
        hal_merges=[],
        out_seq_file=str(tmp_path / "missing.txt"),
    )

    tree = tree_utils.build_alignment_tree(plan, base_dir=tmp_path)

    assert tree is not None
    assert tree.root.name == "c"
    assert tree.root.round and tree.root.round.root == "c"
