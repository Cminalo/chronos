# Chronos

A high-performance, developer-friendly logging wrapper for Python 3.13, built on top of [Loguru](https://github.com/Delgan/loguru).

## Features

- **Rich Terminal UI**: Beautiful, scrollable log output with syntax highlighting, fixed-width columns, and clickable file paths via the [Rich](https://rich.readthedocs.io/) library.
- **Progress Bar Integration**: A built-in `logger.progress()` manager that allows logs to seamlessly flow *above* active progress bars without breaking your terminal layout.
- **Standard Logging Interception**: Automatically routes logs from third-party libraries (like `requests` or `boto3`) into your beautiful Chronos UI.
- **Rich Tracebacks**: Automatic detailed tracebacks showing variable values at the time of crash.
- **Global Error Interception**: Automatically catches and logs all unhandled exceptions with full context (function name, process, and thread details).
- **Benchmarking (Global & Local)**: Built-in context manager to measure execution time, including a "Global Time" tracker that synchronizes across spawned multiprocessing agents.
- **Sticky System Metrics**: Automatically attach CPU % and Active Thread count to every log line for high-density debugging.
- **Memory Profiling**: A dedicated `MEMORY` level and `.memory()` helper to track Resident Set Size (RSS) RAM usage.
- **Contextual IDs**: Easily tag logs with specific agent or session IDs (`x_id`) across threads and processes for clean log filtering.
- **Dual Routing & Serialization**:
  - **Console**: Configurable verbosity via `.env` (INFO, DEBUG, TRACE, etc.), cleanly formatted with Process/Thread IDs.
  - **File (Plain Text)**: Always logs everything to timestamped files in `logs/` for human reading.
  - **File (JSONL)**: Always logs everything to serialized `.jsonl` files for machine parsing (Datadog, ELK, etc.).
- **Execution Summaries**: Generate professional end-of-run reports with `logger.summary()`, showing log level breakdowns, success/failure counts, and system performance.
- **Log Filtering**: Easily silence noisy third-party libraries with `logger.silence("noisy_lib")`.
- **Task Recovery**: Parallel execution automatically tracks and returns the exact inputs that failed, and logs them to a dedicated `logs/failures_{date}.log` file.
- **Automated Rotation**: All log files (standard, JSONL, and failures) are rotated daily, zipped to save space, and retained for 10 days by default.
- **Process & Thread Safe**: Fully compatible with multiprocessing and multithreaded applications.

## Installation

```bash
pip install chronos-logger
```

## Quick Start

### Parallel Execution & Task Recovery

Chronos includes a powerful `parallel` module built to run massive jobs while perfectly maintaining your beautiful progress bars and memory safety. It also tracks exactly which inputs failed for easy re-runs.

```python
from chronos import logger, parallel

def my_worker(data_chunk):
    if data_chunk == 13: raise ValueError("Unlucky!")
    return data_chunk * 2

def my_prep(pool):
    # To enable task recovery, return tuples of (input, async_result)
    data = range(20)
    return [(i, pool.apply_async(my_worker, (i,))) for i in data]

# Returns success count, failure count, AND the list of failed inputs
success, failure, failed_inputs = parallel.process_run(
    prep_func=my_prep,
    post_func=lambda r: None,
    desc="Processing Data",
    total=20
)

if failed_inputs:
    logger.error(f"The following inputs failed: {failed_inputs}")
    # These are also saved in logs/failures_{date}.log with full tracebacks!

logger.summary("Execution Results", success_count=success, failure_count=failure)
```

### Basic Logging & Silencing

```python
from chronos import logger

logger.info("Application started")

# Silence noisy third-party logs (e.g., from 'urllib3' or 'openai')
logger.silence("urllib3", "boto3")
logger.intercept_standard_logging()
```

```python
from chronos import logger

logger.info("Application started")
logger.debug("This is a hidden debug message")
logger.success("Task completed successfully!")

# Check process memory
logger.memory("Checking RAM usage mid-task")
```

### System Metrics & Interception (Advanced)

If you want to track hardware usage on every log line, or capture logs from other libraries:

```python
from chronos import logger

# 1. Adds CPU % and Thread Count to every log's metadata
logger.enable_system_metrics()

# 2. Captures all standard python `logging` (e.g., from 'requests' or 'openai')
logger.intercept_standard_logging()

import logging
logging.warning("This standard warning will now look like a Chronos log!")
```

### Progress Bars (Rich Integration)

Log messages will beautifully scroll past your progress bars without breaking the layout. The bars stack at the bottom of the screen and disappear when the `with` block finishes.

```python
with logger.progress(transient=True) as p:
    main_task = p.add_task("[green]Total Pipeline", total=100)
    
    for i in range(5):
        # Stacks below the main task dynamically
        sub_task = p.add_task(f"[cyan]Agent {i} processing", total=20)
        
        # ... do work ...
        logger.info(f"Agent {i} completed a chunk!") # Scrolls above the bars
```

**Multi-Process Coordination (Proxy Progress & Unified Logging):**
If you are spawning child processes, you can seamlessly route their progress bars **and logs** back to the main terminal window. This prevents "terminal tearing" where logs and bars overwrite each other.

```python
import multiprocessing
from chronos import logger

def worker(queue):
    # This automatically redirects logs and progress to the main process
    logger.set_progress_queue(queue)
    
    with logger.progress() as p:
        task = p.add_task("Agent working...", total=100)
        logger.info("This log will elegantly jump over the progress bar!")
        p.update(task, advance=50)

if __name__ == "__main__":
    queue = logger.get_progress_queue()
    with logger.progress() as p:
        proc = multiprocessing.Process(target=worker, args=(queue,))
        proc.start()
        proc.join()
```

### Benchmarking (with Global Time)

Use the `.benchmark()` context manager to track performance. It will show the duration of the block, and the "Global Time" since the first process imported the logger.

```python
with logger.benchmark("Data Processing"):
    # Your heavy computation here
    result = perform_complex_task()
```

### Contextual IDs (For Multi-Agent tracking)

If you are running multiple processes or agents, you can bind an ID to a specific block of code using `contextualize`. The ID will automatically appear in your console and JSON logs.

```python
with logger.contextualize(x_id="Agent-007"):
    logger.info("Starting up")
    # All logs in this block, including deep nested function calls,
    # will be tagged with [ID: Agent-007].
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
