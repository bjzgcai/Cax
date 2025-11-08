# Cactus-RaMAx

Cactus-RaMAx helps you remix alignment plans emitted by `cactus-prepare`. You can inspect every round, toggle RaMAx for any subtree, and then run or export the resulting command list. Version `0.2.1` adds a Textual command prompt with an argument wizard, template chooser, and command history so first-time runs need fewer flags before you drop into the UI.

## Environment setup

We recommend creating a fresh Conda environment and installing the project in editable mode:

```bash
conda create -n cax python=3.10 -y
conda activate cax

pip install -e .
```

Alternatively, you can build a wheel and install it in a different environment:

```bash
python -m build
pip install dist/cactus_ramax-*.whl
```

## Quick start

### 1. Launch the interactive UI

Run the entry point directly:

```bash
cax
```

- If you do not pass `--prepare-args` or `--from-file`, a Textual prompt opens so you can type or assemble a full `cactus-prepare` command.
  - Press **F2** (or type `:wizard`) to open the argument wizard and fill `--outDir`, `--outSeqFile`, `--outHal`, and `--jobStore` one field at a time.
  - Press **F3** (or type `:template`) to choose from Evolver examples bundled with the package or from your own `~/.cax/templates.json`.
  - Press **F4** or type `!N` (for example `!1`) to recall the Nth entry from `~/.cax/history.json`. The prompt keeps the 20 most recent commands and lets you delete entries from the history window.
- Before running `cactus-prepare`, CAX infers the effective output directory (from `--outDir` or the parent directory of `--outSeqFile`) and offers to delete existing `--outDir`/`--jobStore` paths so the run starts cleanly.
- After execution completes, the UI displays the parsed plan and lets you toggle RaMAx replacements before running or exporting.
- Scripted usage is still supported:
  ```bash
  cax --prepare-args "examples/evolverMammals.txt --outDir steps-output --outSeqFile ... --outHal ... --jobStore jobstore"
  ```
  or load an existing output:
  ```bash
  cax --from-file steps-output/prepare_output.txt
  ```
- Pass `--threads 32` to seed the run-settings prompt so cactus steps inherit `--maxCores 32` and RaMAx receives `--threads 32`; leave it unset to default to each command's original flag.

### 2. Work inside the UI

- The left pane renders the cactus progressive tree; press **Space** to toggle the selected subtree between cactus and RaMAx (use **Ctrl+Space** to expand or collapse nodes).
- The details pane now includes an environment summary card (RaMAx/cactus paths, versions, GPU, CPU, memory, disk) plus a compact plan overview table that adapts to narrow terminals.
- `E`: edit commands for the selected round or RaMAx replacement in a multi-line editor (press **Ctrl+S** to save).
- `R`: run the entire plan; CAX switches to the Run Settings screen so you can review verbose logging and the shared thread count before execution.
- Run Settings shows a live plan summary next to the form, and you can drive it entirely with the keyboard (`Tab` / `Shift+Tab` to focus fields, `Ctrl+Enter` to launch, `V` to toggle verbose).
- `S`: export all commands to `ramax_commands.txt` inside the chosen output directory.
- `P`: refresh the overview table.
- `Q`: quit the UI.
- Verbose streaming is only controlled via the run-settings dialog so you can review the choice right before execution.

When RaMAx is enabled for a round or subtree, execution stops on the first failure—it does not fall back to cactus `blast`/`align` automatically.

### 3. Templates and history (optional)

- Built-in templates are sourced from the packaged Evolver mammals/primates examples and any `.txt` files you add under `examples/`; user-defined templates live in `~/.cax/templates.json`.
- Command history is stored at `~/.cax/history.json`. It deduplicates consecutive runs, keeps up to 20 entries, and syncs with the Textual prompt so you can reuse or delete past commands.

## Logging and troubleshooting

- The raw output from `cactus-prepare` is stored at `<out_dir>/cax_prepare_debug.txt`. If you only passed `--outSeqFile`, the parent directory of that file becomes the inferred output directory.
- Runtime logs reuse the directories referenced by the original plan, for example `steps-output/logs/`.
- Command history and templates live under `~/.cax/` so you can reuse them across projects or machines.

## Feedback

Open an issue or pull request to help us iterate on the combined Cactus/RaMAx workflow.
