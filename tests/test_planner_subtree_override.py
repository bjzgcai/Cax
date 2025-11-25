from pathlib import Path

from cax import parser, planner


def _build_plan(tmp_path: Path):
    text = Path("examples/cactus-prepare_example.txt").read_text(encoding="utf-8")
    plan = parser.parse_prepare_script(text)

    # Build a compact Newick tree that includes every round root so ancestry can be resolved.
    tree_path = tmp_path / "toy_tree.nwk"
    tree_path.write_text("(((a,b)Anc2,c)mr,(d,e)Anc1)Anc0;", encoding="utf-8")
    plan.out_seq_file = str(tree_path)
    return plan


def test_descendant_cactus_runs_when_ancestor_ramax(tmp_path):
    plan = _build_plan(tmp_path)

    # Mark the whole tree as RaMAx, then switch the mr subtree back to cactus to simulate user override.
    for round_entry in plan.rounds:
        round_entry.replace_with_ramax = True
    mr_round = next(r for r in plan.rounds if r.root == "mr")
    mr_round.replace_with_ramax = False

    commands = planner.build_execution_plan(plan, base_dir=tmp_path)

    mr_cmds = [cmd for cmd in commands if cmd.round_name == mr_round.name]
    assert mr_cmds, "mr subtree should not be skipped just because an ancestor uses RaMAx"
    assert {"blast", "align"}.issubset({cmd.category for cmd in mr_cmds})

    anc0_round = next(r for r in plan.rounds if r.root == "Anc0")
    anc0_cmds = [cmd for cmd in commands if cmd.round_name == anc0_round.name]
    assert any(cmd.is_ramax for cmd in anc0_cmds), "root round should still run with RaMAx"


def test_descendant_ramax_skipped_when_ancestor_ramax(tmp_path):
    plan = _build_plan(tmp_path)

    # When Anc0 uses RaMAx and child Anc1 also requests RaMAx, the child should be skipped to avoid duplication.
    anc0 = next(r for r in plan.rounds if r.root == "Anc0")
    anc1 = next(r for r in plan.rounds if r.root == "Anc1")
    anc0.replace_with_ramax = True
    anc1.replace_with_ramax = True

    commands = planner.build_execution_plan(plan, base_dir=tmp_path)

    anc1_cmds = [cmd for cmd in commands if cmd.round_name == anc1.name]
    assert not anc1_cmds, "child RaMAx should be skipped when its ancestor already runs RaMAx"
