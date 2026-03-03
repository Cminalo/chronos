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
import logging
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
        MofNCompleteColumn,
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
        def intercept_standard_logging(self) -> None: ...
        def enable_system_metrics(self) -> None: ...
        def get_progress_queue(self) -> multiprocessing.Queue: ...
        def set_progress_queue(self, queue: multiprocessing.Queue) -> None: ...

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
    if "cpu_pct" in record["extra"]:
        ctx_id += f" [CPU: {record['extra']['cpu_pct']}%|Thr: {record['extra']['thread_cnt']}]"

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
    if "cpu_pct" in record["extra"]:
        ctx_id += f"[CPU: {record['extra']['cpu_pct']}%|Thr: {record['extra']['thread_cnt']}] "
    
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
class RemoteProgress:
    """
    A proxy for the rich.progress.Progress object that can be used in child processes.
    It sends updates via a multiprocessing Queue to the main process.
    """
    def __init__(self, queue: multiprocessing.Queue):
        self._queue = queue

    def add_task(self, description: str, total: float = 100.0, **kwargs) -> int:
        # Create a unique ID for this task across processes
        task_id = id(description) + int(time.time() * 1000)
        self._queue.put(("add", task_id, description, total, kwargs))
        return task_id

    def update(self, task_id: int, advance: float = 0, **kwargs):
        self._queue.put(("update", task_id, advance, kwargs))

    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): pass

def _progress_listener(queue: multiprocessing.Queue, progress_instance: "Progress"):
    """Background thread in the main process that listens for progress updates."""
    tasks = {}
    with progress_instance:
        while True:
            msg = queue.get()
            if msg is None: # Sentinel for shutdown
                break
            
            action = msg[0]
            if action == "add":
                _, tid, desc, total, kwargs = msg
                tasks[tid] = progress_instance.add_task(desc, total=total, **kwargs)
            elif action == "update":
                _, tid, advance, kwargs = msg
                if tid in tasks:
                    progress_instance.update(tasks[tid], advance=advance, **kwargs)

_PROGRESS_QUEUE = None
_PROGRESS_LISTENER_THREAD = None

def progress(transient: bool = False):
    """
    Returns a progress manager. 
    In the Main Process: Returns a real Rich Progress and starts a listener for child updates.
    In Child Processes: Returns a RemoteProgress proxy if logger.progress_queue is set.
    """
    global _PROGRESS_QUEUE, _PROGRESS_LISTENER_THREAD
    
    if not RICH_AVAILABLE:
        raise ImportError("The 'rich' library is required. Install with: pip install rich")

    # If we are in a child process and have a queue, return a proxy
    if multiprocessing.current_process().name != "MainProcess" and _PROGRESS_QUEUE:
        return RemoteProgress(_PROGRESS_QUEUE)
        
    # In Main Process, create a real Progress
    p = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=_rich_console,
        transient=transient,
    )

    # Start the listener thread if we want to support child updates
    if _PROGRESS_QUEUE is None:
        _PROGRESS_QUEUE = multiprocessing.Queue()
        
    _PROGRESS_LISTENER_THREAD = threading.Thread(
        target=_progress_listener, 
        args=(_PROGRESS_QUEUE, p),
        daemon=True
    )
    _PROGRESS_LISTENER_THREAD.start()
    
    return p

def set_progress_queue(queue: multiprocessing.Queue):
    """Set the queue used for remote progress updates (call this in child processes)."""
    global _PROGRESS_QUEUE
    _PROGRESS_QUEUE = queue

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

# 10. Standard Logging Interceptor
class InterceptHandler(logging.Handler):
    """
    Intercepts standard logging messages and routes them to Loguru.
    """
    def emit(self, record: logging.LogRecord):
        # Get corresponding Loguru level if it exists.
        try:
            level = _logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            if frame.f_back:
                frame = frame.f_back
            depth += 1

        _logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

def intercept_standard_logging():
    """
    Routes all standard Python 'logging' calls through Chronos.
    Call this once at the start of your application.
    """
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

# 11. Sticky System Metrics
def enable_system_metrics():
    """
    Patches the logger to include CPU and Thread count in every log's 'extra' dict.
    Useful for high-density debugging.
    """
    def patcher(record):
        record["extra"]["cpu_pct"] = psutil.cpu_percent()
        record["extra"]["thread_cnt"] = threading.active_count()
    
    _logger.configure(patcher=patcher)

def get_progress_queue():
    """Returns the current progress queue to be passed to child processes."""
    global _PROGRESS_QUEUE
    if _PROGRESS_QUEUE is None:
        _PROGRESS_QUEUE = multiprocessing.Queue()
    return _PROGRESS_QUEUE

sys.excepthook = handle_exception

# Attach methods
setattr(_logger, "benchmark", benchmark)
setattr(_logger, "memory", memory)
setattr(_logger, "progress", progress)
setattr(_logger, "intercept_standard_logging", intercept_standard_logging)
setattr(_logger, "enable_system_metrics", enable_system_metrics)
setattr(_logger, "get_progress_queue", get_progress_queue)
setattr(_logger, "set_progress_queue", set_progress_queue)

logger = cast("ChronosLogger", _logger)
__all__ = ["logger"]
