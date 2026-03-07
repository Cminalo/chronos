import multiprocessing
from multiprocessing.pool import Pool, ThreadPool
from typing import Callable, Any, Iterable, Literal
from chronos import logger

def _worker_init(queue: multiprocessing.Queue):
    """
    Initializer for pool workers.
    Sets up the log and progress routing back to the main process.
    """
    if queue:
        logger.set_progress_queue(queue)

def execute(
    mode: Literal["process", "thread"],
    prep_func: Callable[[Any], Iterable[Any | tuple[Any, Any]]],
    post_func: Callable[[Any], None],
    desc: str,
    total: int,
    workers: int | None = None
) -> tuple[int, int, list[Any]]:
    """
    Executes a parallel workflow using a prep -> execute -> post pattern.
    """
    # Safety check to prevent fork bombs on macOS/Windows (spawn method)
    if mode == "process" and multiprocessing.current_process().name != "MainProcess":
        raise RuntimeError(
            "Fatal: parallel.process_run() must be called from within an "
            "'if __name__ == \"__main__\":' block to prevent recursive spawning."
        )

    queue = logger.get_progress_queue()
    PoolClass = Pool if mode == "process" else ThreadPool
    
    success_count = 0
    failure_count = 0
    failed_inputs = []

    # Start the global progress manager
    with logger.progress(transient=False) as p:
        main_task = p.add_task(f"[green]{desc}", total=total)
        
        with PoolClass(
            processes=workers, 
            initializer=_worker_init, 
            initargs=(queue,)
        ) as pool:
            
            try:
                # 1. PREP: Submit work to the pool
                prep_data = prep_func(pool)
                
                # 2. EXECUTE & POST: Wait for results and process them
                for item in prep_data:
                    # Normalize input/result pair
                    if isinstance(item, tuple) and not hasattr(item, "get"):
                        input_val, result = item
                    else:
                        input_val, result = None, item

                    try:
                        # get() blocks until this specific task completes.
                        data = result.get()
                        
                        # 3. POST
                        if post_func:
                            post_func(data)
                        
                        success_count += 1
                            
                    except Exception:
                        # Log the failure once. Because we bind is_failure=True, it will 
                        # automatically be routed to the failures.log file while also 
                        # appearing beautifully in the terminal and standard logs.
                        fail_msg = f"Task failed during '{desc}' (Input: {input_val})" if input_val is not None else f"Task failed during '{desc}'"
                        logger.bind(is_failure=True).opt(exception=True).error(fail_msg)
                        
                        if input_val is not None:
                            failed_inputs.append(input_val)
                        failure_count += 1
                        
                    finally:
                        p.update(main_task, advance=1)
                        
            except KeyboardInterrupt:
                logger.warning("Parallel execution interrupted by user. Shutting down pool...")
                pool.terminate()
                pool.join()
                raise
            
            pool.close()
            pool.join()
    
    return success_count, failure_count, failed_inputs

def process_run(prep_func: Callable, post_func: Callable, desc: str, total: int, workers: int | None = None) -> tuple[int, int, list[Any]]:
    """Helper to run execute() in multiprocess mode."""
    return execute("process", prep_func, post_func, desc, total, workers)

def thread_run(prep_func: Callable, post_func: Callable, desc: str, total: int, workers: int | None = None) -> tuple[int, int, list[Any]]:
    """Helper to run execute() in multithread mode."""
    return execute("thread", prep_func, post_func, desc, total, workers)
