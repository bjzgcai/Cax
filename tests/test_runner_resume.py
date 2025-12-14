import os
import stat
from datetime import datetime
from pathlib import Path

import pytest

from cax.models import Plan, PrepareHeader, Round, RunSettings, Step
from cax.runner import PlanRunner
from cax.resume import preview_resume


def _build_plan(tmp_path: Path) -> Plan:
    header = PrepareHeader(
        generated_by="cactus-prepare --outSeqFile seq.fa --outDir out",
        date=datetime.now(),
    )
    step1 = Step(
        raw=(
            "python -c \"from pathlib import Path; p=Path('count.txt'); "
            "n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1))\""
        ),
        kind="blast",
        out_files=["count.txt"],
        root="root1",
    )
    step2 = Step(
        raw="python -c \"from pathlib import Path; Path('marker.txt').write_text('ok')\"",
        kind="align",
        out_files=["marker.txt"],
        root="root1",
    )
    round1 = Round(
        name="Round 1",
        root="root1",
        target_hal="target.hal",
        blast_step=step1,
        align_step=step2,
    )
    return Plan(
        header=header,
        preprocess=[],
        rounds=[round1],
        hal_merges=[],
        out_seq_file=str(tmp_path / "seq.fa"),
        out_dir=str(tmp_path),
    )


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_resume_skips_completed_steps(tmp_path):
    plan = _build_plan(tmp_path)
    runner = PlanRunner(plan, base_dir=tmp_path, run_settings=RunSettings(verbose=False, resume=True))

    runner.run()

    count_path = tmp_path / "count.txt"
    marker_path = tmp_path / "marker.txt"

    assert count_path.exists()
    assert marker_path.exists()
    assert count_path.read_text() == "1"

    # 删除第二步产物以验证续跑会重做缺失步骤
    marker_path.unlink()

    runner = PlanRunner(plan, base_dir=tmp_path, run_settings=RunSettings(verbose=False, resume=True))
    runner.run()

    assert count_path.read_text() == "1"  # 第一步被跳过
    assert marker_path.exists()  # 第二步因产物缺失被重跑
    assert (tmp_path / "logs" / "run_state.json").exists()


def test_preview_resume_reports_completed_and_pending(tmp_path):
    plan = _build_plan(tmp_path)
    runner = PlanRunner(plan, base_dir=tmp_path, run_settings=RunSettings(verbose=False, resume=True))

    runner.run()

    # 删除第二步产物以制造“缺失产物需重跑”场景
    marker_path = tmp_path / "marker.txt"
    marker_path.unlink()

    preview = preview_resume(plan, base_dir=tmp_path)

    assert preview is not None
    assert preview.plan_matches is True
    assert "blast-root1" in preview.completed
    assert "align-root1" in preview.pending
    assert "align-root1" in preview.missing_outputs


def test_preview_resume_detects_signature_mismatch(tmp_path):
    plan = _build_plan(tmp_path)
    runner = PlanRunner(plan, base_dir=tmp_path, run_settings=RunSettings(verbose=False, resume=True))
    runner.run()

    preview = preview_resume(plan, base_dir=tmp_path, thread_count=8)

    assert preview is not None
    assert preview.plan_matches is False


def test_resume_adds_restart_for_existing_toil_jobstore(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "cactus-blast",
        """#!/usr/bin/env python3
import sys
from pathlib import Path

jobstore = Path(sys.argv[1])
jobstore.mkdir(parents=True, exist_ok=True)
(jobstore / "files" / "shared").mkdir(parents=True, exist_ok=True)
(jobstore / "files" / "shared" / "rootJobStoreID").write_text("ok")

args = " ".join(sys.argv[1:])
Path("seen-args.txt").write_text(args)

if "--restart" not in sys.argv:
    sys.exit(1)

out = next((a for a in sys.argv[1:] if a.endswith(".paf")), None)
if out:
    Path(out).write_text("ok")
sys.exit(0)
""",
    )
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}

    header = PrepareHeader(generated_by="cactus-prepare --outSeqFile seq.fa --outDir out", date=datetime.now())
    step = Step(
        raw="cactus-blast jobstore/0 seq.txt out.paf --root r",
        kind="preprocess",
        jobstore="jobstore/0",
        out_files=["out.paf"],
    )
    plan = Plan(
        header=header,
        preprocess=[step],
        rounds=[],
        hal_merges=[],
        out_seq_file=str(tmp_path / "seq.fa"),
        out_dir=str(tmp_path),
    )

    runner = PlanRunner(plan, base_dir=tmp_path, env=env, run_settings=RunSettings(verbose=False, resume=True))
    with pytest.raises(RuntimeError):
        runner.run()

    assert (tmp_path / "jobstore" / "0").exists()

    runner = PlanRunner(plan, base_dir=tmp_path, env=env, run_settings=RunSettings(verbose=False, resume=True))
    runner.run()

    assert (tmp_path / "out.paf").exists()
    assert "--restart" in (tmp_path / "seen-args.txt").read_text()


