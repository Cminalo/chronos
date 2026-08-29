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

from __future__ import annotations

import logging
import multiprocessing
import os
import re
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from itertools import count
from pathlib import Path
from queue import Empty
from types import TracebackType
from typing import TYPE_CHECKING, Any, cast

import psutil
from dotenv import load_dotenv
from loguru import logger as _logger

# Rich Integration
try:
    from rich.columns import Columns
    from rich.console import Console
    from rich.logging import RichHandler  # noqa: F401 - availability probe
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TaskID,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )
    from rich.table import Table

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from loguru import Logger, Message, Record

    # Define a protocol/class for the custom logger to support autocomplete
    class ChronosLogger(Logger):
        def benchmark(self, name: str = "Operation") -> AbstractContextManager[None]: ...
        def memory(self, message: str = "Memory check") -> None: ...
        def progress(self, transient: bool = False) -> AbstractContextManager[Progress]: ...
        def intercept_standard_logging(self) -> None: ...
        def enable_system_metrics(self) -> None: ...
        def get_progress_queue(self) -> multiprocessing.Queue[Any]: ...
        def set_progress_queue(self, queue: multiprocessing.Queue[Any]) -> None: ...
        def reset_progress_queue(self) -> None: ...
        def summary(
            self,
            title: str = "Execution Summary",
            success_count: int | None = None,
            failure_count: int | None = None,
        ) -> None: ...
        def silence(self, *module_names: str) -> None: ...


# 1. Load Environment Variables
load_dotenv()


# 2. Define Constants
def _resolve_log_dir(raw: str | None) -> tuple[Path, str | None]:
    """Resolve the log directory, falling back to the system temp dir if unusable."""
    base = Path(raw or "logs")
    try:
        base.mkdir(parents=True, exist_ok=True)
        if not os.access(base, os.W_OK):
            raise PermissionError(f"not writable: {base}")
        return base, None
    except OSError as err:
        fallback = Path(tempfile.gettempdir()) / "chronos-logs"
        with suppress(OSError):
            fallback.mkdir(parents=True, exist_ok=True)
        return fallback, f"CHRONOS_LOG_DIR '{raw}' unusable ({err}); log files -> '{fallback}'"


LOG_DIR, _LOG_DIR_WARNING = _resolve_log_dir(os.getenv("CHRONOS_LOG_DIR"))
LOG_FILE_PATH = LOG_DIR / "chronos_{time:YYYY-MM-DD}.log"
JSON_LOG_FILE_PATH = LOG_DIR / "chronos_{time:YYYY-MM-DD}.jsonl"
FAILURES_LOG_FILE_PATH = LOG_DIR / "failures_{time:YYYY-MM-DD}.log"

# Global Silenced Modules (for standard logging interception)
_SILENCED_MODULES = set()

# 3. Configure Levels & Colors
LOG_LEVELS: list[dict[str, Any]] = [
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
            pass  # Level doesn't exist yet

        with suppress(TypeError, ValueError):
            _logger.level(name, **config)

    # Mark as configured so re-imports don't trigger redeclarations
    setattr(_logger, "_chronos_levels_configured", True)  # noqa: B010

# Global Stats Tracking
_LOG_COUNTS = {level["name"]: 0 for level in LOG_LEVELS}
_LOG_COUNTS["EXCEPTION"] = 0  # Track logger.exception calls

_PATCHERS: list[Callable[[Record], None]] = []

_LOG_COUNTS_LOCK = threading.Lock()


def _master_patcher(record: Record) -> None:
    """Executes all registered patchers exactly once per record."""
    # Internal: Track stats (lock guards += against concurrent logging threads)
    with _LOG_COUNTS_LOCK:
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

# Global Start Time (per-process; perf_counter is only comparable within one process)
_CHRONOS_START_TIME: float = time.perf_counter()


# 4. Custom Formatters
def _location(record: Record) -> str:
    """IDE-clickable location: cwd-relative path when possible, absolute otherwise."""
    try:
        rel = os.path.relpath(record["file"].path)
    except ValueError:  # e.g. different drive on Windows
        rel = record["file"].path
    loc = record["file"].path if rel.startswith("..") else rel
    # Paths are interpolated into a loguru color-tag format string; escape `<`
    # so pseudo-paths like `<stdin>` are not parsed as color directives.
    return loc.replace("<", "\\<")


def console_formatter(record: Record) -> str:
    """Compact single-line console format with a clickable `path:line` suffix."""
    return (
        "<green>{time:HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "{message}  <dim>" + _location(record) + ":{line}</dim>\n{exception}"
    )


def file_formatter(record: Record) -> str:
    """Forensic text-file format: full date, process/thread context, quoted message."""
    message_format = "{message}"
    if "duration" in record["extra"]:
        message_format = "{message} in {extra[duration]:.3f}s"
    if "memory_mb" in record["extra"]:
        message_format = "{message} (RSS: {extra[memory_mb]:.2f} MB)"

    ctx = ""
    if "x_id" in record["extra"]:
        ctx += f" [ID: {record['extra']['x_id']}]"
    if "cpu_pct" in record["extra"]:
        ctx += f" [CPU: {record['extra']['cpu_pct']}%|Thr: {record['extra']['thread_cnt']}]"

    return (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <9}</level> | "
        "<dim>[P:{process.name}|T:{thread.name}]</dim>" + ctx + " | "
        f'"{message_format}" | '
        "<cyan>" + _location(record) + ":{line}</cyan> in <cyan>{function}</cyan>()\n{exception}"
    )


