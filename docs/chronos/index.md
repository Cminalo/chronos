# Chronos

A high-performance, developer-friendly logging and parallel execution suite for Python, built on top of Loguru and Rich.

## Overview

Chronos provides professional-grade observability and concurrency tools with zero boilerplate. It ensures your terminal remains clean, your logs remain detailed, and your parallel tasks remain traceable.

Key capabilities:
- Rich terminal UI with syntax-highlighted, scrollable log output
- Tree-style tracebacks with variable inspection
- Global error interception across processes and threads
- Parallel execution with unified UI, task recovery, and fork-bomb protection
- Dual routing: console, text file, JSONL, and targeted failure logs
- Memory profiling and sticky system metrics

## Installation

```bash
pip install chronos-logger
```

## Quick Start

```python
from chronos import logger, parallel

def my_worker(data):
    if data == 13:
        raise ValueError("Unlucky!")
    return data * 2

def my_prep(pool):
    inputs = range(20)
    return [(i, pool.apply_async(my_worker, (i,))) for i in inputs]

def main():
    s, f, failed_inputs = parallel.process_run(
        prep_func=my_prep,
        post_func=lambda r: logger.info(f"Result: {r}"),
        desc="Crunching Numbers",
        total=20
    )
    if failed_inputs:
        logger.error(f"Failed to process: {failed_inputs}")
    logger.summary("Daily Pipeline", success_count=s, failure_count=f)

if __name__ == "__main__":
    main()
```

See [README.md](../../README.md) for full examples and API reference.

## Configuration

Environment variables (via `.env`):

| Variable | Default | Description |
|---|---|---|
| `LOGGER_LEVEL` | `INFO` | Console verbosity (TRACE, DEBUG, INFO, SUCCESS, WARNING, ERROR, CRITICAL) |
| `RICH_CONSOLE` | `True` | Toggle the Rich Terminal UI |
| `CHRONOS_DIAGNOSE` | `False` | Interpolate live variable values into traceback logs — development only, values may include secrets |

Log files rotate daily at midnight and are retained for 7 days. Console records render as compact single lines with an IDE-clickable `path:line` suffix; file logs keep full timestamps and process/thread context.

## Development

```bash
# Install dependencies
pixi install

# Run tests
pixi run test

# Lint and format
pixi run lint
pixi run fmt

# Type check
pixi run typecheck

# Build wheel
pixi run build
```

## Contributing

This project uses pixi for dependency management, ruff for linting/formatting, mypy for type checking, and pytest for testing. Ensure all four gates pass before committing.
