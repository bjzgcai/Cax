"""Typer-powered command line interface for the streamlined CAX toolkit."""
from __future__ import annotations

from pathlib import Path
import shlex
import subprocess
from typing import Optional

import typer
from rich import print
import shutil

from . import command_prompt, history, parser, render, ui as ui_module
from .models import RunSettings
from .runner import PlanRunner

app = typer.Typer(help="Cactus-RaMAx interactive tools (ui only)")


def _load_prepare_text(
    prepare_args: Optional[str],
    from_file: Optional[Path],
    executable: str = "cactus-prepare",
) -> str:
    if prepare_args is not None:
        cmd = [executable, *shlex.split(prepare_args)]
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            typer.echo(result.stdout)
            typer.echo(result.stderr, err=True)
            raise typer.Exit(code=result.returncode)
        output = result.stdout or ""
        history.add_command(shlex.join(cmd))
        tokens = cmd[1:]
        out_dir_path = _discover_out_dir(tokens)
        if out_dir_path is None:
            out_dir_path = Path("steps-output")
        out_dir_path.mkdir(exist_ok=True, parents=True)
        debug_path = out_dir_path / "cax_prepare_debug.txt"
        debug_path.write_text(output, encoding="utf-8")
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
    threads: Optional[int] = typer.Option(
        None,
        min=1,
        help="Override cactus/RaMAx thread count for all steps (leave unset for command defaults)",
    ),
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

    out_dir_preview, job_store_preview = _prepare_plan_preview(executable, prepare_args, from_file)
    _ensure_clean_environment(out_dir_preview, job_store_preview)
    text = _load_prepare_text(prepare_args, from_file, executable=executable)
    plan = parser.parse_prepare_script(text)
    run_settings = RunSettings(verbose=False, thread_count=threads)
    result = ui_module.launch(plan, run_settings=run_settings)
    plan = result.plan
    run_settings = result.run_settings or run_settings
    if result.action == "run" or run_after:
        if result.action != "run":
            run_settings = _prompt_run_settings(run_settings)
        runner = PlanRunner(plan, run_settings=run_settings)
        runner.run()
    else:
        print(render.plan_overview(plan, run_settings=run_settings))


if __name__ == "__main__":
    app()


def _prompt_run_settings(defaults: RunSettings) -> RunSettings:
    """Collect run-time settings from the user just before execution."""

    typer.echo("[cax] Configure run settings before execution:")
    verbose = typer.confirm(
        "Enable verbose logging (stream every command output)?",
        default=defaults.verbose,
    )

    thread_count = defaults.thread_count
    while True:
        default_display = "" if thread_count is None else str(thread_count)
        prompt = typer.prompt(
            "Thread count for cactus/RaMAx (blank = auto)",
            default=default_display,
            show_default=bool(default_display),
        )
        stripped = prompt.strip()
        if not stripped:
            thread_count = None
            break
        try:
            value = int(stripped)
        except ValueError:
            typer.echo("[cax] Please enter a positive integer or leave blank.")
            continue
        if value <= 0:
            typer.echo("[cax] Thread count must be at least 1.")
            continue
        thread_count = value
        break

    return RunSettings(verbose=verbose, thread_count=thread_count)


def _prepare_plan_preview(
    executable: str,
    prepare_args: Optional[str],
    from_file: Optional[Path],
) -> tuple[Optional[str], Optional[str]]:
    """Return the prospective --outDir and --jobStore before running cactus-prepare."""

    if from_file:
        try:
            text = Path(from_file).read_text(encoding="utf-8")
            plan = parser.parse_prepare_script(text)
            return plan.out_dir, None
        except OSError:
            return None, None
    if prepare_args is None:
        return None, None
    tokens = shlex.split(prepare_args)
    out_dir_path = _discover_out_dir(tokens)
    out_dir = str(out_dir_path) if out_dir_path else None
    job_store = _extract_flag(tokens, "--jobStore") or _extract_flag(tokens, "--jobstore")
    # Some users may pass --jobStore=file:/path or jobstore=...; leave as-is for now.
    return out_dir, job_store


def _extract_flag(tokens: list[str], flag: str) -> Optional[str]:
    for idx, tok in enumerate(tokens):
        if tok == flag and idx + 1 < len(tokens):
            return tokens[idx + 1]
        if tok.startswith(flag + "="):
            return tok.split("=", 1)[1]
    return None


def _discover_out_dir(tokens: list[str]) -> Optional[Path]:
    """Infer the output directory from cactus-prepare style tokens."""

    out_dir = _extract_flag(tokens, "--outDir")
    if out_dir:
        return Path(out_dir).expanduser()
    out_seq = _extract_flag(tokens, "--outSeqFile")
    if out_seq:
        seq_path = Path(out_seq).expanduser()
        try:
            parent = seq_path.resolve().parent
        except OSError:
            parent = seq_path.parent
        return parent
    return None


def _ensure_clean_environment(out_dir: Optional[str], job_store: Optional[str]) -> None:
    """Before running cactus-prepare, optionally clean existing output directories."""

    candidates: list[Path] = []
    if out_dir:
        out_path = _resolve_path(out_dir)
        candidates.append(out_path)
    if job_store:
        job_path = _resolve_path(job_store)
        candidates.append(job_path)
    # When no explicit jobStore is supplied, Toil uses subdirectories jobstore/0, etc.
    candidates.append(Path.cwd() / "jobstore")

    existing: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved.exists() and resolved not in seen:
            seen.add(resolved)
            existing.append(resolved)

    if not existing:
        return

    typer.echo("[cax] Warning: existing directories detected:")
    for path in existing:
        try:
            relative = path.relative_to(Path.cwd())
            typer.echo(f"  - {relative}")
        except ValueError:
            typer.echo(f"  - {path}")

    if typer.confirm("Delete these directories before running cactus-prepare?", default=False):
        for path in existing:
            if not path.exists():
                continue
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError as exc:
                typer.echo(f"[cax] Failed to remove {path}: {exc}")
        typer.echo("[cax] Existing directories removed.")
    else:
        typer.echo("[cax] Keeping existing directories (may reuse previous outputs).")


def _resolve_path(path_like: str) -> Path:
    if path_like.startswith("file:"):
        path_like = path_like.split(":", 1)[1]
    path = Path(path_like)
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()
