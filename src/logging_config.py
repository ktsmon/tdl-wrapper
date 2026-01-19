"""Logging configuration for TDL wrapper."""

import logging
import logging.handlers
from pathlib import Path
from typing import Dict, Any


def setup_logging(config: Dict[str, Any]):
    """
    Setup logging configuration.

    Args:
        config: Logging configuration dictionary
    """
    log_level = getattr(logging, config.get('level', 'INFO').upper())
    log_file = config.get('file', 'tdl_wrapper.log')
    max_bytes = config.get('max_bytes', 10485760)  # 10MB
    backup_count = config.get('backup_count', 5)

    # Create log directory if needed
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Setup root logger
    logger = logging.getLogger('tdl_wrapper')
    logger.setLevel(log_level)

    # Remove existing handlers
    logger.handlers = []

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)

    # File handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count
    )
    file_handler.setLevel(log_level)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)

    # Add handlers
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str = 'tdl_wrapper') -> logging.Logger:
    """
    Get a logger instance.

    Args:
        name: Logger name

    Returns:
        Logger instance
    """
    return logging.getLogger(name)
