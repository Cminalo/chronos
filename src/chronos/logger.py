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

# Rich Integration
try:
    from rich.logging import RichHandler
    from rich.progress import (
        Progress,
        SpinnerColumn,
        TextColumn,
        BarColumn,
        TaskProgressColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )
    from rich.console import Console
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

if TYPE_CHECKING:
    from loguru import Logger
    from contextlib import AbstractContextManager
    
    # Define a protocol/class for the custom logger to support autocomplete
    class ChronosLogger(Logger):
        def benchmark(self, name: str = "Operation") -> AbstractContextManager[None]: ...
        def memory(self, message: str = "Memory check") -> None: ...
        def progress(self, transient: bool = False) -> "Progress": ...

# 1. Load Environment Variables
load_dotenv()

# 2. Define Constants
LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE_PATH = LOG_DIR / "chronos_{time:YYYY-MM-DD}.log"
JSON_LOG_FILE_PATH = LOG_DIR / "chronos_{time:YYYY-MM-DD}.jsonl"

# 3. Configure Levels & Colors
LOG_LEVELS = [
    {"name": "TRACE", "color": "<dim>"},
    {"name": "DEBUG", "color": "<cyan>"},
    {"name": "INFO", "color": "<white>"},
    {"name": "MEMORY", "no": 22, "color": "<blue>", "icon": "🧠"},
    {"name": "BENCHMARK", "no": 25, "color": "<magenta>", "icon": "⏱️"},
    {"name": "SUCCESS", "color": "<green>"},
    {"name": "WARNING", "color": "<yellow>"},
    {"name": "ERROR", "color": "<red>"},
    {"name": "CRITICAL", "color": "<red><bold>"},
]

# Apply custom levels and colors
for level_config in LOG_LEVELS:
    config = level_config.copy()
    name = config.pop("name")
    try:
        _logger.level(name, **config)
    except TypeError:
        pass

# Global Start Time
if "CHRONOS_START_TIME" not in os.environ:
    os.environ["CHRONOS_START_TIME"] = str(time.perf_counter())

# 4. Custom Formatters
def file_formatter(record: dict) -> str:
    """Format used for text files (standard loguru syntax)"""
    message_format = "{message}"
    if "duration" in record["extra"]:
        global_time = time.perf_counter() - float(os.environ["CHRONOS_START_TIME"])
        message_format = "{message} (Duration: {extra[duration]:.4f}s, Global: " + f"{global_time:.4f}s" + ")"
    if "memory_mb" in record["extra"]:
        message_format = "{message} (RSS: {extra[memory_mb]:.2f} MB)"

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

def rich_formatter(record: dict) -> str:
    """Format used for RichHandler console (simplified because Rich adds time/level/path)"""
    message_format = "{message}"
    if "duration" in record["extra"]:
        global_time = time.perf_counter() - float(os.environ["CHRONOS_START_TIME"])
        message_format = "{message} (Duration: {extra[duration]:.4f}s, Global: " + f"{global_time:.4f}s" + ")"
    if "memory_mb" in record["extra"]:
        message_format = "{message} (RSS: {extra[memory_mb]:.2f} MB)"
        
    ctx_id = f"[ID: {record['extra']['x_id']}] " if "x_id" in record["extra"] else ""
    
    if record["exception"]:
        return ctx_id + message_format + "\n{exception}"
    
    return ctx_id + message_format

# 5. Configure Sinks
_logger.remove()

console_level = os.getenv("LOGGER_LEVEL", "INFO").upper()
use_rich = os.getenv("RICH_CONSOLE", "True").lower() in ("true", "1", "yes")

# Optional Global Rich Console (used so logger and progress share the same buffer)
_rich_console = Console() if RICH_AVAILABLE else None

if RICH_AVAILABLE and use_rich:
    # Rich Console Sink
    _logger.add(
        RichHandler(
            console=_rich_console,
            rich_tracebacks=False, # We want Loguru's native traceback with variables
            show_path=True,
            markup=True,
        ),
        level=console_level,
        format=rich_formatter,
        enqueue=True, # Important for progress bars pushing logs
    )
else:
    # Standard Console Sink
    _logger.add(
        sys.stderr,
        level=console_level,
        format=file_formatter,
        enqueue=True,
        colorize=True,
        backtrace=True,
        diagnose=True,
    )

# File sinks remain unchanged (text & jsonl)
_logger.add(
    LOG_FILE_PATH,
    level="TRACE",
    rotation="00:00",
    retention="10 days",
    compression="zip",
    format=file_formatter,
    enqueue=True,
    backtrace=True,
    diagnose=True,
)

_logger.add(
    JSON_LOG_FILE_PATH,
    level="TRACE",
    rotation="00:00",
    retention="10 days",
    compression="zip",
    serialize=True,
    enqueue=True,
    backtrace=True,
    diagnose=True,
)

# 6. Benchmark Context Manager
@contextmanager
def benchmark(name: str = "Operation") -> Generator[None, None, None]:
    start_time = time.perf_counter()
    try:
        yield
    finally:
        end_time = time.perf_counter()
        duration = end_time - start_time
        _logger.bind(duration=duration).opt(depth=2).log("BENCHMARK", f"{name} finished")

# 7. Memory Profiling Helper
def memory(message: str = "Memory check"):
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    rss_mb = mem_info.rss / (1024 * 1024)
    _logger.bind(memory_mb=rss_mb).opt(depth=1).log("MEMORY", message)

# 8. Rich Progress Manager
def progress(transient: bool = False):
    """
    Returns a rich.progress.Progress manager integrated with Loguru.
    Logs will naturally flow above the progress bars.
    """
    if not RICH_AVAILABLE:
        raise ImportError("The 'rich' library is required to use progress bars. Install with: pip install rich")
        
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=_rich_console, # Must share the same console instance as the RichHandler
        transient=transient,
    )

# 9. Global Exception Hook
def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    process_name = multiprocessing.current_process().name
    process_id = os.getpid()
    thread_name = threading.current_thread().name
    thread_id = threading.get_ident()

    tb = exc_traceback
    while tb.tb_next:
        tb = tb.tb_next
    function_name = tb.tb_frame.f_code.co_name

    msg = (
        f"An unhandled exception occurred in function '{function_name}', "
        f"process '{process_name}' ({process_id}), thread '{thread_name}' ({thread_id}):"
    )

    _logger.opt(exception=(exc_type, exc_value, exc_traceback)).critical(msg)

sys.excepthook = handle_exception

# Attach methods
setattr(_logger, "benchmark", benchmark)
setattr(_logger, "memory", memory)
setattr(_logger, "progress", progress)

logger = cast("ChronosLogger", _logger)
__all__ = ["logger"]
