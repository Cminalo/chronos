"""
Example: Handling Multiple Return Types in Chronos Parallel Execution

This example demonstrates how a worker function can return:
1. None
2. A single value
3. A tuple of multiple datatypes (e.g., int, dict, list)

It also shows how the post_func gracefully unpacks and handles these returns.
"""

from chronos import logger, parallel
import time
import random

# --- The Worker Function ---
# A worker can return whatever you want. The `execute` engine will take 
# the exact return value and pass it directly into the `post_func`.
def flexible_worker(task_id: int):
    # Simulate work
    time.sleep(random.uniform(0.01, 0.05))
    
    # Example 1: Returning None
    if task_id % 3 == 0:
        logger.debug(f"Task {task_id} returning None")
        return None
        
    # Example 2: Returning a single value
    elif task_id % 3 == 1:
        logger.debug(f"Task {task_id} returning a single integer")
        return task_id * 10
        
    # Example 3: Returning multiple datatypes as a tuple
    else:
        logger.debug(f"Task {task_id} returning multiple datatypes")
        metadata = {"original_id": task_id, "status": "processed"}
        metrics = [1.2, 3.4, 5.6]
        return (task_id, metadata, metrics)

# --- The Preparation Function ---
def prepare_tasks(pool):
    data = range(10)
    # Format: (input_val, async_result)
    # This format enables task recovery if a worker crashes.
    return [(i, pool.apply_async(flexible_worker, (i,))) for i in data]

# --- The Post-Processing Function ---
# This list lives in the main process and collects the results safely.
collected_results = []

def process_results(worker_result):
    """
    This function receives exactly what `flexible_worker` returned.
    We handle the different return types here before appending to our list.
    """
    if worker_result is None:
        logger.warning("Received a None result, skipping.")
        
    elif isinstance(worker_result, int):
        logger.success(f"Received single value: {worker_result}")
        collected_results.append(worker_result)
        
    elif isinstance(worker_result, tuple):
        # Unpack the multiple datatypes
        task_id, meta_dict, metric_list = worker_result
        logger.info(f"Received complex tuple - ID: {task_id} | Status: {meta_dict['status']} | Avg Metric: {sum(metric_list)/len(metric_list):.2f}")
        collected_results.append(meta_dict)

# --- Main Execution Block ---
def main():
    logger.info("Starting Parallel execution with multiple return types")
    
    # 3. GET YOUR RETURNS:
    # The `results` list contains every value returned by your worker functions.
    success, failure, failed_inputs, results = parallel.process_run(
        prep_func=prepare_tasks,
        post_func=process_results, # post_func still runs for side-effects
        desc="Processing Diverse Returns",
        total=10,
        workers=4
    )
    
    logger.summary("Return Types Example", success_count=success, failure_count=failure)
    
    # You can now access your dictionaries/tuples directly from the results list!
    logger.info(f"Successfully collected {len(results)} items via direct return.")
    
    for i, item in enumerate(results):
        logger.debug(f"Result {i}: {type(item).__name__} = {item}")

if __name__ == "__main__":
    main()
