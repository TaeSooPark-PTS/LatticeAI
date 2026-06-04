"""Helpers for keeping sensitive values out of logs."""

from __future__ import annotations

import logging
import re
from typing import Any

_TELEGRAM_BOT_TOKEN_RE = re.compile(r"\bbot(\d{5,20}):([A-Za-z0-9_-]{8,})")
_TELEGRAM_BARE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_:-])(\d{5,20}):([A-Za-z0-9_-]{8,})(?![A-Za-z0-9_-])"
)
_LOG_FILTER_INSTALLED = False


def mask_telegram_bot_token(value: Any) -> str:
    """Return ``value`` as text with Telegram bot token secrets redacted."""

    text = str(value)
    text = _TELEGRAM_BOT_TOKEN_RE.sub(r"bot\1:REDACTED", text)
    return _TELEGRAM_BARE_TOKEN_RE.sub(r"bot\1:REDACTED", text)


def safe_log_text(value: Any) -> str:
    """Sanitize text before it is sent to application logs."""

    return mask_telegram_bot_token(value)


def _safe_log_arg(value: Any) -> Any:
    if isinstance(value, str):
        return mask_telegram_bot_token(value)
    if isinstance(value, tuple):
        return tuple(_safe_log_arg(item) for item in value)
    if isinstance(value, list):
        return [_safe_log_arg(item) for item in value]
    if isinstance(value, dict):
        return {_safe_log_arg(key): _safe_log_arg(item) for key, item in value.items()}

    text = str(value)
    masked = mask_telegram_bot_token(text)
    return masked if masked != text else value


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
