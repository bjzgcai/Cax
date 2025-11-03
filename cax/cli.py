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

    out_dir_preview, job_store_preview = _prepare_plan_preview(executable, prepare_args, from_file)
    _ensure_clean_environment(out_dir_preview, job_store_preview)
    text = _load_prepare_text(prepare_args, from_file, executable=executable)
    plan = parser.parse_prepare_script(text)
    result = ui_module.launch(plan)
    plan = result.plan
    if result.action == "run" or run_after:
        runner = PlanRunner(plan, verbose=plan.verbose)
        runner.run()
    else:
        print(render.plan_overview(plan))


if __name__ == "__main__":
    app()


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
    out_dir = _extract_flag(tokens, "--outDir")
    out_seq = _extract_flag(tokens, "--outSeqFile")
    # If outDir not provided but outSeqFile is, derive from it.
    if out_dir is None and out_seq:
        out_dir = str(Path(out_seq).resolve().parent)
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
