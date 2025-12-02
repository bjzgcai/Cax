from cax import tree_utils
from cax.ui import AsciiPhylo
from cax.models import Round


def _build_tree():
    # Build a minimal tree: root -> child
    root_round = Round(name="root", root="Anc0", target_hal="a.hal", replace_with_ramax=True, ramax_opts=["--subtree-mode"])
    child_round = Round(name="child", root="Anc1", target_hal="b.hal", replace_with_ramax=True)
    root = tree_utils.AlignmentNode(name="Anc0", children=[])
    child = tree_utils.AlignmentNode(name="Anc1", children=[])
    root.children.append(child)
    child.parent = root
    root.round = root_round
    child.round = child_round
    return root, child


def test_bulk_revert_when_toggling_child():
    root, child = _build_tree()
    widget = AsciiPhylo(root)
    # Simulate ancestor subtree mode
    assert "--subtree-mode" in root.round.ramax_opts

    reverted = widget._maybe_revert_bulk(child)

    assert reverted is True
    assert root.round.replace_with_ramax is False
    assert "--subtree-mode" not in root.round.ramax_opts
    assert child.round.replace_with_ramax is True
