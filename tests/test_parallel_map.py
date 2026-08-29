"""Tests for parallel.map / parallel.starmap streaming API."""

import time

import pytest

from chronos import logger, parallel
from chronos.logger import _LOG_COUNTS, _PATCHERS, file_formatter


@pytest.fixture(autouse=True)
def setup_logger(tmp_path):
    """Redirect logger sinks to a temp dir for isolated, synchronous assertions."""
    logger.remove()

    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    test_log = log_dir / "test.log"
    test_fail = log_dir / "fail.log"

    logger.add(test_log, level="TRACE", format=file_formatter, enqueue=False)
    logger.add(
        test_fail,
        level="ERROR",
        filter=lambda r: r["extra"].get("is_failure", False),
        format=file_formatter,
        enqueue=False,
    )

    for k in _LOG_COUNTS:
        _LOG_COUNTS[k] = 0
    _PATCHERS.clear()

    yield {"log": test_log, "fail": test_fail, "dir": log_dir}

    logger.remove()


def worker_sq(x):
    if x == -1:
        raise ValueError("Fail Task")
    return x * x


def add(a, b):
    """Module-level so process-mode starmap can pickle it."""
    return a + b


# --- Unit ---


@pytest.mark.unit
def test_run_result_legacy_unpack():
    """RunResult unpacks as the legacy (successes, failures, failed_inputs, results) tuple."""
    run = parallel.RunResult(
        successes=2, failures=1, failed_inputs=[-1], results=[1, 9], duration=0.5
    )
    s, f, failed, results = run
    assert (s, f, failed, results) == (2, 1, [-1], [1, 9])
    assert run.interrupted is False


# --- Component: thread mode ---


@pytest.mark.component
def test_map_thread_ordered(setup_logger):
    """Ordered map returns results in input order."""
    run = parallel.thread_map(worker_sq, [1, 2, 3, 4], desc="Ordered")
    assert run.successes == 4
    assert run.failures == 0
    assert run.results == [1, 4, 9, 16]
    assert run.duration > 0


@pytest.mark.component
def test_map_generator_input_unknown_total(setup_logger):
    """A lazy generator input works without a known total (the file-processing case)."""
    inputs = (i for i in range(50))
    run = parallel.thread_map(worker_sq, inputs, workers=4, desc="Streaming")
    assert run.successes == 50
    assert sorted(run.results) == [i * i for i in range(50)]


@pytest.mark.component
def test_map_failure_collection(setup_logger):
    """Failures are counted, inputs recorded, and logged to the failures sink."""
    run = parallel.thread_map(worker_sq, [1, -1, 3, -1], desc="Recovery")
    assert run.successes == 2
    assert run.failures == 2
    assert run.failed_inputs == [-1, -1]
    assert sorted(run.results) == [1, 9]

    fail_content = setup_logger["fail"].read_text()
    assert "Task failed during 'Recovery' (Input: -1)" in fail_content
    assert "ValueError: Fail Task" in fail_content


@pytest.mark.component
def test_map_unordered(setup_logger):
    """Unordered mode completes all tasks (completion-order collection)."""
    run = parallel.thread_map(worker_sq, range(20), unordered=True, desc="Unordered")
    assert run.successes == 20
    assert sorted(run.results) == [i * i for i in range(20)]


@pytest.mark.component
def test_starmap(setup_logger):
    """starmap unpacks each input tuple into the worker."""
    run = parallel.starmap(add, [(1, 2), (3, 4), (5, 6)], mode="thread", desc="Starmap")
    assert run.successes == 3
    assert sorted(run.results) == [3, 7, 11]


@pytest.mark.component
def test_map_collect_false_streams(setup_logger):
    """collect=False streams results through post_func without accumulating."""
    seen = []

    def post(r):
        seen.append(r)
        return r

    run = parallel.thread_map(worker_sq, range(10), collect=False, post_func=post, desc="Stream")
    assert run.results == []  # nothing accumulated
    assert sorted(seen) == [i * i for i in range(10)]
    assert run.successes == 10


@pytest.mark.component
def test_map_on_error_raise(setup_logger):
    """on_error='raise' propagates the worker exception after logging."""
    with pytest.raises(ValueError, match="Fail Task"):
        parallel.thread_map(worker_sq, [1, -1], on_error="raise", desc="Raise")


@pytest.mark.component
def test_map_post_func_failure_counts(setup_logger):
    """A post_func exception counts as a task failure, matching execute() semantics."""

    def bad_post(r):
        raise RuntimeError("post boom")

    run = parallel.thread_map(worker_sq, [1, 2], post_func=bad_post, desc="PostFail")
    assert run.successes == 0
    assert run.failures == 2


@pytest.mark.component
def test_map_empty_input(setup_logger):
    """Empty input completes immediately with zero counts."""
    run = parallel.thread_map(worker_sq, [], desc="Empty")
    assert run.successes == 0
    assert run.failures == 0
    assert run.results == []


@pytest.mark.component
def test_map_slow_head_does_not_deadlock(setup_logger):
    """A slow first task must not stall the whole run (bounded window, ordered mode)."""

    def slow_first(x):
        if x == 0:
            time.sleep(0.3)
        return x

    run = parallel.thread_map(slow_first, range(12), workers=4, desc="HeadLine")
    assert run.successes == 12
    assert run.results == list(range(12))


# --- Component: process mode ---


@pytest.mark.component
def test_map_process_basic(setup_logger):
    """Basic process-mode map works (main process, spawn-safe)."""
    run = parallel.process_map(worker_sq, range(4), workers=2, desc="ProcMap")
    assert run.successes == 4
    assert sorted(run.results) == [0, 1, 4, 9]


@pytest.mark.component
def test_starmap_process(setup_logger):
    """Process-mode starmap unpacks argument tuples."""
    run = parallel.starmap(add, [(1, 2), (3, 4)], mode="process", workers=2, desc="ProcStarmap")
    assert run.successes == 2
    assert sorted(run.results) == [3, 7]


@pytest.mark.component
def test_map_process_generator(setup_logger):
    """Process mode over a generator input with unknown total."""
    inputs = (i for i in range(10))
    run = parallel.process_map(worker_sq, inputs, workers=2, desc="ProcGen")
    assert run.successes == 10
    assert sorted(run.results) == [i * i for i in range(10)]
