# Architecture

Chronos is a library-only package (`src/chronos/`) with two modules:

- `logger.py` — Loguru-based logging configuration: custom levels, Rich console
  sink, file/JSONL/failure sinks, standard-library interception, memory and
  system metrics.
- `parallel.py` — thread/process pool execution with unified Rich progress UI,
  task recovery, and failure logs.

## Design decisions

- **Loguru + Rich**: Loguru owns routing and levels; Rich renders the console
  sink so progress bars and logs share one buffer without terminal tearing.
- **Optional Rich**: Rich imports are guarded; `RICH_AVAILABLE` gates the
  console sink and `_rich_console` is `None` when Rich is absent.
- **Sinks**: console (filtered), text file (all), JSONL (serialized), failures
  (targeted) — each daily-rotated, zipped, with 7-day retention.
- **Dependency direction**: `parallel.py` consumes the configured logger;
  `logger.py` does not import `parallel.py`.
