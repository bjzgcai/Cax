# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0] - 2025-10-25

The inaugural release of Cactus-RaMAx introduces an interactive workflow for remixing `cactus-prepare` plans with RaMAx substitutions.

### Highlights
- Tree-aware editor that visualises the cactus progressive alignment hierarchy and supports toggling RaMAx across entire subtrees with a single keystroke.
- Execution planner that automatically suppresses cactus rounds and `halAppendSubtree` steps inside RaMAx-controlled subtrees, eliminating duplicate merges and related HAL errors.

### Documentation & Tooling
- Added this changelog to document future releases.
- Refreshed the quick-start guide and in-app messaging to match the new workflow.
- Captured the canonical project version in the new `VERSION` file.
