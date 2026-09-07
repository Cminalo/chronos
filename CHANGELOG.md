# Changelog

Reverse-chronological log of logical change batches.

## [v0.8.1] — 2026-09-07

### Changed
- Lowered the supported Python floor from 3.11 to 3.10: `requires-python >= 3.10`, mypy `python_version = "3.10"`, ruff `target-version = "py310"`. No source changes were needed — the codebase uses no 3.11+ syntax or stdlib APIs. Metadata-only release; 0.8.0 remains on PyPI but can never be re-uploaded (PyPI versions are immutable), so the fix ships as 0.8.1.

## [v0.8.0] — 2026-08-29

Release tag for the batches below (2026-08-28 → 2026-08-29): streaming map API in `parallel`, compact clickable console format with 7-day retention, TTY-aware colorization, and the child progress proxy / interceptor / console sink hardening. Version bump 0.7.9 → 0.8.0 (new public API: `parallel` streaming map).

## [2026-08-29] — Code audit: child progress proxy, interceptor, console sink

### Fixed
- `RemoteProgress` (child-process progress proxy) now implements `update()` and `advance()` — the main-process listener already handled `"update"` messages, but the proxy could never send them, so `p.update(tid, advance=1)` in a child raised `AttributeError`.
- `RemoteProgress.add_task` IDs no longer collide: `id(description) + ms-timestamp` repeated for interned string literals and recycled objects, cross-wiring updates between bars; IDs are now `pid * 10_000_000 + process-lifetime sequence`.
- `InterceptHandler.emit` degrades gracefully on malformed stdlib records (e.g. `logging.info("%d", "x")` raising `TypeError` in `getMessage`): the error goes through `Handler.handleError` (stderr diagnostic) instead of propagating into caller code — matching chronos' never-crash-the-caller contract.
- Piped/redirected console output is no longer hard-wrapped at 80 columns by the Rich sink (one long log record used to split into several physical lines); the sink now prints with `soft_wrap=True`.
- `summary()` renders valid Rich markup for compound level colors (`<red><bold>` previously became the invalid tag `red><bold`, silently dropping bold).
- `_main_listener` catches queue poll timeouts via explicit `queue.Empty` instead of a broad `except Exception`.
- `_LOG_COUNTS` increments are guarded by a lock, so concurrent logging threads can no longer lose counts.

### Tests
- 5 new regression tests in `tests/test_logger_robustness.py`: collision-free proxy task IDs, proxy update/advance → listener round trip, malformed-stdlib-record graceful degradation, piped-sink no-wrap contract, and loguru→Rich color-tag conversion validity for every level.

## [2026-08-29] — ANSI-correct colorization + logging robustness hardening

### Changed
- Colorization is now TTY-aware end to end: piped/redirected output (console, Rich path, and child-process proxy lines) carries zero ANSI escapes; TTY output stays fully colored. The child proxy decides colorization from the inherited stderr fd, matching the parent console.
- `summary()` no longer relies on a stripped-under-`-O` assert; missing Rich console falls back to the plain-text report.

### Fixed
- `_main_listener` survives malformed child messages (wrong arity, non-tuple garbage): one bad message logs a warning instead of killing the thread and silently dropping every later child log.
- Import no longer crashes on hostile configuration: unwritable/invalid `CHRONOS_LOG_DIR` falls back to `<tempdir>/chronos-logs`, invalid `LOGGER_LEVEL` falls back to `INFO`, both with a logged warning.
- Restored the module-level `_logger.remove()` lost during refactoring — its absence silently doubled every console line (loguru default sink + chronos sink). Regression test added.

### Tests
- New `tests/test_logger_robustness.py` (17 tests, unit/component/system): config-resolution fallbacks, listener resilience to garbage queue messages, proxied-log console-only routing contract, 7-day retention sink contract, custom stdlib-level interception, `summary()` fallbacks, import-time sink-set contract (subprocess-isolated), and a full spawn child→proxy→console/file round trip asserting single clean lines.

## [2026-08-28] — Log output overhaul + retention policy

### Changed
- Console format redesigned for scannability and IDE click-through: compact single-line records (`HH:mm:ss.SSS | LEVEL | message  path:line`), message-first layout, dim clickable `path:line` suffix (cwd-relative, absolute fallback), full date / process-thread ids / quotes moved to file logs.
- File text format: location now renders as clickable `path:line in function()` instead of `module -> function -> line`; process/thread shown as readable names (`[P:MainProcess|T:MainThread]`); benchmark duration renders as `finished in X.XXXs` (dropped the near-useless "Global:" uptime).
- Retention policy enforced across all file sinks (text, JSONL, failures, and child-process sinks): daily rotation at midnight, 7-day maximum retention (was 10 days), centralized in `_add_file_sink`.
- `diagnose` (live variable interpolation in tracebacks — secret-leak risk) now defaults to False everywhere and is opt-in via `CHRONOS_DIAGNOSE=True`; console tracebacks use the clean standard form (`backtrace=False`).

### Fixed
- Child-process logs were written twice to the text/JSONL files (child's own sinks + raw re-log by the main-process listener); proxied messages are now console-only via a `proxied` record flag.
- Proxied child messages leaked ANSI escape codes into text log files (`proxy_sink` hardcoded `colorize=True`); the proxy now formats with the console formatter and only console sinks receive it.
- Locations are escaped for loguru's colorizer, so pseudo-paths like `<stdin>` no longer crash formatting.

### Tests
- `test_benchmark_context` asserts the new `finished in X.XXXs` duration format.

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
