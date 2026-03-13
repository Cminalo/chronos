"""
Parallel execution engine for Chronos.

This module provides a unified interface for multiprocess and multithreaded execution,
with integrated progress reporting and error handling.
"""

from __future__ import annotations

import multiprocessing
import os
import time
from multiprocessing.pool import Pool, ThreadPool
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal

from chronos import logger

if TYPE_CHECKING:
    from multiprocessing.queues import Queue


def _worker_init(queue: Queue[Any] | None) -> None:
    """
    Initialize a worker process or thread with the global progress queue.

    Parameters
    ----------
    queue : multiprocessing.Queue[Any] | None
        The progress queue used for inter-process communication of progress updates.
    """
    if queue is not None:
        logger.set_progress_queue(queue)


def execute(
    mode: Literal["process", "thread"],
    prep_func: Callable[[Pool | ThreadPool], Iterable[Any | tuple[Any, Any]]],
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
        The number of worker processes or threads to spawn. Defaults to the CPU count or pool default.

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
            f"⚠️  parallel.process_run('{desc}') was called outside an 'if __name__ == \"__main__\":' block.\n"
            "Chronos gracefully intercepted this to prevent a multiprocessing fork bomb.\n"
            "Please wrap your top-level execution code in the main block to ensure correct behavior."
        )
        return 0, 0, [], []

    queue = logger.get_progress_queue()
    PoolClass = Pool if mode == "process" else ThreadPool

    success_count = 0
    failure_count = 0
    failed_inputs = []
    results = []

    # Initialize the pool outside the progress context to ensure we can close/join it correctly.
    pool = PoolClass(processes=workers, initializer=_worker_init, initargs=(queue,))

    try:
        with logger.progress(transient=False) as p:
            main_task = p.add_task(f"[green]{desc}", total=total)

            # 1. PREP: Submit tasks to the pool
            prep_data = prep_func(pool)

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
                    # result.get() blocks until the specific task finishes.
                    # It returns exactly what the worker function returned (None, value, or tuple).
                    data = result.get()

                    if post_func:
                        data = post_func(data)

                    results.append(data)
                    success_count += 1

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
