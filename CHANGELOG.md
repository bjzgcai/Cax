# Changelog

All notable changes to this project will be documented in this file.

# Changelog

All notable changes to this project will be documented in this file.

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
