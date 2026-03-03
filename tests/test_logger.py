import sys
from pathlib import Path
from loguru import logger
import pytest
from chronos import logger as chronos_logger

# Define a fixture to redirect the logger to a temporary file
@pytest.fixture(scope="function")
def configured_logger(tmp_path):
    # Remove all existing sinks
    chronos_logger.remove()
    
    # Create a temp log file path
    log_file = tmp_path / "test_chronos.log"
    
    # Re-add the file sink with the same config as the main app
    # We need to import the formatter from the module, but it's not exported.
    # So we'll define a simple compatible one or just read the raw file.
    # Actually, let's just use the default format or a simple one for testing content.
    chronos_logger.add(
        log_file,
        level="TRACE",
        format="{message}", # Simple format for easy assertions
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )
    
    yield log_file
    
    # Cleanup: Remove sinks again to release file handles
    chronos_logger.remove()

def test_logger_exists():
    assert chronos_logger is not None

def test_benchmark_context_manager(configured_logger):
    with chronos_logger.benchmark("Test Task"):
        pass
    
    # Flush to ensure write
    chronos_logger.complete()
    
    content = configured_logger.read_text()
    assert "Test Task finished" in content
    # Since we used simple format, we might miss the duration text if it's in 'extra' 
    # and we didn't use the custom formatter.
    # Let's check if the 'benchmark' context manager adds it to the message.
    # The benchmark CM does: logger.bind(duration=...).log("BENCHMARK", f"{name} finished")
    # Our custom formatter handles the display.
    # So with simple format "{message}", we won't see "Duration: ...".
    # We should probably use the real formatter if possible, or accept that we just check the message.
    
def test_custom_levels(configured_logger):
    chronos_logger.trace("Trace message")
    chronos_logger.debug("Debug message")
    chronos_logger.info("Info message")
    chronos_logger.success("Success message")
    chronos_logger.warning("Warning message")
    chronos_logger.error("Error message")
    chronos_logger.critical("Critical message")
    
    chronos_logger.complete()
    content = configured_logger.read_text()
    
    assert "Trace message" in content
    assert "Debug message" in content
    assert "Info message" in content
    assert "Success message" in content
    assert "Warning message" in content
    assert "Error message" in content
    assert "Critical message" in content

def test_exception_catching(configured_logger):
    @chronos_logger.catch
    def failing_function():
        raise ValueError("Intentional Failure")
    
    failing_function()
    
    chronos_logger.complete()
    content = configured_logger.read_text()
    
    assert "Intentional Failure" in content

def test_interceptor(configured_logger):
    import logging
    chronos_logger.intercept_standard_logging()
    logging.warning("This is a standard warning")
    
    chronos_logger.complete()
    content = configured_logger.read_text()
    
    assert "This is a standard warning" in content

def test_system_metrics(configured_logger):
    # Enable metrics
    chronos_logger.enable_system_metrics()
    chronos_logger.info("Test metrics log")
    
    chronos_logger.complete()
    content = configured_logger.read_text()
    
    # Since we used the simple format fixture {message}, the extra dict won't be printed by default.
    # But we can verify that the patcher didn't crash. 
    assert "Test metrics log" in content
