"""Lazy compatibility facade over :mod:`latticeai.app_factory`.

The application used to be assembled here at import time (MLX/GPU init,
~15 singletons, file creation under the data dir). All of that now lives in
``latticeai.app_factory.create_app``. This module keeps every historical
module-level name (``app``, ``KNOWLEDGE_GRAPH``, ``load_users``,
``save_to_history``, …) importable for tests and legacy callers via module
``__getattr__`` — construction happens on *first attribute access*, never on
import. Importing ``latticeai.server_app`` has no side effects.
"""

from __future__ import annotations

from typing import Any, List

from latticeai.runtime.namespace_runtime import SERVER_APP_EXPORTS


def _runtime():
    from latticeai.app_factory import get_shared_runtime

    return get_shared_runtime()


def __getattr__(name: str) -> Any:
    if name.startswith("__") and name.endswith("__"):
        # Never let dunder probes (importlib, inspect, pickling) trigger the
        # full application construction.
        raise AttributeError(name)
    if name not in SERVER_APP_EXPORTS:
        raise AttributeError(f"module 'latticeai.server_app' has no attribute '{name}'")
    try:
        return getattr(_runtime(), name)
    except AttributeError as exc:
        raise AttributeError(
            f"module 'latticeai.server_app' has no attribute '{name}'"
        ) from exc


def __dir__() -> List[str]:
    return sorted(set(globals()) | set(SERVER_APP_EXPORTS))


def main() -> None:
    from latticeai.app_factory import main as _main

    _main()


if __name__ == "__main__":
    main()
