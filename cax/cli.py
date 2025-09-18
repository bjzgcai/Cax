"""Typer-powered command line interface for the CAX toolkit."""
from __future__ import annotations

from pathlib import Path
import shlex
import subprocess
from typing import Iterable, Optional


import typer
from rich import print

from . import config, parser, planner, render, ui as ui_module
from .models import Plan
from .runner import PlanRunner

FallbackValues = {'cactus', 'abort'}

app = typer.Typer(help="Cactus-RaMAx helper tools")


def _fallback_value(value: str) -> str:
    normalized = value.lower()
    if normalized not in FallbackValues:
        raise typer.BadParameter(f"fallback must be one of {sorted(FallbackValues)}")
    return normalized


def _load_prepare_text(prepare_args: Optional[str], from_file: Optional[Path]) -> str:
    if prepare_args:
        cmd = ["cactus-prepare", *shlex.split(prepare_args)]
        typer.echo(f"[cax] running: {' '.join(cmd)}")
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            typer.echo(result.stdout)
            typer.echo(result.stderr, err=True)
            raise typer.Exit(code=result.returncode)
        output = result.stdout or ""
        Path("steps-output").mkdir(exist_ok=True, parents=True)
        Path("steps-output/cax_prepare_debug.txt").write_text(output, encoding="utf-8")
        return output
    if from_file:
        return Path(from_file).read_text(encoding="utf-8")
    typer.echo("Either --prepare-args or --from-file must be provided.", err=True)
    raise typer.Exit(code=1)


def _update_round_replacements(plan: Plan, replace_rounds: Iterable[str]) -> None:
    selected = {name.strip() for name in replace_rounds if name.strip()}
    if not selected:
        return
    for round_entry in plan.rounds:
        base_name = round_entry.name
        plain_name = base_name.split(" (")[0]
        if base_name in selected or plain_name in selected:
            round_entry.replace_with_ramax = True


@app.command()
def ui(
    prepare_args: Optional[str] = typer.Option(None, help="Arguments passed through to cactus-prepare"),
    from_file: Optional[Path] = typer.Option(None, help="Parse prepare output from an existing file"),
    run_after: bool = typer.Option(False, help="Run the plan after exiting the UI"),
) -> None:
    """Launch the interactive Textual UI for plan editing."""

    text = _load_prepare_text(prepare_args, from_file)
    plan = parser.parse_prepare_script(text)
    result = ui_module.launch(plan)
    plan = result.plan
    if result.action == "run" or run_after:
        runner = PlanRunner(plan)
        runner.run()
    else:
        print(render.plan_overview(plan))


@app.command()
def plan(
    output: Path = typer.Option(..., help="Destination YAML file for the plan"),
    prepare_args: Optional[str] = typer.Option(None, help="Arguments to pass to cactus-prepare"),
    from_file: Optional[Path] = typer.Option(None, help="Read cactus-prepare output from file"),
    replace_rounds: Optional[str] = typer.Option(None, help="Comma-separated round names to replace with RaMAx"),
    fallback: str = typer.Option("cactus", help="Fallback policy when RaMAx fails", show_default=True),
    dry_run: bool = typer.Option(False, help="Mark the plan as dry-run"),
    run_script: Optional[Path] = typer.Option(None, help="Optional run.sh export path"),
) -> None:
    """Generate a YAML plan without executing it."""

    text = _load_prepare_text(prepare_args, from_file)
    plan_obj = parser.parse_prepare_script(text)
    plan_obj.fallback_policy = _fallback_value(fallback)  # type: ignore[assignment]
    plan_obj.dry_run = dry_run
    if replace_rounds:
        _update_round_replacements(plan_obj, replace_rounds.split(","))
    config.save_plan(plan_obj, output)
    typer.echo(f"Plan saved to {output}")
    if run_script:
        commands = planner.build_execution_plan(plan_obj)
        run_text = render.render_run_script(plan_obj, commands)
        run_script.parent.mkdir(parents=True, exist_ok=True)
        run_script.write_text(run_text, encoding="utf-8")
        typer.echo(f"Run script written to {run_script}")


@app.command()
def run(
    config_path: Path = typer.Option(..., help="Path to a previously saved plan YAML"),
    dry_run: Optional[bool] = typer.Option(None, help="Override the plan's dry-run flag"),
    run_script: Optional[Path] = typer.Option(None, help="Export a run.sh alongside execution"),
    quiet: bool = typer.Option(False, help="Silence live command output"),
) -> None:
    """Execute a saved plan."""

    plan_obj = config.load_plan(config_path)
    if dry_run is not None:
        plan_obj.dry_run = dry_run
    commands = planner.build_execution_plan(plan_obj)
    if run_script:
        run_text = render.render_run_script(plan_obj, commands)
        run_script.parent.mkdir(parents=True, exist_ok=True)
        run_script.write_text(run_text, encoding="utf-8")
        typer.echo(f"Run script written to {run_script}")
    runner = PlanRunner(plan_obj, mirror_stdout=not quiet)
    runner.run()


@app.command()
def go(
    seq_file: Path = typer.Argument(..., help="Input sequence file passed to cactus-prepare"),
    out_dir: Path = typer.Option(..., help="--outDir value for cactus-prepare"),
    out_seq_file: Path = typer.Option(..., help="--outSeqFile value"),
    out_hal: Path = typer.Option(..., help="--outHal value"),
    job_store: str = typer.Option(..., help="--jobStore value"),
    replace_rounds: Optional[str] = typer.Option(None, help="Comma-separated round names to replace"),
    fallback: str = typer.Option("cactus", help="Fallback policy [cactus|abort]"),
    dry_run: bool = typer.Option(False, help="Enable dry-run mode"),
    ramax_opt: Optional[list[str]] = typer.Option(None, help="Additional --ramax-opt flags"),
    run_script: Optional[Path] = typer.Option(None, help="Export run.sh before executing"),
    quiet: bool = typer.Option(False, help="Silence live command output"),
) -> None:
    """Shortcut: prepare, configure replacements, and execute immediately."""

    prepare_tokens = [
        str(seq_file),
        "--outDir",
        str(out_dir),
        "--outSeqFile",
        str(out_seq_file),
        "--outHal",
        str(out_hal),
        "--jobStore",
        job_store,
    ]
    prepare_args = shlex.join(prepare_tokens)
    script_text = _load_prepare_text(prepare_args, None)
    plan_obj = parser.parse_prepare_script(script_text)
    plan_obj.fallback_policy = _fallback_value(fallback)  # type: ignore[assignment]
    plan_obj.dry_run = dry_run
    if ramax_opt:
        plan_obj.global_ramax_opts = list(ramax_opt)
    if replace_rounds:
        _update_round_replacements(plan_obj, replace_rounds.split(","))
    commands = planner.build_execution_plan(plan_obj)
    if run_script:
        run_text = render.render_run_script(plan_obj, commands)
        run_script.parent.mkdir(parents=True, exist_ok=True)
        run_script.write_text(run_text, encoding="utf-8")
        typer.echo(f"Run script written to {run_script}")
    runner = PlanRunner(plan_obj, mirror_stdout=not quiet)
    runner.run()


if __name__ == "__main__":
    app()