def _resolve_console_level(raw: str) -> tuple[str, str | None]:
    """Resolve LOGGER_LEVEL against registered levels, falling back to INFO."""
    level = raw.strip().upper()
    if level in _LOG_COUNTS:
        return level, None
    return "INFO", f"Invalid LOGGER_LEVEL '{raw}'; falling back to INFO"


# 5. Configure Sinks
_logger.remove()

console_level, _LEVEL_WARNING = _resolve_console_level(os.getenv("LOGGER_LEVEL", "INFO"))
use_rich = os.getenv("RICH_CONSOLE", "True").lower() in ("true", "1", "yes")

# diagnose=True interpolates live variable values into tracebacks (secret-leak
# risk in persistent logs); opt in for local development via CHRONOS_DIAGNOSE.
diagnose = os.getenv("CHRONOS_DIAGNOSE", "False").lower() in ("true", "1", "yes")

# Retention policy: daily rotation, 7-day maximum retention.
ROTATION = "00:00"
RETENTION = "7 days"


# Messages proxied from child processes are pre-formatted console lines; the
# child already writes them to the shared file sinks, so files must skip them.
def _not_proxied(record: Record) -> bool:
    return not record["extra"].get("proxied", False)


def _add_file_sink(path: Path, **kwargs: Any) -> None:
    """Add a daily-rotated, 7-day-retained, compressed file sink."""
    _logger.add(
        path,
        rotation=ROTATION,
        retention=RETENTION,
        compression="zip",
        enqueue=True,
        backtrace=True,
        diagnose=diagnose,
        **kwargs,
    )


_ANSI_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")


def _console_is_tty() -> bool:
    """Whether the inherited console is a TTY (drives color decisions)."""
    try:
        return bool(sys.stderr.isatty())
    except (AttributeError, ValueError, OSError):
        return False


# Optional Global Rich Console (used so logger and progress share the same buffer)
# We must explicitly set file=sys.stderr so it perfectly synchronizes with Loguru's output stream.
_rich_console = Console(file=sys.stderr) if RICH_AVAILABLE else None


def rich_console_sink(message: Message) -> None:
    """Custom sink that forces Loguru to use Rich's print, preventing progress bar tearing."""
    if _rich_console is None:
        return
    # Rich passes embedded ANSI through even to non-TTY streams; strip it when
    # piped so redirected output stays clean.
    text = message if _console_is_tty() else _ANSI_SGR_RE.sub("", message)
    _rich_console.print(text, end="", markup=False, highlight=False, soft_wrap=True)


if RICH_AVAILABLE and use_rich:
    # Rich Console Sink
    _logger.add(
        rich_console_sink,
        level=console_level,
        format=console_formatter,
        colorize=True,
        # Must be False to prevent background thread terminal
        # tearing with Progress bars
        enqueue=False,
        backtrace=False,
        diagnose=False,
    )
else:
    # Standard Console Sink
    _logger.add(
        sys.stderr,
        level=console_level,
        format=console_formatter,
        enqueue=True,
        colorize=None,
        backtrace=False,
        diagnose=False,
    )

# Surface configuration fallbacks now that sinks exist to carry them.
for _config_warning in (_LOG_DIR_WARNING, _LEVEL_WARNING):
    if _config_warning:
        _logger.warning(_config_warning)

# Sink 2: Text Log (logs/chronos_DATE.log)
_add_file_sink(LOG_FILE_PATH, level="TRACE", format=file_formatter, filter=_not_proxied)

# Sink 3: JSON Log (logs/chronos_DATE.jsonl)
_add_file_sink(JSON_LOG_FILE_PATH, level="TRACE", serialize=True, filter=_not_proxied)

