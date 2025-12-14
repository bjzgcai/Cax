from datetime import datetime
from pathlib import Path

from cax import planner
from cax.models import Plan, PrepareHeader, Round


def test_subtree_flag_not_passed_to_ramax(tmp_path: Path):
    header = PrepareHeader(
        generated_by="cactus-prepare --outSeqFile seq.fa",
        date=datetime.now(),
        cactus_commit=None,
    )
    round_entry = Round(
        name="r1",
        root="Anc0",
        target_hal="out.hal",
        replace_with_ramax=True,
        ramax_opts=["--subtree-mode", "--threads", "4"],
    )
    plan = Plan(
        header=header,
        preprocess=[],
        rounds=[round_entry],
        hal_merges=[],
        out_seq_file="seq.fa",
        out_dir=str(tmp_path),
    )

    commands = planner.build_execution_plan(plan, base_dir=tmp_path)
    ramax_cmd = next(cmd for cmd in commands if cmd.is_ramax)

    assert "--subtree-mode" not in ramax_cmd.command
    assert "--threads" in ramax_cmd.command