def test_resume_cleans_toil_jobstore_when_forced_to_rerun(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "cactus-blast",
        """#!/usr/bin/env python3
import sys
from pathlib import Path

jobstore = Path(sys.argv[1])
if jobstore.exists():
    # Runner should have cleaned it when rerunning a previously-successful step.
    sys.exit(2)
if "--restart" in sys.argv:
    sys.exit(3)

jobstore.mkdir(parents=True, exist_ok=True)
out = next((a for a in sys.argv[1:] if a.endswith(".paf")), None)
if out:
    Path(out).write_text("ok")

counter = Path("blast-count.txt")
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
sys.exit(0)
""",
    )
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}

    header = PrepareHeader(generated_by="cactus-prepare --outSeqFile seq.fa --outDir out", date=datetime.now())
    step1 = Step(
        raw="python -c \"from pathlib import Path; Path('a.txt').write_text('ok')\"",
        kind="preprocess",
        out_files=["a.txt"],
        label="make-a",
    )
    step2 = Step(
        raw="cactus-blast jobstore/0 seq.txt out.paf --root r",
        kind="preprocess",
        jobstore="jobstore/0",
        out_files=["out.paf"],
        label="blast",
    )
    plan = Plan(
        header=header,
        preprocess=[step1, step2],
        rounds=[],
        hal_merges=[],
        out_seq_file=str(tmp_path / "seq.fa"),
        out_dir=str(tmp_path),
    )

    runner = PlanRunner(plan, base_dir=tmp_path, env=env, run_settings=RunSettings(verbose=False, resume=True))
    runner.run()
    assert (tmp_path / "jobstore" / "0").exists()
    assert (tmp_path / "blast-count.txt").read_text() == "1"

    # 触发断点回退：让第 1 步需要重跑，从而第 2 步也必须重跑（并清理 jobStore）。
    (tmp_path / "a.txt").unlink()

    runner = PlanRunner(plan, base_dir=tmp_path, env=env, run_settings=RunSettings(verbose=False, resume=True))
    runner.run()

    assert (tmp_path / "blast-count.txt").read_text() == "2"


def test_resume_cleans_corrupt_jobstore_instead_of_restart(tmp_path: Path):
    """jobStore 存在但缺少 rootJobStoreID 时，--restart 会报错，应当清理后重跑。"""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "cactus-blast",
        """#!/usr/bin/env python3
import sys
from pathlib import Path

jobstore = Path(sys.argv[1])

# 若 runner 未在启动前清理，直接失败。
if jobstore.exists():
    sys.exit(2)
if "--restart" in sys.argv:
    sys.exit(3)

marker = Path("first-run.txt")
if not marker.exists():
    # 第一次：制造一个“看似存在但不完整”的 jobStore（缺少 rootJobStoreID），并失败
    (jobstore / "files" / "shared").mkdir(parents=True, exist_ok=True)
    (jobstore / "files" / "shared" / "config.pickle").write_text("x")
    marker.write_text("1")
    sys.exit(1)

out = next((a for a in sys.argv[1:] if a.endswith(".paf")), None)
if out:
    Path(out).write_text("ok")
sys.exit(0)
""",
    )
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}

    header = PrepareHeader(generated_by="cactus-prepare --outSeqFile seq.fa --outDir out", date=datetime.now())
    step = Step(
        raw="cactus-blast jobstore/0 seq.txt out.paf --root r",
        kind="preprocess",
        jobstore="jobstore/0",
        out_files=["out.paf"],
        label="blast",
    )
    plan = Plan(
        header=header,
        preprocess=[step],
        rounds=[],
        hal_merges=[],
        out_seq_file=str(tmp_path / "seq.fa"),
        out_dir=str(tmp_path),
    )

    runner = PlanRunner(plan, base_dir=tmp_path, env=env, run_settings=RunSettings(verbose=False, resume=True))
    with pytest.raises(RuntimeError):
        runner.run()
    assert (tmp_path / "jobstore" / "0").exists()

    runner = PlanRunner(plan, base_dir=tmp_path, env=env, run_settings=RunSettings(verbose=False, resume=True))
    runner.run()
    assert (tmp_path / "out.paf").exists()