# Sink 4: Failures Log (logs/failures_DATE.log)
# Only captures logs explicitly marked as failures (e.g. from parallel.execute)
_add_file_sink(
    FAILURES_LOG_FILE_PATH,
    level="ERROR",
    filter=lambda record: record["extra"].get("is_failure", False) and _not_proxied(record),
    format=file_formatter,
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
def memory(message: str = "Memory check") -> None:
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    rss_mb = mem_info.rss / (1024 * 1024)
    _logger.bind(memory_mb=rss_mb).opt(depth=1).log("MEMORY", message)


# 8. Rich Progress & Log Proxy Manager
_PROGRESS_QUEUE: multiprocessing.Queue[Any] | None = None
_LISTENER_THREAD: threading.Thread | None = None
_LISTENER_LOCK = threading.Lock()
_ACTIVE_PROGRESS: Progress | None = None  # Tracks the currently active Rich progress instance

_REMOTE_TASK_SEQ = count()  # Collision-free IDs for RemoteProgress tasks (per child process)


class RemoteProgress:
    """
    A proxy for the rich.progress.Progress object that can be used in child processes.
    It sends updates via a multiprocessing Queue to the main process.

    Only the operations the main-process listener understands are proxied:
    ``add_task``, ``update`` and ``advance``.
    """

    def __init__(self, queue: multiprocessing.Queue[Any]):
        self._queue = queue

    def add_task(self, description: str, total: float = 100.0, **kwargs: Any) -> int:
        # PID + process-lifetime sequence: unique across processes and across
        # same-millisecond calls (id(description) can repeat for interned or
        # recycled string objects).
        task_id = os.getpid() * 10_000_000 + next(_REMOTE_TASK_SEQ)
        self._queue.put(("progress", "add", task_id, description, total, kwargs))
        return task_id

    def update(self, task_id: int, advance: float | None = None, **kwargs: Any) -> None:
        self._queue.put(("progress", "update", task_id, advance, kwargs))

    def advance(self, task_id: int, advance: float = 1) -> None:
        self.update(task_id, advance=advance)


def _main_listener(queue: multiprocessing.Queue[Any]) -> None:
    """Background thread in the main process that listens for progress AND log updates."""
    tasks: dict[int, TaskID] = {}
    while True:
        try:
            # We use a timeout to ensure the thread is periodically wakeable
            # and doesn't get stuck if the queue is suddenly closed.
            msg = queue.get(timeout=0.1)
        except Empty:
            continue  # Poll timeout; keep waiting
        except (ValueError, EOFError, OSError, TypeError):
            break

        if msg is None:  # Sentinel for shutdown
            break

        # One malformed message must never kill the listener thread — that
        # would silently drop every later child log and progress update.
        try:
            category = msg[0]
            p = _ACTIVE_PROGRESS

            if category == "progress":
                if p is None:
                    continue
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
                # NOTE: This must work even when no progress bar is active, otherwise
                # this listener thread would die and all later child logs would be lost.
                # The `proxied` flag routes these to console sinks only — the child
                # process already wrote them to the shared file sinks.
                _, formatted_msg = msg
                _logger.bind(proxied=True).opt(raw=True).info(formatted_msg)
        except Exception:  # noqa: BLE001 - resilience over correctness here
            _logger.warning("chronos listener dropped a malformed child message")
            continue


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
                target=_main_listener, args=(_PROGRESS_QUEUE,), daemon=True
            )
            _LISTENER_THREAD.start()

    try:
        with p:
            yield p
    finally:
        _ACTIVE_PROGRESS = None


def set_progress_queue(queue: multiprocessing.Queue[Any]) -> None:
    """Set the queue used for remote progress updates (call this in child processes)."""
    global _PROGRESS_QUEUE
    _PROGRESS_QUEUE = queue

    if multiprocessing.current_process().name == "MainProcess":
        return

    _logger.remove()
    _add_file_sink(LOG_FILE_PATH, level="TRACE", format=file_formatter)
    _add_file_sink(JSON_LOG_FILE_PATH, level="TRACE", serialize=True)
    _add_file_sink(
        FAILURES_LOG_FILE_PATH,
        level="ERROR",
        filter=lambda record: record["extra"].get("is_failure", False),
        format=file_formatter,
    )

    def proxy_sink(message: Message) -> None:
        with suppress(ValueError, EOFError, BrokenPipeError, OSError):
            queue.put(("log", message))

    # Spawned children inherit the parent's stderr fd, so the TTY check here
    # matches the parent console: colorize only when output is actually a TTY.
    _logger.add(
        proxy_sink,
        level=console_level,
        format=console_formatter,
        colorize=_console_is_tty(),
    )


