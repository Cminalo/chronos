import time
import sys
from chronos import logger

def main():
    logger.info("Initializing system...")
    
    with logger.progress(transient=False) as p:
        main_task = p.add_task("[green]Total Pipeline", total=3)
        
        for i in range(3):
            sub_task = p.add_task(f"[cyan]Agent {i} processing", total=5)
            for j in range(5):
                p.update(sub_task, advance=1)
                
                # These logs should elegantly jump OVER the progress bars
                logger.info(f"Agent {i} processing chunk {j}")
                
                time.sleep(0.1)
                
                if i == 1 and j == 2:
                    try:
                        raise ValueError("Something went wrong!")
                    except ValueError:
                        # Full traceback should also jump over without glitching!
                        logger.opt(exception=True).error("Agent 1 hit a snag!")
                        
            p.update(main_task, advance=1)
            logger.success(f"Agent {i} completed.")
            
    logger.info("Pipeline complete.")

if __name__ == "__main__":
    main()
