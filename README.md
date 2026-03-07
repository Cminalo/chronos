# Chronos

A high-performance, developer-friendly logging and parallel execution suite for Python 3.13, built on top of [Loguru](https://github.com/Delgan/loguru) and [Rich](https://github.com/Textualize/rich).

Chronos is designed to provide professional-grade observability and concurrency tools with zero boilerplate. It ensures that your terminal remains clean, your logs remain detailed, and your parallel tasks remain traceable.

## Features

### 🚀 Advanced Logging
- **Rich Terminal UI**: Beautiful, scrollable log output with syntax highlighting and fixed-width columns.
- **Tree-Style Tracebacks**: Native Loguru tracebacks showing exact variable values at the time of crash, even within the Rich UI.
- **Global Error Interception**: Automatically catches and logs all unhandled exceptions with full process and thread context.
- **Standard Interception**: Routes logs from third-party libraries (like `requests`, `boto3`, or `openai`) into the Chronos UI.
- **Log Silencing**: Easily silence noisy third-party modules with `logger.silence("noisy_module")`.
- **Memory Profiling**: Dedicated `MEMORY` level and `.memory()` helper to track Resident Set Size (RSS) usage.
- **Sticky System Metrics**: Optional patching to attach CPU % and Active Thread count to every log line.

### ⚡ Parallel Execution
- **Unified UI**: Logs from worker processes/threads elegantly "leapfrog" over active progress bars without terminal tearing.
- **Task Recovery**: Automatically tracks and returns the exact inputs that failed during parallel runs.
- **Dedicated Failure Logs**: Captured errors are automatically saved to `logs/failures_{date}.log` for immediate debugging.
- **Graceful Fork Bomb Protection**: Safely intercepts infinite recursive spawning loops on macOS/Windows without abruptly killing debuggers or crashing the parent process.
- **Multiple Returns**: Workers can return `None`, single values, or tuples containing multiple datatypes, which are cleanly passed directly into your `post_func`.
- **Memory Safe**: Built using `multiprocessing.Pool` and `ThreadPool` for efficient resource reclamation.

### 📊 Professional Observability
- **Dual Routing**: Console (filtered) + Text File (all) + JSONL (serialized) + Failures (targeted).
- **Execution Summaries**: Generate beautiful end-of-run reports with `logger.summary()`.
- **Automated Maintenance**: Daily rotation, zipping, and 10-day retention for all log files.

---

## Installation

```bash
pip install chronos-logger
```

## Quick Start

### 1. Parallel Execution & Task Recovery

Chronos makes it easy to run massive jobs while perfectly maintaining your UI and tracking failures.

```python
from chronos import logger, parallel

def my_worker(data):
    if data == 13: raise ValueError("Unlucky!")
    return data * 2

def my_prep(pool):
    # Format: [(input, async_result), ...] enables Task Recovery
    inputs = range(20)
    return [(i, pool.apply_async(my_worker, (i,))) for i in inputs]

def main():
    # Returns successes, failures, and the exact list of failed inputs
    s, f, failed_inputs = parallel.process_run(
        prep_func=my_prep,
        post_func=lambda r: logger.info(f"Result: {r}"),
        desc="Crunching Numbers",
        total=20
    )

    if failed_inputs:
        logger.error(f"Failed to process: {failed_inputs}")

    # Generate a professional report
    logger.summary("Daily Pipeline", success_count=s, failure_count=f)

if __name__ == "__main__":
    main()
```

### 2. Multi-Process Coordination
If you are writing custom multiprocessing code, use the coordination queue to keep your terminal clean.

```python
import multiprocessing
from chronos import logger

def worker(queue):
    # Redirect child logs and progress to the main process
    logger.set_progress_queue(queue)
    
    with logger.progress() as p:
        task = p.add_task("Agent Task", total=100)
        logger.info("Logs will jump OVER the progress bar!")
        p.update(task, advance=50)

if __name__ == "__main__":
    queue = logger.get_progress_queue()
    proc = multiprocessing.Process(target=worker, args=(queue,))
    proc.start()
    proc.join()
```

### 3. Basic Utility Logging

```python
from chronos import logger

# Log with custom levels
logger.info("System ready")
logger.success("Operation complete")
logger.memory("Check RAM usage")

# Automated benchmarking
with logger.benchmark("Model Inference"):
    run_inference()

# Silence noisy libraries
logger.silence("urllib3")
logger.intercept_standard_logging()
```

---

## Configuration

Chronos looks for these variables in your `.env` file:

```ini
# Control console verbosity (TRACE, DEBUG, INFO, SUCCESS, WARNING, ERROR, CRITICAL)
LOGGER_LEVEL=INFO

# Toggle the Rich Terminal UI (Default: True)
RICH_CONSOLE=True
```

---

## Development

```bash
# Run the comprehensive test suite
pixi run pytest tests/test_comprehensive.py

# Build the package
python -m build
```

## License
MIT
