"""wp31: the ``create_app`` / ``main`` entrypoints and the server_app facade.

``tests/unit/test_app_factory.py`` runs the factory inside a sandboxed
*subprocess* (that is the point of those tests — a pristine interpreter proves
importing has no side effects), so none of it is measured in-process. The lines
left over here are the two entrypoints and the facade's ``__getattr__`` error
paths.

Construction seams are patched rather than executed: a real ``create_app()``
would assemble a second full runtime and write into the developer's real data
directory, which no unit test may do. What is asserted instead is the contract
each entrypoint owns — what it delegates to, and with what.
"""

from __future__ import annotations

import runpy
import sys
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from latticeai import app_factory, server_app
from latticeai.runtime.namespace_runtime import SERVER_APP_EXPORTS


def test_create_app_returns_the_app_of_a_freshly_built_runtime(monkeypatch):
    built: List[Any] = []
    sentinel_app = object()

    def fake_build_runtime(config=None):
        built.append(config)
        return SimpleNamespace(app=sentinel_app)

    monkeypatch.setattr(app_factory, "build_runtime", fake_build_runtime)
    config = SimpleNamespace(host="127.0.0.1", port=4825, app_mode="local")

    assert app_factory.create_app(config) is sentinel_app
    assert app_factory.create_app() is sentinel_app
    # The config is forwarded verbatim; the default is "no config supplied".
    assert built == [config, None]


def test_main_serves_the_shared_runtime_on_its_configured_address(monkeypatch, capsys):
    served: List[Dict[str, Any]] = []
    runtime = SimpleNamespace(
        app="fastapi-app",
        CONFIG=SimpleNamespace(host="127.0.0.1", port=4830, app_mode="local"),
    )
    fake_uvicorn = SimpleNamespace(
        run=lambda app, **kwargs: served.append({"app": app, **kwargs})
    )

    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(app_factory, "get_shared_runtime", lambda: runtime)

    app_factory.main()

    assert served == [
        {
            "app": "fastapi-app",
            "host": "127.0.0.1",
            "port": 4830,
            "log_level": "info",
        }
    ]
    banner = capsys.readouterr().out
    assert "http://127.0.0.1:4830" in banner
    assert "local mode" in banner


def test_server_app_main_delegates_to_the_factory(monkeypatch):
    calls: List[str] = []
    monkeypatch.setattr(app_factory, "main", lambda: calls.append("factory-main"))

    server_app.main()

    assert calls == ["factory-main"]


@pytest.mark.filterwarnings("ignore:.*found in sys.modules.*:RuntimeWarning")
def test_running_the_module_as_a_script_serves(monkeypatch):
    """``python -m latticeai.server_app`` must reach the factory entrypoint."""
    calls: List[str] = []
    monkeypatch.setattr(app_factory, "main", lambda: calls.append("factory-main"))

    namespace = runpy.run_module("latticeai.server_app", run_name="__main__")

    assert calls == ["factory-main"]
    assert callable(namespace["main"])


def test_unknown_attributes_never_trigger_construction(monkeypatch):
    built: List[str] = []
    monkeypatch.setattr(
        app_factory, "get_shared_runtime", lambda: built.append("built") or object()
    )

    with pytest.raises(AttributeError) as raised:
        server_app.no_such_runtime_name  # noqa: B018 — the access is the test

    assert "no attribute 'no_such_runtime_name'" in str(raised.value)
    assert built == [], "an unknown name must not build the runtime"


def test_an_exported_name_the_runtime_lacks_reads_as_a_module_attribute_error(
    monkeypatch,
):
    monkeypatch.setattr(app_factory, "get_shared_runtime", lambda: object())
    name = "KNOWLEDGE_GRAPH"
    assert name in SERVER_APP_EXPORTS

    with pytest.raises(AttributeError) as raised:
        getattr(server_app, name)

    # The message names the module, not the internal runtime object.
    assert str(raised.value) == (
        "module 'latticeai.server_app' has no attribute 'KNOWLEDGE_GRAPH'"
    )


def test_dunder_probes_are_refused_without_building(monkeypatch):
    built: List[str] = []
    monkeypatch.setattr(
        app_factory, "get_shared_runtime", lambda: built.append("built") or object()
    )

    with pytest.raises(AttributeError):
        server_app.__wrapped__  # noqa: B018 — the access is the test

    assert built == []
    assert set(SERVER_APP_EXPORTS) <= set(dir(server_app))
