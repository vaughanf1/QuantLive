"""Structured logging configuration using loguru."""

import logging
import sys

from loguru import logger


class InterceptHandler(logging.Handler):
    """Redirect stdlib logging to loguru.

    Captures log records from uvicorn, sqlalchemy, alembic, and other
    libraries that use the standard logging module, forwarding them to
    loguru for unified structured output.
    """

    def emit(self, record: logging.LogRecord) -> None:
        # Map stdlib log level to loguru level
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where the logged message originated
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logging(log_level: str = "INFO", json_output: bool = False) -> None:
    """Configure loguru as the sole logging handler.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_output: If True, output structured JSON logs.
    """
    # Remove default loguru handler
    logger.remove()

    # Add stderr handler with formatting or JSON serialization
    if json_output:
        logger.add(
            sys.stderr,
            level=log_level.upper(),
            serialize=True,
        )
    else:
        logger.add(
            sys.stderr,
            level=log_level.upper(),
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                "<level>{message}</level>"
            ),
            colorize=True,
        )

    # Intercept all stdlib logging and route through loguru
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # Explicitly set levels for noisy libraries
    for lib_logger in ("uvicorn", "uvicorn.access", "uvicorn.error", "sqlalchemy.engine"):
        logging.getLogger(lib_logger).handlers = [InterceptHandler()]
