import logging
import structlog
from quantum_edge_core.logging_setup import setup_logging


def main():
    setup_logging()

    logger = structlog.get_logger("test_logging")

    # 1. Structured Info Log
    logger.info("Starting logging verification", user="l_garnatko", env="test")

    # 2. Structured Error with Context
    try:
        _ = 1 / 0
    except ZeroDivisionError:
        logger.error("Calculation failed", error="ZeroDivisionError", input=1)
        # Note: structlog should auto-capture exc_info if configured, or we pass exc_info=True
        # Our config uses StackInfoRenderer which often requires explicit stack info request?
        # Actually format_exc_info processor handles exc_info=True.
        # Let's try explicit exc_info=True
        logger.exception("Calculation failed with exception", input=0)

    # 3. Standard Library Interception
    std_logger = logging.getLogger("stdlib_test")
    std_logger.warning("This is a standard library warning", extra={"some_id": 123})

    print("\n[VERIFICATION] Check output above for colored/JSON logs.")


if __name__ == "__main__":
    main()
