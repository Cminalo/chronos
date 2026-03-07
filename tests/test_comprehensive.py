import os
import time
import logging
import multiprocessing
from pathlib import Path
import pytest
from chronos import logger, parallel
from chronos.logger import _LOG_COUNTS, _PATCHERS, file_formatter

# --- Fixtures ---

@pytest.fixture(autouse=True)
def setup_logger(tmp_path):
    """
    Redirect all logger sinks to a temporary directory for isolated testing.
    This prevents file handle conflicts and ensures a clean state for each test.
    """
    # 1. Remove all existing sinks
    logger.remove()
    
    # 2. Define temp paths
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    
    test_log = log_dir / "test.log"
    test_json = log_dir / "test.jsonl"
    test_fail = log_dir / "fail.log"
    
    # 3. Re-add sinks using the internal file_formatter
    # We use enqueue=False for tests to ensure synchronous writes for immediate assertion
    logger.add(test_log, level="TRACE", format=file_formatter, enqueue=False)
    logger.add(test_json, level="TRACE", format=file_formatter, serialize=True, enqueue=False)
    logger.add(test_fail, level="ERROR", filter=lambda r: r["extra"].get("is_failure", False), format=file_formatter, enqueue=False)
    
    # Reset counts
    for k in _LOG_COUNTS:
        _LOG_COUNTS[k] = 0
    
    # Clear patchers (except master)
    _PATCHERS.clear()
    
    yield {
        "log": test_log,
        "json": test_json,
        "fail": test_fail,
        "dir": log_dir
    }
    
    # 4. Cleanup
    logger.remove()

# --- Logger Core Tests ---

def test_logger_levels(setup_logger):
    """Verify all custom and standard levels work and are counted."""
    logger.trace("trace msg")
    logger.debug("debug msg")
    logger.info("info msg")
    logger.memory("memory check")
    logger.success("success msg")
    logger.warning("warning msg")
    logger.error("error msg")
    logger.critical("critical msg")
    
    assert _LOG_COUNTS["TRACE"] == 1
    assert _LOG_COUNTS["DEBUG"] == 1
    assert _LOG_COUNTS["INFO"] == 1
    assert _LOG_COUNTS["SUCCESS"] == 1
    assert _LOG_COUNTS["WARNING"] == 1
    assert _LOG_COUNTS["ERROR"] == 1
    assert _LOG_COUNTS["CRITICAL"] == 1

def test_memory_logging(setup_logger):
    """Verify memory logging doesn't crash and captures RSS."""
    logger.memory("Check RAM")
    assert _LOG_COUNTS["MEMORY"] == 1
    
    content = setup_logger["log"].read_text()
    assert "RSS:" in content
    assert "MB)" in content

def test_benchmark_context(setup_logger):
    """Verify benchmark context manager captures duration."""
    with logger.benchmark("speed test"):
        time.sleep(0.05)
    
    assert _LOG_COUNTS["BENCHMARK"] == 1
    content = setup_logger["log"].read_text()
    assert "speed test finished" in content
    assert "Duration:" in content
    assert "Global:" in content

def test_system_metrics_patching(setup_logger):
    """Verify system metrics can be enabled and patched into logs."""
    logger.enable_system_metrics()
    logger.info("Metrics test")
    
    content = setup_logger["log"].read_text()
    assert "CPU:" in content
    assert "Thr:" in content

def test_standard_logging_interception(setup_logger):
    """Verify standard logging is correctly intercepted."""
    logger.intercept_standard_logging()
    std_logger = logging.getLogger("test_interceptor")
    std_logger.error("Intercepted Error")
    
    assert _LOG_COUNTS["ERROR"] >= 1
    content = setup_logger["log"].read_text()
    assert "Intercepted Error" in content

def test_log_silencing(setup_logger):
    """Verify specific modules can be silenced."""
    logger.intercept_standard_logging()
    logger.silence("noisy_lib")
    
    noisy = logging.getLogger("noisy_lib")
    noisy.info("Silence is golden")
    
    assert _LOG_COUNTS["INFO"] == 0
    
    clean = logging.getLogger("clean_lib")
    clean.info("I am heard")
    assert _LOG_COUNTS["INFO"] == 1

# --- Parallel Tests ---

def worker_sq(x):
    if x == -1:
        raise ValueError("Fail Task")
    return x * x

def test_parallel_thread_recovery(setup_logger):
    """Verify thread execution with task recovery (failed input tracking)."""
    # Using Format B: (input, result)
    def prep(pool):
        data = [1, -1, 3]
        return [(item, pool.apply_async(worker_sq, (item,))) for item in data]
    
    s, f, failed, results = parallel.thread_run(prep, lambda x: None, "Thread Recovery", 3)
    
    assert s == 2
    assert f == 1
    assert failed == [-1]
    assert sorted(results) == [1, 9]
    
    # Check failure log
    fail_content = setup_logger["fail"].read_text()
    assert "Task failed during 'Thread Recovery' (Input: -1)" in fail_content
    assert "ValueError: Fail Task" in fail_content

def test_parallel_process_basic(setup_logger):
    """Verify basic process execution works."""
    def prep(pool):
        return [pool.apply_async(worker_sq, (i,)) for i in range(2)]
    
    s, f, failed, results = parallel.process_run(prep, lambda x: None, "Process Basic", 2, workers=2)
    assert s == 2
    assert f == 0
    assert sorted(results) == [0, 1]

def test_parallel_diverse_returns(setup_logger):
    """Verify that multiple return types (None, dict, tuple) are correctly handled and collected."""
    def diverse_worker(x):
        if x == 0: return None
        if x == 1: return {"key": "val"}
        return (x, [1, 2])

    def prep(pool):
        return [pool.apply_async(diverse_worker, (i,)) for i in range(3)]
    
    s, f, failed, results = parallel.thread_run(prep, None, "Diverse Returns", 3)
    
    assert s == 3
    assert None in results
    assert {"key": "val"} in results
    assert (2, [1, 2]) in results

def test_summary_panel(setup_logger, capsys):
    """Verify summary panel prints to rich console."""
    logger.info("test summary")
    logger.summary("Verification Summary", success_count=5, failure_count=0)
    # Just ensure it doesn't crash
    captured = capsys.readouterr()

def test_fork_bomb_safety():
    """Verify the safety check gracefully intercepts non-main-process execution without crashing."""
    # We simulate a child process call
    import multiprocessing
    original_name = multiprocessing.current_process().name
    multiprocessing.current_process().name = "SpawnPoolWorker-1"
    
    try:
        # Should NOT raise an exception, but return early to prevent the fork bomb
        s, f, failed, results = parallel.execute("process", lambda p: [], lambda x: None, "Safety", 0)
        assert s == 0
        assert f == 0
        assert failed == []
        assert results == []
    finally:
        multiprocessing.current_process().name = original_name
