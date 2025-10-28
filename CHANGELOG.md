# Changelog

All notable changes to this project will be documented in this file.

# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - Unreleased

### Highlights
- Added interactive confirmation before running `cactus-prepare`, allowing users to remove stale `--outDir`/`--jobStore` directories in advance.
- Enhanced execution feedback with Rich-based progress indicators, end-of-run summaries, and optional verbose streaming of all command output.
- Surfaced plan-level verbose state in the UI (toggle with `V`) and overview rendering, keeping the terminal quiet by default while filtering RaMAx graph verification noise.

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
