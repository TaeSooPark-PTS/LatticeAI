import asyncio
from types import SimpleNamespace

import pytest

import auto_setup
from latticeai.services import model_engines
from latticeai.services.model_errors import ModelRuntimeError
from latticeai.services.process_audit import (
    CommandConfirmationError,
    command_plan,
    verify_command_confirmation,
)
from setup_wizard import install_stream


def test_command_plan_redacts_secret_args_and_confirms() -> None:
    plan = command_plan(["installer", "--token", "super-secret-value"], name="demo")

    assert plan["command_preview"] == ["installer", "--token", "[REDACTED]"]
    assert verify_command_confirmation(
        ["installer", "--token", "super-secret-value"],
        plan["confirmation_token"],
        purpose="installer",
    )


def test_auto_setup_apply_plan_requires_plan_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    step = auto_setup.InstallStep(name="demo", why="test", command=["echo", "ok"])
    plan = auto_setup.InstallPlan(package_manager=None, steps=[step])
    events = []

    monkeypatch.setattr(auto_setup, "append_process_audit_event", lambda *args, **kwargs: events.append((args, kwargs)))
    monkeypatch.setattr(
        auto_setup.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )

    with pytest.raises(CommandConfirmationError):
        auto_setup.apply_plan(plan, confirm=True)

    result = auto_setup.apply_plan(
        plan,
        confirm=True,
        confirmation_token=plan.to_json()["confirmation_token"],
    )

    assert result[0]["returncode"] == 0
    assert result[0]["command_hash"]
    assert any(kwargs.get("status") == "started" for _args, kwargs in events)


def test_setup_install_stream_refuses_missing_confirmation_token() -> None:
    item = {"id": "pip_demo", "name": "Demo", "action": {"type": "pip", "packages": ["demo-package"]}}

    async def collect() -> str:
        return "".join([chunk async for chunk in install_stream([item], router=None)])

    body = asyncio.run(collect())

    assert "설치 명령 확인 토큰" in body


def test_engine_install_refuses_missing_confirmation_token() -> None:
    plan = model_engines.engine_install_plan("local_mlx")

    assert plan["confirmation_token"]
    with pytest.raises(ModelRuntimeError) as exc:
        model_engines.install_engine("local_mlx")

    assert exc.value.status_code == 403
    assert exc.value.detail["status"] == "confirmation_required"
    assert exc.value.detail["install_plan"]["command_hash"] == plan["command_hash"]
