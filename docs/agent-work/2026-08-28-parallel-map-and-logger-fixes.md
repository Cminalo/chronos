---
title: "parallel.map streaming API + logger fixes"
date: 2026-08-28
tags:
  - chronos
  - parallel
  - logger
  - design
---

# parallel.map streaming API + logger fixes

## Goal

Make chronos usable for jobs whose inputs cannot be materialized as a list
(e.g. line-by-line processing of a huge file), and fix the defects found in the
2026-08-28 code review. This batch delivers the high-priority items from the
review; profiling suite, async support, and pipelines are deferred (see
"Deferred" below).

## Problems

### P1 — parallel API forces materialized inputs

`parallel.execute()` (`src/chronos/parallel.py`) requires a `prep_func(pool)`
that the user writes, calling `pool.apply_async` themselves and returning
`[(input, AsyncResult), ...]`. Consequences:

- Inputs must be materialized; a generator over a 10 GB file cannot be used.
- `total` must be known upfront.
- `execute()` sniffs tuples via `isinstance(item, tuple) and not hasattr(item, "get")`
  (line ~125), which misparses legitimate 2-tuple results.
- Results are collected in submission order via blocking `result.get(timeout=1.0)`
  (lines ~137-142): head-of-line blocking makes the progress bar and `post_func`
  lag actual completions.
- `results` accumulates unboundedly in RAM; no `collect=False` streaming option.
- No `chunksize`, `maxtasksperchild`, initializer passthrough, or retries.

### P2 — logger defects

| # | Defect | Location | Fix |
|---|--------|----------|-----|
| B1 | Listener thread dies on child log when no progress is active: `assert p is not None` fires for `("log", ...)` messages, killing `_main_listener`; all later child logs silently lost | `logger.py:333-337` | Handle `log` messages regardless of `_ACTIVE_PROGRESS`; only skip `progress` messages when no active progress |
| B2 | `CHRONOS_START_TIME` stored in `os.environ`; `perf_counter()` is per-process, so spawned children inherit the parent's value and compute garbage "Global:" times | `logger.py:150-151`, used at `159`, `560`, `568` | Module global `_CHRONOS_START_TIME: float = time.perf_counter()`; remove env var |
| B3 | README quick-start unpacks 3 values but `process_run` returns a 4-tuple | `README.md:59` | Fix copy; supersede with `parallel.map` docs |
| B4 | Log dir is CWD-relative (`Path("logs")`); running from other dirs scatters logs | `logger.py:83` | `CHRONOS_LOG_DIR` env override, default `logs` |
| B5 | Multi-process file-sink rotation race: children re-open the same rotating sinks; loguru rotation across processes can corrupt/duplicate files | `logger.py:415-433` | **Deferred** (see below) |

## Design: `parallel.map` / `parallel.starmap`

Chronos owns submission; the user supplies a worker and any iterable.

```python
result = parallel.map(
    worker,                      # Callable[[Any], Any]; picklable in process mode
    inputs,                      # ANY iterable — list, generator, file iterator
    mode="process",              # or "thread"
    desc=None,                   # defaults to worker.__name__
    total=None,                  # auto from len() if Sized; None -> indeterminate bar
    workers=None,                # default cpu_count()
    on_error="collect",          # or "raise" (first worker exception propagates)
    unordered=False,             # True: process completions out of order
    collect=True,                # False: stream to post_func, don't accumulate
    post_func=None,              # per-result callback; its exceptions count as task failure
)
# -> RunResult(successes, failures, failed_inputs, results, duration, interrupted)
```

`parallel.starmap(worker, inputs)` — same, but each input is an args tuple.

### Mechanics

- **Feeder thread** (daemon): consumes `inputs` lazily, submits
  `pool.apply_async(worker, args)` keeping at most `4 × workers` tasks in
  flight (semaphore + `stop_event` with timed acquire so it can exit on
  interrupt). Memory stays flat regardless of input size.
- **Collection**: main thread holds an `OrderedDict[AsyncResult, input]`.
  - `unordered=False` (default): process the front entry when ready
    (0.1 s timed `get` so Ctrl+C stays responsive) — results in input order,
    like `builtins.map`.
  - `unordered=True`: scan all in-flight entries each cycle, process ready ones
    in completion order.
- **Progress**: `logger.progress()` task with `total` when known, else
  indeterminate; advances per completed task (success or failure).
- **Failures**: same semantics as `execute()` — logged via
  `logger.bind(is_failure=True).opt(exception=True)` (lands in
  `logs/failures_*.log`), input recorded in `failed_inputs`.
- **Interrupt**: on Ctrl+C, terminate the pool and return the partial
  `RunResult` with `interrupted=True` instead of raising — callers building
  checkpointed pipelines keep the completed subset. (`execute()` still raises.)
- **RunResult** is a dataclass with `__iter__` yielding the legacy
  4-tuple `(successes, failures, failed_inputs, results)` for unpacking compat.
- `execute` / `process_run` / `thread_run` remain unchanged as the raw escape
  hatch. `process_map` / `thread_map` thin wrappers added for symmetry.

### Non-goals

- No executor replacement (keep `multiprocessing.Pool`/`ThreadPool`).
- No distributed execution.
- No retries in this batch (design allows re-submitting `failed_inputs`;
  revisit after map ships).

## Test plan (`tests/test_parallel_map.py`)

- unit: `RunResult` legacy 4-tuple unpack.
- component: thread map, list input, ordered results match input order.
- component: thread map over a generator with `total=None` (the file-style case).
- component: failure collection — `failed_inputs`, failure log content.
- component: `unordered=True` completes.
- component: `starmap` multi-arg worker.
- component: `collect=False` + `post_func` streams without accumulating.
- component: process map basic (spawn-safe, mirrors existing process test).
- component: `on_error="raise"` propagates worker exception.

## Gates

Code change + public API change → `lint`, `fmt`, `typecheck`, full `test`.
Docs updated in same commit: README (new API + B3/B4 env var), CHANGELOG,
STATUS.md, this note.

## Deferred (tracked for later batches)

- **B5 queue-routed file writes**: children send structured records
  (level, name, function, line, message, extra, exception) through the progress
  queue; only the main process writes files. Eliminates the rotation race but
  touches the most deadlock-sensitive code (queue protocol, child sink setup,
  listener) — deserves its own batch with dedicated tests.
- Profiling suite: `@timed` / `profile` CM / per-label stats registry /
  `sys.monitoring` line profiler.
- `parallel.async_run` on `asyncio.TaskGroup` with the same progress/failure
  machinery.
- `parallel.pipeline` (chained stages), rate limiting / backpressure knobs.
