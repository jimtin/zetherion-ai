"""Logging configuration for Zetherion AI."""

import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

from zetherion_ai.config import get_settings


def exception_fields(exc: BaseException | None = None) -> dict[str, Any]:
    """Return structured exception details for JSON/event logs."""
    resolved = exc
    tb = exc.__traceback__ if exc is not None else None
    if resolved is None:
        _exc_type, resolved, tb = sys.exc_info()
    if resolved is None:
        return {
            "error_type": None,
            "error_message": None,
            "traceback": None,
        }

    message = str(resolved).strip() or repr(resolved)
    formatted = "".join(
        traceback.format_exception(type(resolved), resolved, tb),
    ).strip()
    return {
        "error_type": type(resolved).__name__,
        "error_message": message,
        "traceback": formatted or None,
    }


def setup_logging() -> None:
    """Configure structured logging with console and file outputs."""
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Create log directory if file logging is enabled
    if settings.log_to_file:
        try:
            log_dir = Path(settings.log_directory)
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            # If we can't create log directory, continue with console-only logging
            print(f"Warning: Could not create log directory: {e}", file=sys.stderr)
            settings.log_to_file = False

    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        handlers=[],  # We'll add handlers manually
    )

    # Console handler (existing behavior)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    logging.root.addHandler(console_handler)

    # File handler (new feature)
    file_handler = None
    if settings.log_to_file:
        try:
            file_handler = RotatingFileHandler(
                filename=settings.log_file_path,
                maxBytes=settings.log_file_max_bytes,
                backupCount=settings.log_file_backup_count,
                encoding="utf-8",
            )
            file_handler.setLevel(log_level)
            logging.root.addHandler(file_handler)
        except Exception as e:
            print(f"Warning: Could not setup file logging: {e}", file=sys.stderr)
            file_handler = None

    # Configure structlog processors
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.format_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure formatters for handlers
    # Console: colored in dev, JSON in prod
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            (
                structlog.dev.ConsoleRenderer(colors=True)  # type: ignore[list-item]
                if settings.is_development
                else structlog.processors.JSONRenderer()
            ),
        ]
    )
    console_handler.setFormatter(console_formatter)

    # File: always JSON for easy parsing
    json_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ]
    )
    if file_handler:
        file_handler.setFormatter(json_formatter)

    # Error log file (WARNING+) for focused analysis
    if settings.log_to_file and settings.log_error_file_enabled:
        try:
            error_file_handler = RotatingFileHandler(
                filename=settings.error_log_file_path,
                maxBytes=settings.log_file_max_bytes,
                backupCount=settings.log_file_backup_count,
                encoding="utf-8",
            )
            error_file_handler.setLevel(logging.WARNING)
            error_file_handler.setFormatter(json_formatter)
            logging.root.addHandler(error_file_handler)
        except Exception as e:
            print(f"Warning: Could not setup error file logging: {e}", file=sys.stderr)

    # Reduce noise from third-party packages
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a logger instance."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]
