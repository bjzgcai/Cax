# Changelog

All notable changes to this project will be documented in this file.

## [0.2.1] - 2025-11-07

### Highlights
- The Textual `cactus-prepare` prompt now features an argument wizard (F2 / `:wizard`), template chooser (F3 / `:template`), and history window (F4 / `!N`), guiding newcomers through common flags, bundling Evolver examples, and reusing the 20 most recent commands to shorten onboarding.
- The CLI persists each command, infers the real output directory from `--outDir` or `--outSeqFile`, and writes `cax_prepare_debug.txt` into that directory so logs travel alongside their artifacts.

### CLI & Prompt
- Added inline shortcut hints plus `!N` history recall and the `:wizard`/`:template` quick commands, reducing re-typing and accidental edits.
- The wizard separates the species tree, outputs, HAL target, job store, and extra arguments; the extra field honors `shlex` parsing, and defaults are inferred from the current command or a chosen template.
- Before running `cactus-prepare`, the CLI records the exact command in `~/.cax/history.json` and emits debug output next to the inferred outputs (defaulting to `steps-output/`), keeping history, logs, and artifacts aligned.

### Templates & Examples
- Introduced a template manager that scans the packaged Evolver Newick samples together with user entries in `~/.cax/templates.json`, deduplicates them, and points defaults to `~/.cax/outputs/<stem>` so repeated runs do not collide.
- Wheel builds now ship the Evolver example files, so templates work immediately after installation without manual copies.

### History
- Added a lightweight history store (max 20 entries) in `~/.cax/history.json`, including the ability to delete entries from the history window and reuse them across projects.

### UI
- Plan Overview supports a compact mode below 110 columns, hiding the environment card and tightening columns to avoid overflow, while environment summaries and RaMAx option dialogs now use English copy.
- The command editor switched to a multi-line TextArea with `Ctrl+S` save support, making long commands easier to edit and presenting modal text in English for clarity.
- The environment summary card condenses multi-line paths and versions into single-line snippets, uses English labels, and stays readable at any terminal width.

### Other
- `.gitignore` now ignores `*.pyc` to avoid accidentally committing Python bytecode.

## [0.2.0] - 2025-10-28

### Highlights
- Added interactive confirmation before running `cactus-prepare`, allowing users to remove stale `--outDir`/`--jobStore` directories in advance.
- Enhanced execution feedback with Rich-based progress indicators, end-of-run summaries, and optional verbose streaming of all command output.
- Surfaced plan-level verbose state in the UI (toggle with `V`) and overview rendering, keeping the terminal quiet by default while filtering RaMAx graph verification noise.

### UI
- Added Environment Summary card to the top of the details pane, showing RaMAx/cactus paths and versions, GPU, CPU, memory, and disk. The card is now rendered as a Rich `Panel` and adapts to terminal width to avoid overflow and misalignment.
- Replaced fixed-width string rendering with Rich renderables (`Panel`, `Table`) for both the Environment Summary and Plan Overview, ensuring responsive layout from narrow to full-screen terminals.
- Introduced RaMAx options editor: global `plan.global_ramax_opts` and per-round `round.ramax_opts` editable via a new modal with add/remove controls. Options are reflected in command previews automatically.
- Removed unsupported `gap` CSS usage and adjusted spacing with margins to fix Textual CSS errors.

### Detection & Tooling
- Environment detection now reports cactus (not cactus-prepare) path and version. Cactus version is obtained via `pip show cactus | grep -i ^Version` with a fallback to `python -m pip show cactus` parsing; noisy NVML messages are filtered out.
- Minor CLI polish: removed echoing of the full `cactus-prepare` command to reduce console noise.

### Documentation & Tooling
- Updated README with the new verbose shortcut and pre-run cleanup behaviour.
- Expanded `PlanRunner` logging to emphasise failures and log locations.

## [0.1.0] - 2025-10-25

The inaugural release of Cactus-RaMAx introduces an interactive workflow for remixing `cactus-prepare` plans with RaMAx substitutions.

### Highlights
- Tree-aware editor that visualises the cactus progressive alignment hierarchy and supports toggling RaMAx across entire subtrees with a single keystroke.
- Execution planner that automatically suppresses cactus rounds and `halAppendSubtree` steps inside RaMAx-controlled subtrees, eliminating duplicate merges and related HAL errors.

### Documentation & Tooling
- Added this changelog to document future releases.
- Refreshed the quick-start guide and in-app messaging to match the new workflow.
- Captured the canonical project version in the new `VERSION` file.
