from __future__ import annotations

import sys
import types

import latticeai.cli.entrypoint as ltcai_cli


def test_cli_starts_the_worker_factory(monkeypatch):
    ran = []

    monkeypatch.setattr(sys, "argv", ["LTCAI", "--host", "127.0.0.1", "--port", "8999"])
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        types.SimpleNamespace(run=lambda *args, **kwargs: ran.append((args, kwargs))),
    )

    ltcai_cli.main()

    assert ran
    kwargs = ran[0][1]
    assert kwargs["factory"] is True
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8999
    assert ran[0][0][0] == "latticeai.worker_app:create_worker_app"


def test_cli_has_no_tunnel_or_telegram_hooks():
    assert not hasattr(ltcai_cli, "_start_tunnel")
    assert not hasattr(ltcai_cli, "threading")
