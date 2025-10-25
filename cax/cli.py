"""Typer-powered command line interface for the streamlined CAX toolkit."""
from __future__ import annotations

from pathlib import Path
import shlex
import subprocess
from typing import Optional

import typer
from rich import print

from . import command_prompt, parser, render, ui as ui_module
from .runner import PlanRunner

app = typer.Typer(help="Cactus-RaMAx interactive tools (ui only)")


def _load_prepare_text(
    prepare_args: Optional[str],
    from_file: Optional[Path],
    executable: str = "cactus-prepare",
) -> str:
    if prepare_args is not None:
        cmd = [executable, *shlex.split(prepare_args)]
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


@app.command()
def ui(
    prepare_args: Optional[str] = typer.Option(None, help="Arguments passed through to cactus-prepare"),
    from_file: Optional[Path] = typer.Option(None, help="Parse prepare output from an existing file"),
    run_after: bool = typer.Option(False, help="Run the plan after exiting the UI"),
) -> None:
    """Launch the interactive Textual UI for plan editing."""

    executable = "cactus-prepare"
    if prepare_args is None and from_file is None:
        prompt_result = command_prompt.prompt_prepare_command()
        if prompt_result.action == "quit":
            typer.echo("[cax] Cancelled.")
            return
        prepare_args = prompt_result.args
        executable = prompt_result.executable or executable

    text = _load_prepare_text(prepare_args, from_file, executable=executable)
    plan = parser.parse_prepare_script(text)
    result = ui_module.launch(plan)
    plan = result.plan
    if result.action == "run" or run_after:
        runner = PlanRunner(plan)
        runner.run()
    else:
        print(render.plan_overview(plan))


if __name__ == "__main__":
    app()
