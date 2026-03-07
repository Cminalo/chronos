"""
Example: Graceful Fork Bomb Prevention in Chronos

This file deliberately omits the `if __name__ == "__main__":` block to demonstrate 
how Chronos safely intercepts and neutralizes the multiprocessing fork bomb that
normally crashes macOS and Windows machines.

When run, it will print a loud warning but exit cleanly, returning control to your terminal or debugger.
"""

from chronos import logger, parallel

def simple_worker(x):
    return x * 2

def prep(pool):
    return [(i, pool.apply_async(simple_worker, (i,))) for i in range(5)]

logger.info("Calling process_run without a __main__ guard...")

# ⚠️ This is usually dangerous on macOS/Windows!
# Chronos will detect the spawn loop, print a warning, and return instantly.
success, fail, failed_inputs = parallel.process_run(
    prep_func=prep,
    post_func=lambda r: logger.info(f"Got {r}"),
    desc="Dangerous Execution",
    total=5
)

# In the MainProcess, this will print 0 successes because the child failed to start
# In the child processes, it will safely execute without creating a nested pool
logger.success(f"Execution finished cleanly. Success: {success}, Failures: {fail}")
