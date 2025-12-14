import os
import stat
from datetime import datetime
from pathlib import Path

from cax.models import Plan, PrepareHeader, Round, RunSettings, Step
from cax.runner import PlanRunner
from cax.resume import preview_resume


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_resume_skips_even_when_threads_change_for_cactus_commands(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "cactus-preprocess",
        """#!/usr/bin/env python3
import sys
from pathlib import Path

out = None
for arg in sys.argv[1:]:
    if arg.endswith(".txt"):
        out = arg
        break
out = out or "count.txt"
p = Path(out)
n = int(p.read_text()) if p.exists() else 0
p.write_text(str(n + 1))
""",
    )
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}

    header = PrepareHeader(generated_by="cactus-prepare --outSeqFile seq.fa --outDir out", date=datetime.now())
    step = Step(raw="cactus-preprocess count.txt", kind="preprocess", out_files=["count.txt"])
    plan = Plan(
        header=header,
        preprocess=[step],
        rounds=[],
        hal_merges=[],
        out_seq_file=str(tmp_path / "seq.fa"),
        out_dir=str(tmp_path),
    )

    runner = PlanRunner(
        plan,
        base_dir=tmp_path,
        env=env,
        run_settings=RunSettings(verbose=False, resume=True, thread_count=4),
    )
    runner.run()
    assert (tmp_path / "count.txt").read_text() == "1"

    preview = preview_resume(plan, base_dir=tmp_path, thread_count=8)
    assert preview is not None
    assert preview.plan_matches is False  # thread_count 变更会导致签名不同
    assert "cactus-preprocess" in preview.completed  # 但续跑匹配仍应能跳过

    runner = PlanRunner(
        plan,
        base_dir=tmp_path,
        env=env,
        run_settings=RunSettings(verbose=False, resume=True, thread_count=8),
    )
    runner.run()
    assert (tmp_path / "count.txt").read_text() == "1"  # 已完成步骤仍可被跳过


def test_resume_keeps_skipping_completed_steps_when_future_step_changes(tmp_path: Path):
    header = PrepareHeader(generated_by="cactus-prepare --outSeqFile seq.fa --outDir out", date=datetime.now())
    step1 = Step(
        raw=(
            "python -c \"from pathlib import Path; p=Path('count.txt'); "
            "n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1))\""
        ),
        kind="preprocess",
        out_files=["count.txt"],
    )
    step2 = Step(
        raw="python -c \"from pathlib import Path; Path('marker.txt').write_text('v1')\"",
        kind="preprocess",
        out_files=["marker.txt"],
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
    assert (tmp_path / "count.txt").read_text() == "1"
    assert (tmp_path / "marker.txt").read_text() == "v1"

    # 微调后续步骤命令（模拟用户编辑待执行/后续步骤），应不影响已完成步骤的跳过。
    step2.raw = "python -c \"from pathlib import Path; Path('marker.txt').write_text('v2')\""
    runner = PlanRunner(plan, base_dir=tmp_path, run_settings=RunSettings(verbose=False, resume=True))
    runner.run()

    assert (tmp_path / "count.txt").read_text() == "1"  # 第一步仍被跳过
    assert (tmp_path / "marker.txt").read_text() == "v2"  # 第二步按新命令重跑


def test_resume_reruns_ramax_when_output_missing(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "ramax",
        """#!/usr/bin/env python3
import sys
from pathlib import Path

out = None
for i, arg in enumerate(sys.argv[1:]):
    if arg == "-o" and i + 2 <= len(sys.argv[1:]):
        out = sys.argv[1:][i + 1]
        break
    if arg.startswith("-o="):
        out = arg.split("=", 1)[1]
        break

out = out or "out.hal"
Path(out).write_text("ok")

counter = Path("ramax-count.txt")
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
""",
    )
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}

    header = PrepareHeader(generated_by="cactus-prepare --outSeqFile seq.fa --outDir out", date=datetime.now())
    round1 = Round(
        name="Round 1",
        root="root1",
        target_hal="out.hal",
        replace_with_ramax=True,
    )
    plan = Plan(
        header=header,
        preprocess=[],
        rounds=[round1],
        hal_merges=[],
        out_seq_file=str(tmp_path / "seq.fa"),
        out_dir=str(tmp_path),
    )

    runner = PlanRunner(
        plan,
        base_dir=tmp_path,
        env=env,
        run_settings=RunSettings(verbose=False, resume=True, thread_count=4),
    )
    runner.run()
    assert (tmp_path / "out.hal").exists()
    assert (tmp_path / "ramax-count.txt").read_text() == "1"

    # 线程数变化不应导致已完成的 RaMAx 被重复执行
    runner = PlanRunner(
        plan,
        base_dir=tmp_path,
        env=env,
        run_settings=RunSettings(verbose=False, resume=True, thread_count=8),
    )
    runner.run()
    assert (tmp_path / "ramax-count.txt").read_text() == "1"

    # 删除产物，应触发重跑
    (tmp_path / "out.hal").unlink()
    runner = PlanRunner(
        plan,
        base_dir=tmp_path,
        env=env,
        run_settings=RunSettings(verbose=False, resume=True, thread_count=8),
    )
    runner.run()
    assert (tmp_path / "out.hal").exists()
    assert (tmp_path / "ramax-count.txt").read_text() == "2"