def reset_progress_queue() -> None:
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
def handle_exception(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: TracebackType | None,
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    process_name = multiprocessing.current_process().name
    process_id = os.getpid()
    thread_name = threading.current_thread().name
    thread_id = threading.get_ident()

    tb = exc_traceback
    while tb is not None and tb.tb_next:
        tb = tb.tb_next
    function_name = tb.tb_frame.f_code.co_name if tb is not None else "<unknown>"

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

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Respect silenced modules
            if record.name in _SILENCED_MODULES:
                return

            # Get corresponding Loguru level if it exists.
            level: str | int
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
        except Exception:
            # A malformed stdlib record (e.g. mismatched format args raising
            # TypeError in getMessage) must never crash caller code — the same
            # graceful-degradation contract stdlib handlers follow. handleError
            # reports to stderr and never raises.
            self.handleError(record)


def intercept_standard_logging() -> None:
    """
    Routes all standard Python 'logging' calls through Chronos.
    Call this once at the start of your application.
    """
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)


def silence(*module_names: str) -> None:
    """Silences log output from the specified modules (only for intercepted logs)."""
    global _SILENCED_MODULES
    for name in module_names:
        _SILENCED_MODULES.add(name)


# 11. Sticky System Metrics
def enable_system_metrics() -> None:
    """
    Patches the logger to include CPU and Thread count in every log's 'extra' dict.
    Useful for high-density debugging.
    """

    def metrics_patcher(record: Record) -> None:
        record["extra"]["cpu_pct"] = psutil.cpu_percent()
        record["extra"]["thread_cnt"] = threading.active_count()

    # Register the patcher if not already present
    if not any(p.__name__ == "metrics_patcher" for p in _PATCHERS):
        _PATCHERS.append(metrics_patcher)


def summary(
    title: str = "Execution Summary",
    success_count: int | None = None,
    failure_count: int | None = None,
) -> None:
    """
    Displays a beautiful Rich panel with execution statistics,
    log counts, and system performance.
    """
    if not RICH_AVAILABLE:
        print(f"--- {title} ---")
        print(f"Total Runtime: {time.perf_counter() - _CHRONOS_START_TIME:.2f}s")
        return

    if _rich_console is None:  # Defensive: RICH_AVAILABLE but console missing
        print(f"--- {title} ---")
        print(f"Total Runtime: {time.perf_counter() - _CHRONOS_START_TIME:.2f}s")
        return

    # 1. Time Stats
    runtime = time.perf_counter() - _CHRONOS_START_TIME

    # 2. Log Stats Table
    log_table = Table(box=None, padding=(0, 2))
    log_table.add_column("Level", style="bold")
    log_table.add_column("Count", justify="right")

    # Track if we have any stats to show
    has_stats = False
    for level, n in _LOG_COUNTS.items():
        if n > 0:
            has_stats = True
            # Convert loguru color tags ('<red><bold>') to a Rich style ('red bold')
            color = next(
                (
                    entry["color"].replace("><", " ").strip("<>")
                    for entry in LOG_LEVELS
                    if entry["name"] == level
                ),
                "white",
            )
            # Special handling for EXCEPTION which isn't in LOG_LEVELS
            if level == "EXCEPTION":
                color = "red"
            log_table.add_row(f"[{color}]{level}[/]", str(n))

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
    content: list[Any] = []
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
            padding=(1, 2),
        )
    )


def get_progress_queue() -> multiprocessing.Queue[Any]:
    """Returns the current progress queue to be passed to child processes."""
    global _PROGRESS_QUEUE
    if _PROGRESS_QUEUE is None:
        _PROGRESS_QUEUE = multiprocessing.Queue()
    return _PROGRESS_QUEUE


sys.excepthook = handle_exception

# Attach methods
setattr(_logger, "benchmark", benchmark)  # noqa: B010
setattr(_logger, "memory", memory)  # noqa: B010
setattr(_logger, "progress", progress)  # noqa: B010
setattr(_logger, "intercept_standard_logging", intercept_standard_logging)  # noqa: B010
setattr(_logger, "enable_system_metrics", enable_system_metrics)  # noqa: B010
setattr(_logger, "get_progress_queue", get_progress_queue)  # noqa: B010
setattr(_logger, "set_progress_queue", set_progress_queue)  # noqa: B010
setattr(_logger, "reset_progress_queue", reset_progress_queue)  # noqa: B010
setattr(_logger, "summary", summary)  # noqa: B010
setattr(_logger, "silence", silence)  # noqa: B010

logger = cast("ChronosLogger", _logger)
__all__ = ["logger"]
