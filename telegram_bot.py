"""Compatibility shim for the historical root Telegram bot module."""

from latticeai.integrations import telegram_bot as _impl


if __name__ == "__main__":
    import asyncio

    try:
        asyncio.run(_impl.run_bot())
    except KeyboardInterrupt:
        pass
else:
    import sys

    sys.modules[__name__] = _impl
