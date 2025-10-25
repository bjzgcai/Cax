# Cactus-RaMAx

Cactus-RaMAx helps you remix alignment plans emitted by `cactus-prepare`. You can inspect every round, toggle RaMAx for any subtree, and then run or export the resulting command list. Version `0.1.0` introduces a tree-based editor so that RaMAx can replace entire cactus subtrees instead of only individual rounds.

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

- If you do not pass `--prepare-args` or `--from-file`, the program first prompts you for the full `cactus-prepare` command (for example `cactus-prepare examples/... --outDir ...`).
- After execution completes, the UI displays the parsed plan and lets you toggle RaMAx replacements before running or exporting.
- Scripted usage is still supported:
  ```bash
  cax --prepare-args "examples/evolverMammals.txt --outDir steps-output --outSeqFile ... --outHal ... --jobStore jobstore"
  ```
  or load an existing output:
  ```bash
  cax --from-file steps-output/prepare_output.txt
  ```

### 2. Work inside the UI

- The left pane renders the cactus progressive tree; press **Space** to toggle the selected subtree between cactus and RaMAx (use **Ctrl+Space** to expand or collapse nodes).
- The details pane shows the current node, generated commands, and a subtree summary that counts how many rounds are using RaMAx.
- `E`: edit commands for the selected round or RaMAx replacement.
- `R`: run the entire plan immediately.
- `S`: export all commands to `ramax_commands.txt` inside the chosen output directory.
- `P`: refresh the overview table.
- `Q`: quit the UI.

When RaMAx is enabled for a round or subtree, execution stops on the first failure—it does not fall back to cactus `blast`/`align` automatically.

## Logging and troubleshooting

- The raw output from `cactus-prepare` is stored at `steps-output/cax_prepare_debug.txt` (the path follows your `--outDir` if you change it).
- Runtime logs reuse the directories referenced by the original plan, for example `steps-output/logs/`.

## Feedback

Open an issue or pull request to help us iterate on the combined Cactus/RaMAx workflow.
