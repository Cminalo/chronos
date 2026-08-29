"""
Parallel execution engine for Chronos.

This module provides a unified interface for multiprocess and multithreaded execution,
with integrated progress reporting and error handling.
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Sized
from dataclasses import dataclass, field
from multiprocessing.pool import AsyncResult, Pool, ThreadPool
from operator import length_hint
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from chronos.logger import logger

if TYPE_CHECKING:
    from multiprocessing.queues import Queue


def _worker_init(queue: Queue[Any] | None) -> None:
    """
    Initialize a worker process or thread with the global progress queue.
    Also ensures workers ignore SIGINT so the main process can handle Ctrl+C.

    Parameters
    ----------
    queue : multiprocessing.Queue[Any] | None
        The progress queue used for inter-process communication of progress updates.
    """
    # Ignore SIGINT in workers so only the main process handles KeyboardInterrupt.
    # We MUST NOT do this in ThreadPool threads, as signal() only works in the main thread.
    if multiprocessing.current_process().name != "MainProcess":
        signal.signal(signal.SIGINT, signal.SIG_IGN)

    if queue is not None:
        logger.set_progress_queue(queue)


_PoolT = TypeVar("_PoolT", Pool, ThreadPool)


def execute(
    mode: Literal["process", "thread"],
    prep_func: Callable[[_PoolT], Iterable[Any | tuple[Any, Any]]],
    post_func: Callable[[Any], None] | None,
    desc: str,
    total: int,
    workers: int | None = None,
) -> tuple[int, int, list[Any], list[Any]]:
    """
    Execute a set of tasks in parallel using a process or thread pool.

    Parameters
    ----------
    mode : {"process", "thread"}
        The parallelization strategy to use.
    prep_func : Callable[[Pool | ThreadPool], Iterable[Any | tuple[Any, Any]]]
        A function that prepares the pool (e.g., calls apply_async) and returns an iterable
        of results or (input, result) tuples.
    post_func : Callable[[Any], None] | None
        A function to process each result as it completes. If None, no post-processing occurs.
    desc : str
        A description of the execution task, used for the progress bar.
    total : int
        The total number of tasks expected.
    workers : int | None, optional
        The number of worker processes or threads to spawn. Defaults to
        the CPU count or pool default.

    Returns
    -------
    tuple[int, int, list[Any], list[Any]]
        A tuple containing (success_count, failure_count, failed_inputs, results).

    Raises
    ------
    KeyboardInterrupt
        If the execution is interrupted by the user.
    Exception
        Any unexpected error that occurs during pool initialization or execution.
    """
    # 1. Spawn Safety Check
    # On macOS/Windows (spawn/forkserver start methods), this prevents an infinite recursion
    # loop if the entry point logic is accidentally triggered in a child process.
    if mode == "process" and multiprocessing.current_process().name != "MainProcess":
        logger.warning(
            f"⚠️  parallel.process_run('{desc}') was called outside an "
            "'if __name__ == \"__main__\":' block.\n"
            "Chronos gracefully intercepted this to prevent a "
            "multiprocessing fork bomb.\n"
            "Please wrap your top-level execution code in the main block "
            "to ensure correct behavior."
        )
        return 0, 0, [], []

    queue = logger.get_progress_queue()
    PoolClass = Pool if mode == "process" else ThreadPool

    success_count = 0
    failure_count = 0
    failed_inputs = []
    results = []
    interrupted = False

    # Initialize the pool outside the progress context to ensure we can close/join it correctly.
    pool = PoolClass(processes=workers, initializer=_worker_init, initargs=(queue,))

    try:
        with logger.progress(transient=False) as p:
            main_task = p.add_task(f"[green]{desc}", total=total)

            # 1. PREP: Submit tasks to the pool
            prep_data = prep_func(cast(Any, pool))

            # 2. EXECUTE & POST: Collect results
            for item in prep_data:
                # Normalize input/result pair from prep_func
                # We expect either an AsyncResult object or a tuple of (input_val, AsyncResult)
                input_val: Any = None
                result: Any = item

                if isinstance(item, tuple) and not hasattr(item, "get"):
                    if len(item) == 2:
                        input_val, result = item
                    elif len(item) > 2:
                        # If more than 2, assume first is input, second is result, ignore rest
                        input_val, result = item[0], item[1]
                    elif len(item) == 1:
                        result = item[0]

                try:
                    # We use a timeout in get() to ensure the main thread remains responsive
                    # to KeyboardInterrupt (SIGINT) on all platforms.
                    while True:
                        try:
                            data = result.get(timeout=1.0)
                            break
                        except multiprocessing.TimeoutError:
                            continue

                    if post_func:
                        data = post_func(data)

                    results.append(data)
                    success_count += 1

                except KeyboardInterrupt:
                    # Re-raise to be caught by the outer block
                    raise
                except Exception:
                    fail_msg = (
                        f"Task failed during '{desc}' (Input: {input_val})"
                        if input_val is not None
                        else f"Task failed during '{desc}'"
                    )
                    logger.bind(is_failure=True).opt(exception=True).error(fail_msg)

                    if input_val is not None:
                        failed_inputs.append(input_val)
                    failure_count += 1

                finally:
                    p.update(main_task, advance=1)

    except KeyboardInterrupt:
        interrupted = True
        logger.warning(f"\nExecution interrupted by user. Cleaning up {mode}s...")
        pool.terminate()
        raise
    except Exception as e:
        logger.error(f"Unexpected error in execution: {e}")
        pool.terminate()
        raise
    else:
        # Normal pool shutdown
        pool.close()
    finally:
        # 3. CRITICAL CLEANUP: Prevent semaphore leaks and zombie processes
        # We do NOT reset the progress queue here, as it can cause deadlocks
        # when multiple parallel runs occur or when a debugger is attached.
        # The queue and listener thread will persist for the life of the process.

        # A tiny delay helps workers finish flushing their final logs to the queue
        # before we block on pool.join(), which is critical for debugger stability.
        # However, we skip this if interrupted to ensure a fast Ctrl+C exit.
        if not interrupted:
            time.sleep(0.01)

        pool.join()

    return success_count, failure_count, failed_inputs, results


def process_run(
    prep_func: Callable[[Pool], Iterable[Any | tuple[Any, Any]]],
    post_func: Callable[[Any], None] | None,
    desc: str,
    total: int,
    workers: int | None = None,
) -> tuple[int, int, list[Any], list[Any]]:
    """
    Submit tasks to a process pool for execution.

    Parameters
    ----------
    prep_func : Callable[[Pool], Iterable[Any | tuple[Any, Any]]]
        Preparation function for submitting tasks to the multiprocessing.Pool.
    post_func : Callable[[Any], None] | None
        Function to handle each task result.
    desc : str
        Description for progress tracking.
    total : int
        Number of items to process.
    workers : int | None, optional
        Number of worker processes.

    Returns
    -------
    tuple[int, int, list[Any], list[Any]]
        (success_count, failure_count, failed_inputs, results).
    """
    return execute("process", prep_func, post_func, desc, total, workers)


def thread_run(
    prep_func: Callable[[ThreadPool], Iterable[Any | tuple[Any, Any]]],
    post_func: Callable[[Any], None] | None,
    desc: str,
    total: int,
    workers: int | None = None,
) -> tuple[int, int, list[Any], list[Any]]:
    """
    Submit tasks to a thread pool for execution.

    Parameters
    ----------
    prep_func : Callable[[ThreadPool], Iterable[Any | tuple[Any, Any]]]
        Preparation function for submitting tasks to the ThreadPool.
    post_func : Callable[[Any], None] | None
        Function to handle each task result.
    desc : str
        Description for progress tracking.
    total : int
        Number of items to process.
    workers : int | None, optional
        Number of worker threads.

    Returns
    -------
    tuple[int, int, list[Any], list[Any]]
        (success_count, failure_count, failed_inputs, results).
    """
    return execute("thread", prep_func, post_func, desc, total, workers)


# --- High-level streaming API (parallel.map / parallel.starmap) ---


@dataclass
class RunResult:
    """
    Outcome of a parallel.map / parallel.starmap run.

    Supports legacy tuple unpacking:
    ``successes, failures, failed_inputs, results = run``.
    """

    successes: int = 0
    failures: int = 0
    failed_inputs: list[Any] = field(default_factory=list)
    results: list[Any] = field(default_factory=list)
    duration: float = 0.0
    interrupted: bool = False

    def __iter__(self) -> Iterator[Any]:
        return iter((self.successes, self.failures, self.failed_inputs, self.results))


def _run_stream(
    worker: Callable[..., Any],
    inputs: Iterable[Any],
    *,
    mode: Literal["process", "thread"],
    desc: str | None,
    total: int | None,
    workers: int | None,
    on_error: Literal["collect", "raise"],
    unordered: bool,
    collect: bool,
    post_func: Callable[[Any], Any] | None,
    starmap_mode: bool,
    maxtasksperchild: int | None,
) -> RunResult:
    # Spawn-safety: identical guard to execute()
    if mode == "process" and multiprocessing.current_process().name != "MainProcess":
        logger.warning(
            f"⚠️  parallel.map('{desc or getattr(worker, '__name__', 'map')}') was called "
            "outside an 'if __name__ == \"__main__\":' block.\n"
            "Chronos gracefully intercepted this to prevent a multiprocessing fork bomb."
        )
        return RunResult()

    n_workers = workers if workers is not None else (os.cpu_count() or 1)

    if total is None:
        if isinstance(inputs, Sized):
            total = len(inputs)
        else:
            hint = length_hint(inputs)
            total = hint if hint > 0 else None

    desc_name = desc or getattr(worker, "__name__", "parallel.map")
    queue = logger.get_progress_queue()

    pool: Pool | ThreadPool
    if mode == "process":
        pool = Pool(
            processes=n_workers,
            initializer=_worker_init,
            initargs=(queue,),
            maxtasksperchild=maxtasksperchild,
        )
    else:
        pool = ThreadPool(processes=n_workers, initializer=_worker_init, initargs=(queue,))

    # Bounded submission window keeps memory flat for unbounded input iterables.
    window = max(4 * n_workers, 8)
    slots = threading.Semaphore(window)
    stop_event = threading.Event()
    feed_done = threading.Event()
    feed_error: list[Exception] = []
    in_flight: OrderedDict[AsyncResult[Any], Any] = OrderedDict()
    if_lock = threading.Lock()
    result = RunResult()

    def feeder() -> None:
        """Consume `inputs` lazily, keeping at most `window` tasks in flight."""
        try:
            for item in inputs:
                acquired = False
                while not stop_event.is_set():
                    if slots.acquire(timeout=0.1):
                        acquired = True
                        break
                if not acquired:
                    return  # Interrupted; stop feeding
                ar = pool.apply_async(worker, item if starmap_mode else (item,))
                with if_lock:
                    in_flight[ar] = item
        except Exception as exc:  # Surface input-generator failures to the caller
            feed_error.append(exc)
        finally:
            feed_done.set()

    start = time.perf_counter()
    interrupted = False

    try:
        with logger.progress(transient=False) as p:
            main_task = p.add_task(f"[green]{desc_name}", total=total)

            def handle(ar: AsyncResult[Any], item: Any) -> None:
                """Collect one completed task: count, log failures, advance progress."""
                try:
                    data = ar.get()
                    if post_func is not None:
                        data = post_func(data)
                except KeyboardInterrupt:
                    raise
                except Exception:
                    result.failures += 1
                    logger.bind(is_failure=True).opt(exception=True).error(
                        f"Task failed during '{desc_name}' (Input: {item!r})"
                    )
                    result.failed_inputs.append(item)
                    if on_error == "raise":
                        raise
                else:
                    result.successes += 1
                    if collect:
                        result.results.append(data)
                finally:
                    with if_lock:
                        in_flight.pop(ar, None)
                    slots.release()
                    p.update(main_task, advance=1)

            threading.Thread(target=feeder, daemon=True, name="chronos-feeder").start()

            while in_flight or not feed_done.is_set():
                if not in_flight:
                    time.sleep(0.01)  # Feeder still working (slow/blocking input)
                    continue

                if unordered:
                    with if_lock:
                        ready = [(ar, itm) for ar, itm in in_flight.items() if ar.ready()]
                    if not ready:
                        time.sleep(0.01)
                        continue
                    for ar, itm in ready:
                        handle(ar, itm)
                else:
                    # Strict input order: only the oldest in-flight task is
                    # eligible. Poll ready() (never raises); handle() does the
                    # get() so worker exceptions are routed through failure
                    # logging instead of escaping the collection loop.
                    with if_lock:
                        ar, itm = next(iter(in_flight.items()))
                    if not ar.ready():
                        time.sleep(0.01)
                        continue
                    handle(ar, itm)

            if feed_error:
                raise feed_error[0]

    except KeyboardInterrupt:
        interrupted = True
        logger.warning(f"\nExecution interrupted by user. Cleaning up {mode}s...")
        pool.terminate()
    except Exception:
        pool.terminate()
        raise
    else:
        pool.close()
    finally:
        stop_event.set()  # Unblock the feeder if it is waiting for a slot
        if not interrupted:
            time.sleep(0.01)  # Let workers flush final logs (see execute())
        pool.join()
        result.duration = time.perf_counter() - start
        result.interrupted = interrupted

    return result


def map(
    worker: Callable[[Any], Any],
    inputs: Iterable[Any],
    *,
    mode: Literal["process", "thread"] = "process",
    desc: str | None = None,
    total: int | None = None,
    workers: int | None = None,
    on_error: Literal["collect", "raise"] = "collect",
    unordered: bool = False,
    collect: bool = True,
    post_func: Callable[[Any], Any] | None = None,
    maxtasksperchild: int | None = None,
) -> RunResult:
    """
    Execute `worker` over `inputs` in parallel with lazy, streaming submission.

    Unlike :func:`execute`, inputs are never materialized: a feeder thread
    consumes `inputs` lazily and keeps a bounded window of tasks in flight, so
    unbounded sources (generators, file iterators) run with flat memory use.

    Parameters
    ----------
    worker : Callable[[Any], Any]
        Task function taking one input item. Must be picklable in process mode.
    inputs : Iterable[Any]
        Any iterable of input items (list, generator, file object, ...).
    mode : {"process", "thread"}
        Execution strategy. Default "process".
    desc : str | None, optional
        Progress bar label. Defaults to `worker.__name__`.
    total : int | None, optional
        Expected task count for the progress bar. Auto-detected via len()
        when inputs is Sized; None renders an indeterminate bar.
    workers : int | None, optional
        Worker count. Defaults to CPU count.
    on_error : {"collect", "raise"}, optional
        "collect" (default) records failures and continues; "raise" propagates
        the first worker exception after logging it.
    unordered : bool, optional
        Process completions out of submission order (keeps progress live when
        early tasks are slow). Default False (results in input order).
    collect : bool, optional
        Accumulate results in RunResult.results. Set False to stream results
        through `post_func` without accumulating.
    post_func : Callable[[Any], Any] | None, optional
        Per-result transform; its exceptions count as task failures.
    maxtasksperchild : int | None, optional
        Process mode only: recycle worker processes after N tasks.

    Returns
    -------
    RunResult
        successes, failures, failed_inputs, results, duration, interrupted.
        On Ctrl+C the partial result is returned with interrupted=True.
    """
    return _run_stream(
        worker,
        inputs,
        mode=mode,
        desc=desc,
        total=total,
        workers=workers,
        on_error=on_error,
        unordered=unordered,
        collect=collect,
        post_func=post_func,
        starmap_mode=False,
        maxtasksperchild=maxtasksperchild,
    )


def starmap(
    worker: Callable[..., Any],
    inputs: Iterable[tuple[Any, ...]],
    *,
    mode: Literal["process", "thread"] = "process",
    desc: str | None = None,
    total: int | None = None,
    workers: int | None = None,
    on_error: Literal["collect", "raise"] = "collect",
    unordered: bool = False,
    collect: bool = True,
    post_func: Callable[[Any], Any] | None = None,
    maxtasksperchild: int | None = None,
) -> RunResult:
    """
    Like :func:`map`, but each input item is a tuple of arguments unpacked
    into `worker` (i.e. ``worker(*item)``).
    """
    return _run_stream(
        worker,
        inputs,
        mode=mode,
        desc=desc,
        total=total,
        workers=workers,
        on_error=on_error,
        unordered=unordered,
        collect=collect,
        post_func=post_func,
        starmap_mode=True,
        maxtasksperchild=maxtasksperchild,
    )


def process_map(worker: Callable[[Any], Any], inputs: Iterable[Any], **kwargs: Any) -> RunResult:
    """parallel.map with mode="process"."""
    return map(worker, inputs, mode="process", **kwargs)


def thread_map(worker: Callable[[Any], Any], inputs: Iterable[Any], **kwargs: Any) -> RunResult:
    """parallel.map with mode="thread"."""
    return map(worker, inputs, mode="thread", **kwargs)
