# Chronos

A high-performance, developer-friendly logging wrapper for Python 3.13, built on top of [Loguru](https://github.com/Delgan/loguru).

## Features

- **Rich Tracebacks**: Automatic detailed tracebacks showing variable values at the time of crash.
- **Global Error Interception**: Automatically catches and logs all unhandled exceptions with full context (function name, process, and thread details).
- **Benchmarking**: Built-in context manager to measure and log execution time with high precision.
- **Dual Routing**:
  - **Console**: Configurable verbosity via `.env` (INFO, DEBUG, TRACE, etc.).
  - **File**: Always logs everything to timestamped files in `logs/` for post-mortem analysis.
- **Modern Tech Stack**: Optimized for Python 3.13 and Apple Silicon (M-series).
- **Process & Thread Safe**: Fully compatible with multiprocessing and multithreaded applications.

## Installation

```bash
pip install chronos-logger
```

## Quick Start

### Basic Logging

```python
from chronos import logger

logger.info("Application started")
logger.debug("This is a hidden debug message")
logger.success("Task completed successfully!")
```

### Benchmarking

Use the `.benchmark()` context manager to track performance:

```python
with logger.benchmark("Data Processing"):
    # Your heavy computation here
    result = perform_complex_task()
```

### Catching Exceptions

Chronos automatically handles unexpected crashes. For manual catching while keeping the program alive:

```python
try:
    1 / 0
except ZeroDivisionError:
    logger.exception("Caught an error, but the program continues.")
```

## Configuration

Chronos looks for a `LOGGER_LEVEL` variable in your `.env` file to control console output:

```ini
# .env
LOGGER_LEVEL=DEBUG
```

Available levels: `TRACE`, `DEBUG`, `INFO`, `BENCHMARK`, `SUCCESS`, `WARNING`, `ERROR`, `CRITICAL`.

## Development

This project uses [Pixi](https://pixi.sh) for dependency management.

```bash
# Run tests
pixi run pytest tests/

# Build package
hatch build
```

## License

MIT
