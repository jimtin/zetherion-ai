"""Unit tests for the logging configuration module."""

import logging
from logging.handlers import RotatingFileHandler
from unittest.mock import MagicMock, patch

import pytest
import structlog

from zetherion_ai.logging import get_logger, setup_logging


@pytest.fixture(autouse=True)
def _reset_root_logger():
    """Reset root logger state before and after each test."""
    original_handlers = logging.root.handlers[:]
    original_level = logging.root.level
    logging.root.handlers.clear()
    logging.root.setLevel(logging.WARNING)
    yield
    logging.root.handlers.clear()
    logging.root.handlers.extend(original_handlers)
    logging.root.setLevel(original_level)


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_setup_logging_calls_basic_config_with_debug(self):
        """Test that setup_logging calls basicConfig with correct level for DEBUG."""
        mock_settings = MagicMock()
        mock_settings.log_level = "DEBUG"
        mock_settings.log_to_file = False
        mock_settings.is_development = True

        with patch("zetherion_ai.logging.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.logging.logging.basicConfig") as mock_basic:
                setup_logging()

        mock_basic.assert_called_once_with(format="%(message)s", level=logging.DEBUG, handlers=[])

    def test_setup_logging_calls_basic_config_with_warning(self):
        """Test that setup_logging calls basicConfig with WARNING level."""
        mock_settings = MagicMock()
        mock_settings.log_level = "WARNING"
        mock_settings.log_to_file = False
        mock_settings.is_development = False

        with patch("zetherion_ai.logging.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.logging.logging.basicConfig") as mock_basic:
                setup_logging()

        mock_basic.assert_called_once_with(format="%(message)s", level=logging.WARNING, handlers=[])

    def test_setup_logging_invalid_level_defaults_to_info(self):
        """Test setup_logging falls back to INFO for invalid log level."""
        mock_settings = MagicMock()
        mock_settings.log_level = "NONEXISTENT"
        mock_settings.log_to_file = False
        mock_settings.is_development = False

        with patch("zetherion_ai.logging.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.logging.logging.basicConfig") as mock_basic:
                setup_logging()

        mock_basic.assert_called_once_with(format="%(message)s", level=logging.INFO, handlers=[])

    def test_setup_logging_adds_console_handler(self):
        """Test that setup_logging adds a StreamHandler to root logger."""
        mock_settings = MagicMock()
        mock_settings.log_level = "INFO"
        mock_settings.log_to_file = False
        mock_settings.is_development = True

        with patch("zetherion_ai.logging.get_settings", return_value=mock_settings):
            setup_logging()

        stream_handlers = [h for h in logging.root.handlers if type(h) is logging.StreamHandler]
        assert len(stream_handlers) >= 1

    def test_setup_logging_console_handler_has_structlog_formatter(self):
        """Test that the console handler gets a ProcessorFormatter."""
        mock_settings = MagicMock()
        mock_settings.log_level = "INFO"
        mock_settings.log_to_file = False
        mock_settings.is_development = True

        with patch("zetherion_ai.logging.get_settings", return_value=mock_settings):
            setup_logging()

        # Find our StreamHandler (not a subclass like RotatingFileHandler)
        console_handlers = [h for h in logging.root.handlers if type(h) is logging.StreamHandler]
        assert len(console_handlers) >= 1
        formatter = console_handlers[0].formatter
        assert isinstance(formatter, structlog.stdlib.ProcessorFormatter)

    def test_setup_logging_reduces_third_party_noise(self):
        """Test that setup_logging sets third-party loggers to WARNING."""
        mock_settings = MagicMock()
        mock_settings.log_level = "DEBUG"
        mock_settings.log_to_file = False
        mock_settings.is_development = True

        with patch("zetherion_ai.logging.get_settings", return_value=mock_settings):
            setup_logging()

        assert logging.getLogger("discord").level == logging.WARNING
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING

    def test_setup_logging_configures_structlog(self):
        """Test that setup_logging calls structlog.configure with correct params."""
        mock_settings = MagicMock()
        mock_settings.log_level = "INFO"
        mock_settings.log_to_file = False
        mock_settings.is_development = True

        with patch("zetherion_ai.logging.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.logging.structlog.configure") as mock_configure:
                setup_logging()

        mock_configure.assert_called_once()
        call_kwargs = mock_configure.call_args[1]
        assert call_kwargs["context_class"] is dict
        assert call_kwargs["cache_logger_on_first_use"] is True

    def test_setup_logging_development_uses_console_renderer(self):
        """Test that development mode uses ConsoleRenderer for console output."""
        mock_settings = MagicMock()
        mock_settings.log_level = "INFO"
        mock_settings.log_to_file = False
        mock_settings.is_development = True

        with patch("zetherion_ai.logging.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.logging.structlog.dev.ConsoleRenderer") as mock_renderer:
                setup_logging()

        mock_renderer.assert_called_once_with(colors=True)

    def test_setup_logging_production_uses_json_renderer(self):
        """Test that production mode uses JSONRenderer for console output."""
        mock_settings = MagicMock()
        mock_settings.log_level = "INFO"
        mock_settings.log_to_file = False
        mock_settings.is_development = False

        with patch("zetherion_ai.logging.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.logging.structlog.processors.JSONRenderer") as mock_renderer:
                setup_logging()

        # Called twice: once for console formatter (prod mode) and once for json_formatter
        assert mock_renderer.call_count == 2