def test_resume_only_skips_prefix_steps(tmp_path: Path):
    """验证续跑只跳过前缀：一旦前置步骤需重跑，后续即使成功也要重跑。"""

    header = PrepareHeader(generated_by="cactus-prepare --outSeqFile seq.fa --outDir out", date=datetime.now())
    step1 = Step(
        raw="python -c \"from pathlib import Path; Path('a.txt').write_text('ok')\"",
        kind="preprocess",
        out_files=["a.txt"],
        label="make-a",
    )
    step2 = Step(
        raw=(
            "python -c \"from pathlib import Path; p=Path('b.txt'); "
            "n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1))\""
        ),
        kind="preprocess",
        out_files=["b.txt"],
        label="bump-b",
    )
    plan = Plan(
        header=header,
        preprocess=[step1, step2],
        rounds=[],
        hal_merges=[],
        out_seq_file=str(tmp_path / "seq.fa"),
        out_dir=str(tmp_path),
    )

    runner = PlanRunner(plan, base_dir=tmp_path, run_settings=RunSettings(verbose=False, resume=True))
    runner.run()
    assert (tmp_path / "a.txt").exists()
    assert (tmp_path / "b.txt").read_text() == "1"

    # 删除第一步产物，触发断点回退：后续步骤即便已成功也不应被跳过。
    (tmp_path / "a.txt").unlink()

    preview = preview_resume(plan, base_dir=tmp_path)
    assert preview is not None
    assert "make-a" in preview.pending
    assert "bump-b" in preview.pending
    assert "bump-b" not in preview.completed

    runner = PlanRunner(plan, base_dir=tmp_path, run_settings=RunSettings(verbose=False, resume=True))
    runner.run()
    assert (tmp_path / "a.txt").exists()
    assert (tmp_path / "b.txt").read_text() == "2"


def test_resume_cleans_failed_toil_jobstore_when_not_first_step(tmp_path: Path):
    """当失败的 Toil 步骤不是本次重跑的第一个步骤时，应清理 jobStore 而不是 --restart。"""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "cactus-blast",
        """#!/usr/bin/env python3
import sys
from pathlib import Path

jobstore = Path(sys.argv[1])
Path("seen-args.txt").write_text(" ".join(sys.argv[1:]))

marker = Path("first-run.txt")
if not marker.exists():
    (jobstore / "files" / "shared").mkdir(parents=True, exist_ok=True)
    (jobstore / "files" / "shared" / "rootJobStoreID").write_text("ok")
    marker.write_text("1")
    sys.exit(1)

if jobstore.exists():
    sys.exit(2)
if "--restart" in sys.argv:
    sys.exit(3)

(jobstore / "files" / "shared").mkdir(parents=True, exist_ok=True)
(jobstore / "files" / "shared" / "rootJobStoreID").write_text("ok")

out = next((a for a in sys.argv[1:] if a.endswith(".paf")), None)
if out:
    Path(out).write_text("ok")
sys.exit(0)
""",
    )
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}

    header = PrepareHeader(generated_by="cactus-prepare --outSeqFile seq.fa --outDir out", date=datetime.now())
    step1 = Step(
        raw="python -c \"from pathlib import Path; Path('a.txt').write_text('ok')\"",
        kind="preprocess",
        out_files=["a.txt"],
        label="make-a",
    )
    step2 = Step(
        raw="cactus-blast jobstore/0 seq.txt out.paf --root r",
        kind="blast",
        jobstore="jobstore/0",
        out_files=["out.paf"],
        label="blast",
    )
    plan = Plan(
        header=header,
        preprocess=[step1, step2],
        rounds=[],
        hal_merges=[],
        out_seq_file=str(tmp_path / "seq.fa"),
        out_dir=str(tmp_path),
    )

    runner = PlanRunner(plan, base_dir=tmp_path, env=env, run_settings=RunSettings(verbose=False, resume=True))
    with pytest.raises(RuntimeError):
        runner.run()
    assert (tmp_path / "jobstore" / "0").exists()

    # 触发断点回退：让第 1 步需要重跑，从而第 2 步不再是“本次重跑的第一步”
    (tmp_path / "a.txt").unlink()

    runner = PlanRunner(plan, base_dir=tmp_path, env=env, run_settings=RunSettings(verbose=False, resume=True))
    runner.run()

    assert (tmp_path / "out.paf").exists()
    assert "--restart" not in (tmp_path / "seen-args.txt").read_text()


