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
    prep_func: Callable[[Any], Iterable[Any]],
    post_func: Callable[[Any], None],
    desc: str,
    total: int,
    workers: int | None = None
):
    """
    Executes a parallel workflow using a prep -> execute -> post pattern.
    
    Args:
        mode: "process" for CPU-bound tasks, "thread" for IO-bound tasks.
        prep_func: A function that takes the Pool as an argument and returns an iterable of async results.
                   Example: lambda pool: [pool.apply_async(my_func, (args,)) for _ in range(10)]
        post_func: A function called with the result of each completed task.
        desc: Description for the global progress bar.
        total: Expected number of tasks.
        workers: Number of parallel workers (defaults to os.cpu_count()).
    """
    queue = logger.get_progress_queue()
    PoolClass = Pool if mode == "process" else ThreadPool
    
    # Start the global progress manager
    success_count = 0
    failure_count = 0

    with logger.progress(transient=False) as p:
        main_task = p.add_task(f"[green]{desc}", total=total)
        
        # Initialize the pool with the Chronos worker setup
        with PoolClass(
            processes=workers, 
            initializer=_worker_init, 
            initargs=(queue,)
        ) as pool:
            
            try:
                # 1. PREP: Submit work to the pool
                async_results = prep_func(pool)
                
                # 2. EXECUTE & POST: Wait for results and process them
                for idx, result in enumerate(async_results):
                    try:
                        # get() blocks until this specific task completes.
                        # It will re-raise any exception caught in the worker.
                        data = result.get()
                        
                        # 3. POST
                        if post_func:
                            post_func(data)
                        
                        success_count += 1
                            
                    except Exception as e:
                        # Use logger.opt to preserve the rich tree-style traceback of the worker crash
                        logger.opt(exception=True).error(f"Worker task failed during '{desc}'")
                        failure_count += 1
                        
                    finally:
                        # Update progress regardless of success or failure
                        p.update(main_task, advance=1)
                        
            except KeyboardInterrupt:
                logger.warning("Parallel execution interrupted by user. Shutting down pool...")
                pool.terminate()
                pool.join()
                raise
            
            # Normal cleanup
            pool.close()
            pool.join()
    
    return success_count, failure_count

def process_run(prep_func: Callable, post_func: Callable, desc: str, total: int, workers: int | None = None):
    """Helper to run execute() in multiprocess mode."""
    return execute("process", prep_func, post_func, desc, total, workers)

def thread_run(prep_func: Callable, post_func: Callable, desc: str, total: int, workers: int | None = None):
    """Helper to run execute() in multithread mode."""
    return execute("thread", prep_func, post_func, desc, total, workers)