class TestSetupLoggingFileHandler:
    """Tests for file handler configuration in setup_logging."""

    def test_setup_logging_with_file_logging_enabled(self, tmp_path):
        """Test that file logging creates a RotatingFileHandler."""
        log_dir = tmp_path / "logs"
        mock_settings = MagicMock()
        mock_settings.log_level = "INFO"
        mock_settings.log_to_file = True
        mock_settings.log_directory = str(log_dir)
        mock_settings.log_file_path = str(log_dir / "zetherion_ai.log")
        mock_settings.log_file_max_bytes = 10485760
        mock_settings.log_file_backup_count = 5
        mock_settings.is_development = False
        mock_settings.log_error_file_enabled = False

        with patch("zetherion_ai.logging.get_settings", return_value=mock_settings):
            setup_logging()

        file_handlers = [h for h in logging.root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 1
        assert file_handlers[0].maxBytes == 10485760
        assert file_handlers[0].backupCount == 5

    def test_setup_logging_file_handler_json_formatter(self, tmp_path):
        """Test that file handler uses JSON ProcessorFormatter."""
        log_dir = tmp_path / "logs"
        mock_settings = MagicMock()
        mock_settings.log_level = "DEBUG"
        mock_settings.log_to_file = True
        mock_settings.log_directory = str(log_dir)
        mock_settings.log_file_path = str(log_dir / "zetherion_ai.log")
        mock_settings.log_file_max_bytes = 1048576
        mock_settings.log_file_backup_count = 3
        mock_settings.is_development = True
        mock_settings.log_error_file_enabled = False

        with patch("zetherion_ai.logging.get_settings", return_value=mock_settings):
            setup_logging()

        file_handlers = [h for h in logging.root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 1
        assert isinstance(file_handlers[0].formatter, structlog.stdlib.ProcessorFormatter)

    def test_setup_logging_creates_log_directory(self, tmp_path):
        """Test that setup_logging creates the log directory when it doesn't exist."""
        log_dir = tmp_path / "new_logs"
        mock_settings = MagicMock()
        mock_settings.log_level = "INFO"
        mock_settings.log_to_file = True
        mock_settings.log_directory = str(log_dir)
        mock_settings.log_file_path = str(log_dir / "zetherion_ai.log")
        mock_settings.log_file_max_bytes = 10485760
        mock_settings.log_file_backup_count = 5
        mock_settings.is_development = False
        mock_settings.log_error_file_enabled = False

        assert not log_dir.exists()

        with patch("zetherion_ai.logging.get_settings", return_value=mock_settings):
            setup_logging()

        assert log_dir.exists()

    def test_setup_logging_log_directory_creation_failure(self):
        """Test that log directory creation failure disables file logging gracefully."""
        mock_settings = MagicMock()
        mock_settings.log_level = "INFO"
        mock_settings.log_to_file = True
        mock_settings.log_directory = "/nonexistent/deeply/nested/path"
        mock_settings.is_development = False

        with patch("zetherion_ai.logging.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.logging.Path.mkdir", side_effect=PermissionError("denied")):
                # Should not raise; falls back to console-only
                setup_logging()

        # log_to_file should have been set to False
        assert mock_settings.log_to_file is False

    def test_setup_logging_file_handler_creation_failure(self, tmp_path):
        """Test that file handler creation failure is handled gracefully."""
        log_dir = tmp_path / "logs"
        mock_settings = MagicMock()
        mock_settings.log_level = "INFO"
        mock_settings.log_to_file = True
        mock_settings.log_directory = str(log_dir)
        mock_settings.log_file_path = str(log_dir / "zetherion_ai.log")
        mock_settings.log_file_max_bytes = 10485760
        mock_settings.log_file_backup_count = 5
        mock_settings.is_development = False
        mock_settings.log_error_file_enabled = False

        with patch("zetherion_ai.logging.get_settings", return_value=mock_settings):
            with patch(
                "zetherion_ai.logging.RotatingFileHandler",
                side_effect=PermissionError("cannot write"),
            ):
                # Should not raise
                setup_logging()

        file_handlers = [h for h in logging.root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 0

    def test_setup_logging_no_file_handler_when_disabled(self):
        """Test that no file handler is added when log_to_file is False."""
        mock_settings = MagicMock()
        mock_settings.log_level = "INFO"
        mock_settings.log_to_file = False
        mock_settings.is_development = False

        with patch("zetherion_ai.logging.get_settings", return_value=mock_settings):
            setup_logging()

        file_handlers = [h for h in logging.root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 0

    def test_setup_logging_creates_error_file_handler(self, tmp_path):
        """Test that error file handler is created at WARNING level."""
        log_dir = tmp_path / "logs"
        mock_settings = MagicMock()
        mock_settings.log_level = "INFO"
        mock_settings.log_to_file = True
        mock_settings.log_directory = str(log_dir)
        mock_settings.log_file_path = str(log_dir / "zetherion_ai.log")
        mock_settings.error_log_file_path = str(log_dir / "zetherion_ai_error.log")
        mock_settings.log_file_max_bytes = 10485760
        mock_settings.log_file_backup_count = 5
        mock_settings.is_development = False
        mock_settings.log_error_file_enabled = True

        with patch("zetherion_ai.logging.get_settings", return_value=mock_settings):
            setup_logging()

        file_handlers = [h for h in logging.root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 2
        # One at standard level, one at WARNING
        warning_handlers = [h for h in file_handlers if h.level == logging.WARNING]
        assert len(warning_handlers) == 1

    def test_setup_logging_no_error_file_when_disabled(self, tmp_path):
        """Test that error file handler is not created when disabled."""
        log_dir = tmp_path / "logs"
        mock_settings = MagicMock()
        mock_settings.log_level = "INFO"
        mock_settings.log_to_file = True
        mock_settings.log_directory = str(log_dir)
        mock_settings.log_file_path = str(log_dir / "zetherion_ai.log")
        mock_settings.log_file_max_bytes = 10485760
        mock_settings.log_file_backup_count = 5
        mock_settings.is_development = False
        mock_settings.log_error_file_enabled = False

        with patch("zetherion_ai.logging.get_settings", return_value=mock_settings):
            setup_logging()

        file_handlers = [h for h in logging.root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 1


class TestGetLogger:
    """Tests for get_logger function."""

    def test_get_logger_returns_bound_logger(self):
        """Test that get_logger returns a structlog logger."""
        logger = get_logger("test.module")
        assert logger is not None

    def test_get_logger_with_different_names(self):
        """Test that get_logger works with different module names."""
        logger1 = get_logger("module.a")
        logger2 = get_logger("module.b")
        assert logger1 is not None
        assert logger2 is not None
