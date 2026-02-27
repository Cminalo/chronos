"""
Chronos Logger Configuration

This module provides a configured Loguru logger instance.
It supports:
- Custom levels (BENCHMARK)
- Automatic log rotation (daily)
- Environment-based configuration (.env)
- Rich tracebacks
- Multiprocessing/threading support (enqueue=True)
- Custom formatting
"""

import sys
import os
import time
import threading
import multiprocessing
import psutil
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, TYPE_CHECKING, cast

from dotenv import load_dotenv
from loguru import logger as _logger

if TYPE_CHECKING:
    from loguru import Logger
    from contextlib import AbstractContextManager

    # Define a protocol/class for the custom logger to support autocomplete
    class ChronosLogger(Logger):
        def benchmark(self, name: str = "Operation") -> AbstractContextManager[None]: ...
        def memory(self, message: str = "Memory check") -> None: ...

# 1. Load Environment Variables
load_dotenv()

# 2. Define Constants
LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE_PATH = LOG_DIR / "chronos_{time:YYYY-MM-DD}.log"
JSON_LOG_FILE_PATH = LOG_DIR / "chronos_{time:YYYY-MM-DD}.jsonl"

# 3. Configure Levels & Colors
# We define specific colors for each level to make them visually distinct.
# The <level> tag in the formatter will use these colors.
LOG_LEVELS = [
    {"name": "TRACE", "color": "<dim>"},              # Gray/Dim
    {"name": "DEBUG", "color": "<cyan>"},             # Cyan
    {"name": "INFO", "color": "<white>"},             # White
    {"name": "MEMORY", "no": 22, "color": "<blue>", "icon": "🧠"}, # Blue
    {"name": "BENCHMARK", "no": 25, "color": "<magenta>", "icon": "⏱️"}, # Magenta
    {"name": "SUCCESS", "color": "<green>"},          # Green
    {"name": "WARNING", "color": "<yellow>"},         # Yellow
    {"name": "ERROR", "color": "<red>"},              # Red
    {"name": "CRITICAL", "color": "<red><bold>"},     # Bold Red
]

# Apply custom levels and colors
for level_config in LOG_LEVELS:
    config = level_config.copy()
    name = config.pop("name")
    try:
        # Update existing level or add new one
        _logger.level(name, **config)
    except TypeError:
        # Fallback if arguments mismatch (shouldn't happen with correct usage)
        pass

# Global Start Time for cross-process benchmarking
# Using environment variables ensures spawned processes inherit the same start time
if "CHRONOS_START_TIME" not in os.environ:
    os.environ["CHRONOS_START_TIME"] = str(time.perf_counter())

# 4. Custom Formatter
def formatter(record: dict) -> str:
    """
    Custom logging format to match requirements.
    
    Format:
    [TIMESTAMP] | LEVEL | [PID:TID] | "Message" | module -> function -> line
    """
    message_format = "{message}"
    
    # Check if there's a benchmark duration in extra
    if "duration" in record["extra"]:
        global_time = time.perf_counter() - float(os.environ["CHRONOS_START_TIME"])
        message_format = "{message} (Duration: {extra[duration]:.4f}s, Global: " + f"{global_time:.4f}s" + ")"
        
    # Check if memory info is in extra
    if "memory_mb" in record["extra"]:
        message_format = "{message} (RSS: {extra[memory_mb]:.2f} MB)"

    # Add Contextual IDs if provided (for multi-agent tracking)
    ctx_id = f" [ID: {record['extra']['x_id']}]" if "x_id" in record["extra"] else ""

    return (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <9}</level> | "
        "<dim>[P:{process.id}|T:{thread.id}]</dim>" + ctx_id + " | "
        f"\"{message_format}\" | "
        "<cyan>{name}</cyan> -> "
        "<cyan>{function}</cyan> -> "
        "<cyan>{line}</cyan>\n{exception}"
    )

# 5. Configure Sinks
# Remove default handler
_logger.remove()

# Get console level from env, default to INFO
# Users can set LOGGER_LEVEL=TRACE in .env to see everything in console
console_level = os.getenv("LOGGER_LEVEL", "INFO").upper()

# Sink 1: Console (stderr)
_logger.add(
    sys.stderr,
    level=console_level,
    format=formatter,
    enqueue=True,       # Thread/Process safe
    colorize=True,
    backtrace=True,     # Extended traceback
    diagnose=True,      # Show variable values in traceback
)

# Sink 2: File (logs/chronos_DATE.log)
# Always log everything (TRACE/DEBUG) to file as requested
_logger.add(
    LOG_FILE_PATH,
    level="TRACE",      # Capture everything regardless of console level
    rotation="00:00",   # New file at midnight
    retention="10 days",# Keep logs for 10 days
    compression="zip",  # Compress old logs
    format=formatter,
    enqueue=True,       # Thread/Process safe
    backtrace=True,
    diagnose=True,
)

# Sink 3: JSON File (logs/chronos_DATE.jsonl)
# Serialize logs to JSON Lines for ELK/Datadog/Machine parsing
_logger.add(
    JSON_LOG_FILE_PATH,
    level="TRACE",
    rotation="00:00",
    retention="10 days",
    compression="zip",
    serialize=True,     # <--- Enables JSON serialization
    enqueue=True,
    backtrace=True,
    diagnose=True,
)

# 6. Benchmark Context Manager
@contextmanager
def benchmark(name: str = "Operation") -> Generator[None, None, None]:
    """
    Context manager to measure and log execution time.
    
    Usage:
        from chronos.logger import logger
        
        with logger.benchmark("Data Processing"):
            process_data()
    """
    start_time = time.perf_counter()
    try:
        yield
    finally:
        end_time = time.perf_counter()
        duration = end_time - start_time
        # Log with BENCHMARK level and attach duration to extra dict
        # depth=2 ensures the log location points to the caller of the context manager
        _logger.bind(duration=duration).opt(depth=2).log("BENCHMARK", f"{name} finished")

# 7. Memory Profiling Helper
def memory(message: str = "Memory check"):
    """
    Logs the current process Resident Set Size (RSS) memory usage in MB.
    """
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    # Convert bytes to Megabytes
    rss_mb = mem_info.rss / (1024 * 1024)
    _logger.bind(memory_mb=rss_mb).opt(depth=1).log("MEMORY", message)

# 8. Global Exception Hook
def handle_exception(exc_type, exc_value, exc_traceback):
    """
    Intersects unhandled exceptions and logs them via loguru.
    Does not log KeyboardInterrupt to allow clean Ctrl+C exits.
    """
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    # Get execution context
    process_name = multiprocessing.current_process().name
    process_id = os.getpid()
    thread_name = threading.current_thread().name
    thread_id = threading.get_ident()

    # Find the function name where the exception occurred (innermost frame)
    tb = exc_traceback
    while tb.tb_next:
        tb = tb.tb_next
    function_name = tb.tb_frame.f_code.co_name

    msg = (
        f"An unhandled exception occurred in function '{function_name}', "
        f"process '{process_name}' ({process_id}), thread '{thread_name}' ({thread_id}):"
    )

    # Use .opt(exception=...) to ensure the rich traceback is generated
    _logger.opt(exception=(exc_type, exc_value, exc_traceback)).critical(msg)

# Install the hook
sys.excepthook = handle_exception

# Attach methods to logger instance for easy access
# We treat _logger as a mutable object here to attach the methods
setattr(_logger, "benchmark", benchmark)
setattr(_logger, "memory", memory)

# Cast the logger to our custom type so IDEs recognize .benchmark() and .memory()
logger = cast("ChronosLogger", _logger)

# Export the configured logger
__all__ = ["logger"]
