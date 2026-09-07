# STATUS

## Capabilities
- Advanced logging (Rich UI, tree tracebacks, global error interception, standard-library interception, silencing, memory profiling, sticky system metrics): done
- Parallel execution (thread/process pools, task recovery, failure logs, fork-bomb protection, diverse returns): done
- Streaming parallel execution (`parallel.map`/`starmap`/`process_map`/`thread_map`: lazy generator inputs, bounded in-flight window, ordered/unordered collection, failure recovery, partial results on Ctrl+C): done
- Observability (dual routing console/file/JSONL/failures, execution summaries, daily rotation + 7-day retention): done
- Sphinx published docs beyond `index.md`: planned
- System-tier tests (end-to-end CLI-style workflow): planned

## Recent commits
- 2026-08-29: code audit fixes (RemoteProgress update/advance + collision-free IDs, interceptor never crashes caller, piped console no-wrap, summary markup, counts lock; 5 regression tests)
- 2026-09-07: v0.8.1 metadata-only release — Python floor lowered 3.11 → 3.10 (requires-python, mypy, ruff targets; no source changes needed)
- 2026-08-28: log output overhaul (compact clickable console format, 7-day retention, proxied-log dedup + ANSI fix, CHRONOS_DIAGNOSE gate)
- 2026-08-16: refactor(logger): add type annotations and clean up formatting
- 2026-07-11: chore: align project with init-py-project conventions

## Next work
- Queue-routed child file writes (eliminate multi-process rotation race; design in docs/agent-work/2026-08-28 note).
- Profiling suite: `@timed` decorator, `profile` context manager, per-label stats registry, `sys.monitoring` line profiler.
- `parallel.async_run` (asyncio.TaskGroup with same progress/failure machinery) and `parallel.pipeline`.
- Expand `docs/chronos/` beyond `index.md` (getting-started, API reference via autodoc, architecture).
- Add a system-tier test exercising `parallel.map` end-to-end.

## Known limitations
- Published docs are a single `index.md`; no API reference pages yet.
- No CLI entry point (`[project.scripts]`) — library-only surface.
- mypy excludes `tests/`; test-suite type coverage is not enforced.
