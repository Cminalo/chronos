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
    from rich.panel import Panel
    from rich.table import Table
    from rich.columns import Columns
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
        def progress(self, transient: bool = False) -> AbstractContextManager["Progress"]: ...
        def intercept_standard_logging(self) -> None: ...
        def enable_system_metrics(self) -> None: ...
        def get_progress_queue(self) -> multiprocessing.Queue: ...
        def set_progress_queue(self, queue: multiprocessing.Queue) -> None: ...
        def reset_progress_queue(self) -> None: ...
        def summary(self, title: str = "Execution Summary", success_count: int | None = None, failure_count: int | None = None) -> None: ...
        def silence(self, *module_names: str) -> None: ...

# 1. Load Environment Variables
load_dotenv()

# 2. Define Constants
LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE_PATH = LOG_DIR / "chronos_{time:YYYY-MM-DD}.log"
JSON_LOG_FILE_PATH = LOG_DIR / "chronos_{time:YYYY-MM-DD}.jsonl"
FAILURES_LOG_FILE_PATH = LOG_DIR / "failures_{time:YYYY-MM-DD}.log"

# Global Silenced Modules (for standard logging interception)
_SILENCED_MODULES = set()

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

# Apply custom levels and colors (only once per process)
if not getattr(_logger, "_chronos_levels_configured", False):
    for level_config in LOG_LEVELS:
        config = level_config.copy()
        name = config.pop("name")
        
        try:
            # Check if the level exists by attempting to retrieve it
            _logger.level(name)
            # If it exists, Loguru doesn't allow changing the level number 'no'.
            config.pop("no", None)
        except ValueError:
            pass # Level doesn't exist yet

        try:
            _logger.level(name, **config)
        except (TypeError, ValueError):
            pass

    # Mark as configured so re-imports don't trigger redeclarations
    setattr(_logger, "_chronos_levels_configured", True)

# Global Stats Tracking
_LOG_COUNTS = {level["name"]: 0 for level in LOG_LEVELS}
_LOG_COUNTS["EXCEPTION"] = 0 # Track logger.exception calls

_PATCHERS = []

def _master_patcher(record):
    """Executes all registered patchers exactly once per record."""
    # Internal: Track stats
    level_name = record["level"].name
    if level_name in _LOG_COUNTS:
        _LOG_COUNTS[level_name] += 1
    if record["exception"]:
        _LOG_COUNTS["EXCEPTION"] += 1
    
    # User registered patchers
    for patch_func in _PATCHERS:
        patch_func(record)

# Configure the global master patcher
_logger.configure(patcher=_master_patcher)

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

# 5. Configure Sinks
_logger.remove()

console_level = os.getenv("LOGGER_LEVEL", "INFO").upper()
use_rich = os.getenv("RICH_CONSOLE", "True").lower() in ("true", "1", "yes")

# Optional Global Rich Console (used so logger and progress share the same buffer)
# We must explicitly set file=sys.stderr so it perfectly synchronizes with Loguru's output stream.
_rich_console = Console(file=sys.stderr) if RICH_AVAILABLE else None

def rich_console_sink(message):
    """Custom sink that forces Loguru to use Rich's print, preventing progress bar tearing."""
    _rich_console.print(message, end="", markup=False, highlight=False)

