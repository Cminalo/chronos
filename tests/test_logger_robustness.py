"""Robustness tests: chronos logging must degrade gracefully, never crash caller code.

Covers fallback paths (bad env vars, unusable log dirs, malformed child messages,
missing Rich) and regression-guards the proxied-log dedup / retention contracts.
"""

import importlib
import json
import logging
import multiprocessing
import os
import queue as pyqueue
import re
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from chronos.logger import (
    InterceptHandler,
    _add_file_sink,
    _console_is_tty,
    _location,
    _main_listener,
    _not_proxied,
    _resolve_console_level,
    _resolve_log_dir,
    logger,
)


@pytest.fixture(autouse=True)
def setup_logger(tmp_path):
    """Redirect logger sinks to a temp dir with a list-based capture sink."""
    logger.remove()

    captured: list[str] = []
    logger.add(captured.append, level="TRACE")
    test_log = tmp_path / "test.log"
    logger.add(test_log, level="TRACE", format="{message}", enqueue=False)

    yield {"capture": captured, "log": test_log}

    logger.remove()


# --- Unit: config resolution fallbacks ---------------------------------------


@pytest.mark.unit
def test_resolve_console_level_accepts_registered_levels():
    assert _resolve_console_level("info") == ("INFO", None)
    assert _resolve_console_level("  debug ") == ("DEBUG", None)
    assert _resolve_console_level("BENCHMARK") == ("BENCHMARK", None)


@pytest.mark.unit
def test_resolve_console_level_falls_back_to_info():
    level, warning = _resolve_console_level("BOGUS")
    assert level == "INFO"
    assert warning is not None and "BOGUS" in warning

    level, warning = _resolve_console_level("20")  # numeric strings are not levels
    assert level == "INFO"
    assert warning is not None


@pytest.mark.unit
def test_resolve_log_dir_creates_directory(tmp_path):
    target = tmp_path / "fresh" / "nested"
    resolved, warning = _resolve_log_dir(str(target))
    assert resolved == target and target.is_dir()
    assert warning is None


