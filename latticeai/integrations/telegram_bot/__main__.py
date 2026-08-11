"""Script entry: ``python -m latticeai.integrations.telegram_bot``.

The single file carried this block under ``if __name__ == "__main__"``. A
package cannot: ``__init__`` is never ``__main__``. Moving it here verbatim is
what keeps the ``-m`` invocation working after the v11.3.0 split.
"""

import asyncio

from latticeai.core.quiet import quiet
from latticeai.integrations.telegram_bot import run_bot

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        quiet()
