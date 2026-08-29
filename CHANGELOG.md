# Changelog

Reverse-chronological log of logical change batches.

## [2026-08-28] — Streaming parallel map + logger fixes

### Added
- `parallel.map(worker, inputs, ...)` / `parallel.starmap(...)`: high-level streaming API. Inputs are consumed lazily by a feeder thread with a bounded in-flight window, so generators and file iterators run with flat memory use and no known `total` (indeterminate progress bar). Options: `mode`, `workers`, `on_error` (`collect`/`raise`), `unordered`, `collect`, `post_func`, `maxtasksperchild` (process mode).
- `parallel.process_map` / `parallel.thread_map` convenience wrappers.
- `parallel.RunResult` dataclass (successes, failures, failed_inputs, results, duration, interrupted) with legacy 4-tuple unpacking.
- `CHRONOS_LOG_DIR` env var to override the log directory (was hardcoded CWD-relative `logs/`).
- `tests/test_parallel_map.py`: 14 tests covering ordered/unordered collection, generator inputs, failure recovery, starmap (thread + process), `collect=False` streaming, `on_error="raise"`, empty input, and head-of-line resilience.
- Design note: `docs/agent-work/2026-08-28-parallel-map-and-logger-fixes.md`.

### Fixed
- `_main_listener` no longer dies (silently dropping all later child logs) when a child log message arrives while no progress bar is active.
- "Global:" elapsed time in file logs is now per-process (`perf_counter` is not comparable across processes); replaced the `CHRONOS_START_TIME` env hack with a module global.
- README quick-start unpacked 3 values from a 4-tuple return; now uses `RunResult` attributes.

### Changed
- Ctrl+C during `parallel.map` returns the partial `RunResult` with `interrupted=True` instead of raising, preserving the completed subset for checkpointed pipelines.

## [2026-08-28] — Align tooling with init-py-project conventions

### Added
- pytest tier markers (`unit` / `component` / `system` / `live`) on all tests, with `tests/conftest.py` failing collection on unmarked tests.
- pixi tasks: `test-quick`, `test-unit`, `test-component`, `test-system`, `docs`, `fmt-check`, `lint-check` (already present), `typecheck`.
- Ruff configuration (`line-length = 100`, `target-version = py311`, rules `E,F,I,UP,B,SIM`).
- Sphinx documentation scaffold: `docs/chronos/conf.py` (sphinx-immaterial + MyST + autodoc), `docs` pixi task.
- `CHANGELOG.md`, `STATUS.md`, `AGENTS.md`.

### Changed
- Dev tooling (pytest, mypy, ruff, sphinx, myst-parser, sphinx-immaterial, build) moved from `[project.optional-dependencies]` into `[tool.pixi.pypi-dependencies]` so the pixi environment is self-contained.
- `.gitignore` extended: `*.egg-info/`, `.venv/`, `build/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.coverage`, `htmlcov/`, `docs/chronos/_build/`.
- `docs/work-items/` → `docs/agent-work/`, `docs/user-items/` → `docs/user-work/` per skill layout.

## [2026-08-16] — Type annotations

### Changed
- `refactor(logger)`: full type annotations across `logger.py` / `parallel.py`, formatting cleanup (0380ebc).

## [2026-07-11] — Convention alignment

### Changed
- `chore`: aligned project structure with init-py-project conventions (b3faf04): pixi tasks, pytest/ruff/mypy config, build dependency, `requires-python >= 3.11`, `docs/` layout, `.gitignore` entries.

## [2026-03-18] — 0.7.9

### Fixed
- Thread crash prevention (9b9b2dd).
- Ctrl+C exit handling (abfc1e6).

### Changed
- Version bump 0.7.7 → 0.7.9; loosened Python version requirement (d2fde40).