@pytest.mark.unit
def test_resolve_log_dir_falls_back_when_path_is_a_file(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    resolved, warning = _resolve_log_dir(str(blocker))
    assert "chronos-logs" in str(resolved)
    assert warning is not None and "unusable" in warning


@pytest.mark.unit
def test_resolve_log_dir_falls_back_on_permission_error(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr("pathlib.Path.mkdir", denied)
    resolved, warning = _resolve_log_dir(str(tmp_path / "denied"))
    assert "chronos-logs" in str(resolved)
    assert warning is not None and "denied" in warning


@pytest.mark.unit
def test_console_is_tty_survives_broken_stderr(monkeypatch):
    class Broken:
        def isatty(self):
            raise ValueError("closed")

    monkeypatch.setattr("sys.stderr", Broken())
    assert _console_is_tty() is False


@pytest.mark.unit
def test_location_escapes_angle_brackets():
    record = {"file": SimpleNamespace(path="/tmp/<stdin>"), "line": 7}
    loc = _location(record)  # type: ignore[arg-type]
    assert "\\<" in loc  # escaped so loguru's colorizer treats it as literal
    assert "\x1b" not in loc


# --- Component: listener resilience ------------------------------------------


@pytest.mark.component
def test_listener_survives_malformed_messages(setup_logger):
    """Garbage on the queue must not kill the listener: later valid logs survive."""
    q: pyqueue.Queue = pyqueue.Queue()
    malformed: list[object] = [
        42,
        "not a tuple",
        ("bogus",),
        ("log",),  # wrong arity -> unpack error
        ("progress", "add"),  # wrong arity for progress add
        ("progress", "bogus_action", 1),
    ]
    for item in malformed:
        q.put(item)
    q.put(("log", "survivor message"))
    q.put(None)  # shutdown sentinel

    thread = threading.Thread(target=_main_listener, args=(q,), daemon=True)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive(), "listener must exit on sentinel, not on garbage"
    assert any("survivor message" in m for m in setup_logger["capture"])
    # Every malformed item produced a dropped-message warning instead of a crash.
    dropped = [m for m in setup_logger["capture"] if "malformed child message" in m]
    assert len(dropped) >= 1


@pytest.mark.component
def test_listener_ignores_progress_when_no_active_bar(setup_logger):
    q: pyqueue.Queue = pyqueue.Queue()
    q.put(("progress", "add", 1, "task", 10, {}))
    q.put(("log", "still alive"))
    q.put(None)

    thread = threading.Thread(target=_main_listener, args=(q,), daemon=True)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert any("still alive" in m for m in setup_logger["capture"])


# --- Component: proxied-log routing contract ---------------------------------


@pytest.mark.component
def test_proxied_messages_never_reach_file_sinks(setup_logger):
    """Regression guard: child logs are written by the child's own file sinks;
    the main-process re-log must stay console-only or files get duplicates."""
    proxied_log = setup_logger["log"].with_name("proxied.log")
    _add_file_sink(proxied_log, level="TRACE", format="{message}", filter=_not_proxied)

    logger.bind(proxied=True).opt(raw=True).info("proxied line")
    logger.info("normal line")

    content = proxied_log.read_text()
    assert "proxied line" not in content
    # Normal (non-proxied) records still flow through the filtered file sink.
    assert "normal line" in setup_logger["log"].read_text()


@pytest.mark.component
def test_file_sinks_enforce_daily_rotation_and_7_day_retention(setup_logger):
    """Retention contract: every _add_file_sink rotates daily and keeps 7 days."""
    sink_path = setup_logger["log"].with_name("retention.log")
    _add_file_sink(sink_path, level="TRACE", format="{message}")

    handler = next(
        h
        for h in logger._core.handlers.values()
        if str(getattr(h._sink, "_path", "")) == str(sink_path)
    )
    assert "RotationTime" in repr(handler._sink._rotation_function)
    assert "seconds=604800.0" in repr(handler._sink._retention_function)  # exactly 7 days


# --- Component: standard-logging interception --------------------------------


@pytest.mark.component
def test_intercept_custom_stdlib_level_does_not_raise(setup_logger):
    """stdlib levels unknown to loguru (e.g. level 35) must not crash emit()."""
    record = logging.LogRecord("test.mod", 35, "p", 1, "custom level msg", None, None)
    InterceptHandler().emit(record)  # must not raise
    assert any("custom level msg" in m for m in setup_logger["capture"])


@pytest.mark.component
def test_silence_drops_intercepted_modules(setup_logger):
    from chronos.logger import _SILENCED_MODULES

    _SILENCED_MODULES.add("noisy.mod")
    try:
        record = logging.LogRecord("noisy.mod", logging.INFO, "p", 1, "hidden", None, None)
        InterceptHandler().emit(record)
        assert not any("hidden" in m for m in setup_logger["capture"])
    finally:
        _SILENCED_MODULES.discard("noisy.mod")


# --- Component: summary fallbacks ---------------------------------------------


@pytest.mark.component
def test_summary_plain_fallback_without_rich(setup_logger, capsys, monkeypatch):
    cl = importlib.import_module("chronos.logger")  # bypass package-attr shadowing

    monkeypatch.setattr(cl, "RICH_AVAILABLE", False)
    cl.summary(title="Fallback Title")
    out = capsys.readouterr().out
    assert "--- Fallback Title ---" in out
    assert "Total Runtime" in out


@pytest.mark.component
def test_summary_plain_fallback_when_console_missing(setup_logger, capsys, monkeypatch):
    """Defensive guard: RICH_AVAILABLE=True but console is None must not assert-crash."""
    cl = importlib.import_module("chronos.logger")  # bypass package-attr shadowing

    monkeypatch.setattr(cl, "_rich_console", None)
    cl.summary(title="Guard Title")
    out = capsys.readouterr().out
    assert "--- Guard Title ---" in out


# --- System: full child-process proxy round trip ------------------------------


def _child_worker(q) -> None:  # module-level: spawn must pickle it
    from chronos.logger import logger, set_progress_queue

    set_progress_queue(q)
    logger.info("hello from child process")


@pytest.mark.system
def test_child_proxy_roundtrip_single_clean_line(tmp_path, monkeypatch, setup_logger):
    """End-to-end: child log appears exactly once on console and once in the
    file, with no ANSI escapes and no duplication."""
    monkeypatch.chdir(tmp_path)  # child inherits CWD -> its file sinks land here

    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    listener = threading.Thread(target=_main_listener, args=(q,), daemon=True)
    listener.start()

    proc = ctx.Process(target=_child_worker, args=(q,))
    proc.start()
    proc.join(timeout=30)
    q.put(None)
    listener.join(timeout=5)

    assert proc.exitcode == 0, "child must run to completion"

    # Console: exactly one proxied line, no ANSI (piped stderr under pytest).
    console_hits = [m for m in setup_logger["capture"] if "hello from child process" in m]
    assert len(console_hits) == 1
    assert "\x1b[" not in console_hits[0]

    # File: exactly one line (child's own sink), no ANSI, no proxy duplicate.
    log_files = list((tmp_path / "logs").glob("chronos_*.log"))
    assert len(log_files) == 1
    content = log_files[0].read_text()
    hits = [line for line in content.splitlines() if "hello from child process" in line]
    assert len(hits) == 1
    assert "\x1b[" not in hits[0]
    assert re.search(r"\[P:SpawnProcess-\d+\|T:MainThread\]", hits[0])


@pytest.mark.system
def test_import_configures_exactly_the_documented_sinks():
    """Regression guard: module import must replace loguru's default sink with
    the documented set — a missing _logger.remove() silently doubles every
    console line (default format + chronos format)."""
    code = (
        "import json; from chronos.logger import logger; "
        "print(json.dumps([(h._id, type(h._sink).__name__) "
        "for h in logger._core.handlers.values()]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "RICH_CONSOLE": "True"},
        timeout=60,
        check=True,
    )
    sinks = json.loads(result.stdout.strip().splitlines()[-1])
    kinds = sorted(k for _, k in sinks)
    # 1 console (Rich CallableSink) + text/JSONL/failures FileSinks; the
    # default StreamSink must be gone.
    assert kinds == ["CallableSink", "FileSink", "FileSink", "FileSink"]
