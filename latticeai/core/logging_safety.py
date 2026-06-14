"""Helpers for keeping sensitive values out of logs."""

from __future__ import annotations

import logging
from typing import Any

from latticeai.core.security import redact_secret_text, redact_secrets

_LOG_FILTER_INSTALLED = False


def mask_telegram_bot_token(value: Any) -> str:
    """Return ``value`` as text with Telegram bot token and other secrets redacted."""

    return redact_secret_text(str(value))


def safe_log_text(value: Any) -> str:
    """Sanitize text before it is sent to application logs."""

    return mask_telegram_bot_token(value)


def _safe_log_arg(value: Any) -> Any:
    return redact_secrets(value)


def install_sensitive_log_filter() -> None:
    """Install a process-wide log-record sanitizer for known secret shapes."""

    global _LOG_FILTER_INSTALLED
    if _LOG_FILTER_INSTALLED:
        return

    original_factory = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = original_factory(*args, **kwargs)
        record.msg = _safe_log_arg(record.msg)
        if record.args:
            record.args = _safe_log_arg(record.args)
        return record

    logging.setLogRecordFactory(factory)
    _LOG_FILTER_INSTALLED = True
