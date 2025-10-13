import logging

from config import LOG_LEVEL


def configure(logger_name: str = "main") -> logging.Logger:
    """Initialize basic logging configuration and return a named logger."""
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="[%(asctime)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger(logger_name)