def test_resume_reruns_blast_when_paf_inconsistent_with_fasta(tmp_path: Path):
    """PAF 引用的 contig 与当前 FASTA 不一致时，不应跳过 blast 步骤。"""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "cactus-blast",
        """#!/usr/bin/env python3
import sys
from pathlib import Path

counter = Path("blast-count.txt")
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))

out = sys.argv[3] if len(sys.argv) > 3 else "out.paf"

# 故意写一个“与 FASTA 不一致”的 PAF：A 的 contig 写成 A.chr1，但 FASTA 里只有 ArefChr0
Path(out).write_text(
    "id=B|BrefChr0\\t4\\t0\\t4\\t+\\tid=A|A.chr1\\t4\\t0\\t4\\t4\\t4\\t60\\n",
    encoding="utf-8",
)
sys.exit(0)
""",
    )
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}

    header = PrepareHeader(generated_by="cactus-prepare --outSeqFile seq.fa --outDir out", date=datetime.now())
    step0 = Step(
        raw=(
            "python -c \"from pathlib import Path; "
            "Path('A.fa').write_text('>ArefChr0\\nACGT\\n'); "
            "Path('B.fa').write_text('>BrefChr0\\nACGT\\n'); "
            "Path('seq.txt').write_text('(A:0.1,B:0.1)R;\\nA\\tA.fa\\nB\\tB.fa\\n')\""
        ),
        kind="preprocess",
        out_files=["A.fa", "B.fa", "seq.txt"],
        label="prep",
    )
    step1 = Step(
        raw="cactus-blast jobstore/0 seq.txt out.paf --root R",
        kind="blast",
        jobstore="jobstore/0",
        out_files=["out.paf"],
        label="blast",
    )
    step2 = Step(
        raw=(
            "python -c \"from pathlib import Path; p=Path('after-count.txt'); "
            "n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1))\""
        ),
        kind="preprocess",
        out_files=["after-count.txt"],
        label="after",
    )
    plan = Plan(
        header=header,
        preprocess=[step0, step1, step2],
        rounds=[],
        hal_merges=[],
        out_seq_file=str(tmp_path / "seq.fa"),
        out_dir=str(tmp_path),
    )

    runner = PlanRunner(plan, base_dir=tmp_path, env=env, run_settings=RunSettings(verbose=False, resume=True))
    runner.run()
    assert (tmp_path / "blast-count.txt").read_text() == "1"
    assert (tmp_path / "after-count.txt").read_text() == "1"

    runner = PlanRunner(plan, base_dir=tmp_path, env=env, run_settings=RunSettings(verbose=False, resume=True))
    runner.run()

    # step0 会被跳过，但 step1 的 PAF 校验失败 -> step1/step2 必须重跑
    assert (tmp_path / "blast-count.txt").read_text() == "2"
    assert (tmp_path / "after-count.txt").read_text() == "2"
