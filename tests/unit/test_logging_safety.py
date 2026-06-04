import logging

from latticeai.core.logging_safety import (
    install_sensitive_log_filter,
    mask_telegram_bot_token,
    safe_log_text,
)


def test_masks_telegram_bot_token_in_api_url():
    raw = "GET https://api.telegram.org/bot8663786689:AA_exampleSecret/sendMessage"

    assert mask_telegram_bot_token(raw) == (
        "GET https://api.telegram.org/bot8663786689:REDACTED/sendMessage"
    )


def test_masks_bare_telegram_token_in_exception_text():
    raw = "Request failed for 8663786689:AA_exampleSecret"

    assert safe_log_text(raw) == "Request failed for bot8663786689:REDACTED"


def test_keeps_non_secret_text_unchanged():
    assert safe_log_text("telegram unavailable") == "telegram unavailable"


def test_log_record_factory_masks_httpx_style_urls(caplog):
    install_sensitive_log_filter()
    url = "https://api.telegram.org/bot8663786689:AA_exampleSecret/getUpdates?timeout=30"

    logger = logging.getLogger("tests.logging_safety")
    with caplog.at_level(logging.INFO, logger=logger.name):
        logger.info('HTTP Request: GET %s "HTTP/1.1 200 OK"', url)

    assert "AA_exampleSecret" not in caplog.text
    assert "bot8663786689:REDACTED" in caplog.text