if RICH_AVAILABLE and use_rich:
    # Rich Console Sink
    _logger.add(
        rich_console_sink,
        level=console_level,
        format=file_formatter,
        colorize=True,
        enqueue=False, # Must be False to prevent background thread terminal tearing with Progress bars
        backtrace=True,
        diagnose=True,
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

# Sink 4: Failures Log (logs/failures_DATE.log)
# Only captures logs explicitly marked as failures (e.g. from parallel.execute)
_logger.add(
    FAILURES_LOG_FILE_PATH,
    level="ERROR",
    filter=lambda record: record["extra"].get("is_failure", False),
    rotation="00:00",
    retention="10 days",
    compression="zip",
    format=file_formatter,
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

# 8. Rich Progress & Log Proxy Manager
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
        self._queue.put(("progress", "add", task_id, description, total, kwargs))
        return task_id

    def update(self, task_id: int, advance: float = 0, **kwargs):
        self._queue.put(("progress", "update", task_id, advance, kwargs))

    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): pass

_PROGRESS_QUEUE = None
_LISTENER_THREAD = None
_LISTENER_LOCK = threading.Lock()
_ACTIVE_PROGRESS = None # Tracks the currently active Rich progress instance

def _main_listener(queue: multiprocessing.Queue):
    """Background thread in the main process that listens for progress AND log updates."""
    tasks = {}
    while True:
        try:
            # We use a timeout to ensure the thread is periodically wakeable 
            # and doesn't get stuck if the queue is suddenly closed.
            msg = queue.get(timeout=0.1)
        except (ValueError, EOFError, OSError, TypeError):
            break
        except Exception: # Empty queue timeout
            continue
        
        if msg is None: # Sentinel for shutdown
            break
        
        # Always route to the currently active progress instance
        p = _ACTIVE_PROGRESS
        if p is None and msg[0] == "progress":
            continue

        category = msg[0]
        
        if category == "progress":
            action = msg[1]
            if action == "add":
                _, _, tid, desc, total, kwargs = msg
                tasks[tid] = p.add_task(desc, total=total, **kwargs)
            elif action == "update":
                _, _, tid, advance, kwargs = msg
                if tid in tasks:
                    p.update(tasks[tid], advance=advance, **kwargs)
        
        elif category == "log":
            # Instead of printing directly, we log it raw. 
            # This ensures it goes through the main thread's logging synchronization.
            _, formatted_msg = msg
            _logger.opt(raw=True).info(formatted_msg)

@contextmanager
def progress(transient: bool = False) -> Generator[Progress, None, None]:
    """
    Returns a progress manager context manager. 
    In the Main Process: Manages a real Rich Progress and the listener thread.
    In Child Processes: Returns a RemoteProgress proxy.
    """
    global _PROGRESS_QUEUE, _LISTENER_THREAD, _ACTIVE_PROGRESS
    
    if not RICH_AVAILABLE:
        raise ImportError("The 'rich' library is required. Install with: pip install rich")

    # If we are in a child process and have a queue, yield a proxy
    if multiprocessing.current_process().name != "MainProcess" and _PROGRESS_QUEUE:
        yield cast(Progress, RemoteProgress(_PROGRESS_QUEUE))
        return
        
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
    
    _ACTIVE_PROGRESS = p

    with _LISTENER_LOCK:
        if _PROGRESS_QUEUE is None:
            _PROGRESS_QUEUE = multiprocessing.Queue()
            
        if _LISTENER_THREAD is None or not _LISTENER_THREAD.is_alive():
            _LISTENER_THREAD = threading.Thread(
                target=_main_listener, 
                args=(_PROGRESS_QUEUE,),
                daemon=True
            )
            _LISTENER_THREAD.start()
    
    try:
        with p:
            yield p
    finally:
        _ACTIVE_PROGRESS = None

def set_progress_queue(queue: multiprocessing.Queue):
    """Set the queue used for remote progress updates (call this in child processes)."""
    global _PROGRESS_QUEUE
    _PROGRESS_QUEUE = queue
    
    if multiprocessing.current_process().name == "MainProcess":
        return
        
    _logger.remove()
    _logger.add(LOG_FILE_PATH, level="TRACE", rotation="00:00", retention="10 days", compression="zip", format=file_formatter, enqueue=True)
    _logger.add(JSON_LOG_FILE_PATH, level="TRACE", rotation="00:00", retention="10 days", compression="zip", serialize=True, enqueue=True)
    
    def proxy_sink(message):
        try:
            queue.put(("log", message))
        except (ValueError, EOFError, BrokenPipeError):
            pass 
        
    _logger.add(proxy_sink, level=os.getenv("LOGGER_LEVEL", "INFO").upper(), format=file_formatter, colorize=True)

def reset_progress_queue():
    """Shuts down and clears the global progress queue state."""
    global _PROGRESS_QUEUE, _LISTENER_THREAD, _ACTIVE_PROGRESS
    _ACTIVE_PROGRESS = None
    with _LISTENER_LOCK:
        if _PROGRESS_QUEUE is not None:
            try:
                _PROGRESS_QUEUE.put(None)
                _PROGRESS_QUEUE.close()
            except (ValueError, EOFError, BrokenPipeError):
                pass
            _PROGRESS_QUEUE = None
        _LISTENER_THREAD = None

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
        # Respect silenced modules
        if record.name in _SILENCED_MODULES:
            return

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

def silence(*module_names):
    """Silences log output from the specified modules (only for intercepted logs)."""
    global _SILENCED_MODULES
    for name in module_names:
        _SILENCED_MODULES.add(name)

# 11. Sticky System Metrics
def enable_system_metrics():
    """
    Patches the logger to include CPU and Thread count in every log's 'extra' dict.
    Useful for high-density debugging.
    """
    def metrics_patcher(record):
        record["extra"]["cpu_pct"] = psutil.cpu_percent()
        record["extra"]["thread_cnt"] = threading.active_count()
    
    # Register the patcher if not already present
    if not any(p.__name__ == "metrics_patcher" for p in _PATCHERS):
        _PATCHERS.append(metrics_patcher)

def summary(title: str = "Execution Summary", success_count: int | None = None, failure_count: int | None = None):
    """
    Displays a beautiful Rich panel with execution statistics, 
    log counts, and system performance.
    """
    if not RICH_AVAILABLE:
        print(f"--- {title} ---")
        print(f"Total Runtime: {time.perf_counter() - float(os.environ['CHRONOS_START_TIME']):.2f}s")
        return

    # 1. Time Stats
    runtime = time.perf_counter() - float(os.environ["CHRONOS_START_TIME"])
    
    # 2. Log Stats Table
    log_table = Table(box=None, padding=(0, 2))
    log_table.add_column("Level", style="bold")
    log_table.add_column("Count", justify="right")
    
    # Track if we have any stats to show
    has_stats = False
    for level, count in _LOG_COUNTS.items():
        if count > 0:
            has_stats = True
            color = next((l["color"].strip("<>") for l in LOG_LEVELS if l["name"] == level), "white")
            # Special handling for EXCEPTION which isn't in LOG_LEVELS
            if level == "EXCEPTION": color = "red"
            log_table.add_row(f"[{color}]{level}[/]", str(count))

    # 3. Success/Failure Stats (if provided)
    results_table = None
    if success_count is not None or failure_count is not None:
        has_stats = True
        results_table = Table(box=None, padding=(0, 2))
        results_table.add_column("Status", style="bold")
        results_table.add_column("Count", justify="right")
        if success_count is not None:
            results_table.add_row("[green]Success[/]", str(success_count))
        if failure_count is not None:
            results_table.add_row("[red]Failure[/]", str(failure_count))

    # 4. System Stats
    mem = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    sys_info = f"[dim]Final Memory: {mem:.2f} MB | Runtime: {runtime:.2f}s[/]"

    # Construct the content
    content = []
    if has_stats:
        content.append(log_table)
        if results_table:
            content.append(results_table)
    else:
        content.append("[dim]No activity recorded.[/]")

    _rich_console.print("\n")
    _rich_console.print(
        Panel(
            Columns(content),
            title=f"[bold cyan]{title}[/]",
            subtitle=sys_info,
            expand=False,
            padding=(1, 2)
        )
    )

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
setattr(_logger, "reset_progress_queue", reset_progress_queue)
setattr(_logger, "summary", summary)
setattr(_logger, "silence", silence)

logger = cast("ChronosLogger", _logger)
__all__ = ["logger"]
