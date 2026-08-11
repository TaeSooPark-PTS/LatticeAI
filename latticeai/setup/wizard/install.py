"""Verification, repair, and the SSE install stream.

The acting half of the wizard: prove a binary or module is really there, try a
bounded automatic repair when it is not, and stream one SSE event per step
while pip/brew/model-load/auth actions run. Every subprocess goes through the
process-audit command plan, and every plan is confirmed against the token the
caller returned.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import subprocess
import sys
import time
from typing import Any, AsyncIterator, Dict, List, Tuple

from latticeai.core.quiet import quiet
from latticeai.services.process_audit import (
    CommandConfirmationError,
    append_process_audit_event,
    command_plan,
    require_command_confirmation,
)
from latticeai.setup.wizard.paths import (
    _module_available,
    _package_module,
    _which_any,
    repair_path_for,
)
from latticeai.setup.wizard.plans import _sse, _verify_action_confirmation


def _verify_binary(binary: str, version_args: List[str] | None = None, timeout: int = 20) -> Tuple[bool, str]:
    repair_path_for(binary)
    found = _which_any(binary)
    if not found:
        return False, f"{binary} 실행 파일을 PATH에서 찾지 못했습니다."
    args = [found, *(version_args or ["--version"])]
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as e:
        return False, str(e)
    output = (completed.stdout or completed.stderr or "").strip().splitlines()
    if completed.returncode == 0:
        return True, output[0] if output else found
    return False, (completed.stderr or completed.stdout or f"returncode={completed.returncode}")[-400:]


async def _wait_for_binary(binary: str, seconds: int = 300) -> Tuple[bool, str]:
    deadline = time.time() + seconds
    while time.time() < deadline:
        ok, msg = _verify_binary(binary)
        if ok:
            return True, msg
        await asyncio.sleep(2)
    return False, f"{binary} 설치 완료를 제한 시간 안에 감지하지 못했습니다."

# ── Installation Stream ───────────────────────────────────────────────────────

def _verify_action(action: Dict[str, Any]) -> Tuple[bool, str]:
    atype = action.get("type")
    if atype == "pip":
        modules = action.get("verify_modules") or [_package_module(pkg) for pkg in action.get("packages", [])]
        missing = [module for module in modules if not _module_available(module)]
        if missing:
            return False, "Python 모듈 감지 실패: " + ", ".join(missing)
        return True, "Python 모듈 import 테스트 통과"
    binary = action.get("binary")
    if binary:
        return _verify_binary(binary)
    return True, "검증 항목 없음"


async def _repair_action(
    action: Dict[str, Any],
    *,
    confirmation_token: str | None = None,
    actor: str | None = None,
) -> Tuple[bool, str]:
    binary = action.get("binary")
    if binary:
        repair_path_for(binary)
        ok, msg = _verify_binary(binary)
        if ok:
            return True, f"PATH 자동 보정 완료: {msg}"
    if action.get("type") == "pip":
        packages = action.get("packages", [])
        if packages:
            if not _verify_action_confirmation(action, confirmation_token, name="repair_action"):
                return False, "설치 명령 확인 토큰이 일치하지 않습니다."
            for pkg in packages:
                success, err = await _pip_install(pkg, confirmed=True, actor=actor)
                if not success:
                    return False, err
            return _verify_action(action)
    return False, "자동 복구 방법을 찾지 못했습니다."


async def install_stream(
    items: List[Dict],
    router: Any,
    *,
    confirmation_token: str | None = None,
    user_email: str | None = None,
) -> AsyncIterator[str]:
    for item in items:
        item_id    = item.get("id", "unknown")
        name       = item.get("name", item_id)
        action     = item.get("action") or {}
        atype      = action.get("type")

        if not atype:
            yield _sse({"id": item_id, "status": "skipped", "msg": f"{name} — 이미 준비됨"})
            await asyncio.sleep(0.04)
            continue

        yield _sse({"id": item_id, "status": "starting", "msg": f"{name} 준비 중..."})

        if atype == "pip":
            packages = action.get("packages", [])
            token = confirmation_token or action.get("confirmation_token") or (action.get("command_plan") or {}).get("confirmation_token")
            if not _verify_action_confirmation(action, token, name=str(item_id)):
                yield _sse({"id": item_id, "status": "error", "msg": "설치 명령 확인 토큰이 일치하지 않습니다."})
                continue
            ok = True
            for pkg in packages:
                yield _sse({"id": item_id, "status": "running", "msg": f"pip install {pkg} ..."})
                success, err = await _pip_install(pkg, confirmed=True, actor=user_email)
                if success:
                    yield _sse({"id": item_id, "status": "progress", "msg": f"{pkg} 설치 완료"})
                else:
                    yield _sse({"id": item_id, "status": "error", "msg": f"{pkg} 실패: {err[:400]}"})
                    ok = False
                    break
            if ok:
                yield _sse({"id": item_id, "status": "running", "msg": f"{name} 동작 테스트 중..."})
                verified, detail = _verify_action(action)
                if verified:
                    yield _sse({"id": item_id, "status": "done", "msg": f"{name} 설치 · 검증 완료 ✅\n{detail}"})
                else:
                    yield _sse({"id": item_id, "status": "running", "msg": f"검증 실패 — 자동 복구 중...\n{detail}"})
                    repaired, repair_msg = await _repair_action(action, confirmation_token=token, actor=user_email)
                    yield _sse({"id": item_id, "status": "done" if repaired else "error", "msg": repair_msg[:500]})

        elif atype == "brew":
            pkg = action.get("package", "")
            token = confirmation_token or action.get("confirmation_token") or (action.get("command_plan") or {}).get("confirmation_token")
            if not _verify_action_confirmation(action, token, name=str(item_id)):
                yield _sse({"id": item_id, "status": "error", "msg": "설치 명령 확인 토큰이 일치하지 않습니다."})
                continue
            yield _sse({"id": item_id, "status": "running", "msg": f"brew install {pkg} ..."})
            success, err = await _brew_install(pkg, confirmed=True, actor=user_email)
            if success:
                yield _sse({"id": item_id, "status": "running", "msg": "설치 완료 감지 · PATH 보정 중..."})
                binary = action.get("binary")
                if binary:
                    repair_path_for(binary)
                verified, detail = _verify_action(action)
                if verified:
                    yield _sse({"id": item_id, "status": "done", "msg": f"{name} 설치 · 연결 · 검증 완료 ✅\n{detail}"})
                else:
                    yield _sse({"id": item_id, "status": "running", "msg": f"검증 실패 — 자동 복구 중...\n{detail}"})
                    repaired, repair_msg = await _repair_action(action, confirmation_token=token, actor=user_email)
                    yield _sse({"id": item_id, "status": "done" if repaired else "error", "msg": repair_msg[:500]})
            else:
                url = action.get("official_url") or action.get("url")
                if url:
                    yield _sse({"id": item_id, "status": "auth", "msg": f"자동 설치 실패 — 공식 다운로드 페이지를 엽니다.\n{err[:240]}", "auth_url": url})
                    open_url(url)
                yield _sse({"id": item_id, "status": "error", "msg": f"실패: {err[:400]}"})

        elif atype == "load_model":
            model_id = action.get("model_id", "")
            yield _sse({"id": item_id, "status": "running",
                        "msg": f"모델 다운로드 · 로딩 중...\n{model_id}\n(용량에 따라 수 분 소요)"})
            try:
                await router.load_model(model_id)
                yield _sse({"id": item_id, "status": "done", "msg": f"{name} 로드 완료 ✅"})
            except Exception as e:
                yield _sse({"id": item_id, "status": "error", "msg": f"로드 실패: {str(e)[:400]}"})

        elif atype == "auth":
            url = action.get("url", "")
            yield _sse({"id": item_id, "status": "auth",
                        "msg": "브라우저에서 인증 페이지를 엽니다...", "auth_url": url})
            open_url(url)
            yield _sse({"id": item_id, "status": "waiting",
                        "msg": "브라우저에서 인증 완료 후 계속하세요"})

        elif atype == "url":
            url = action.get("url", "")
            yield _sse({"id": item_id, "status": "auth",
                        "msg": "설치 페이지를 브라우저에서 엽니다...", "auth_url": url})
            open_url(url)
            binary = action.get("binary")
            if binary:
                yield _sse({"id": item_id, "status": "waiting",
                            "msg": f"{binary} 설치 완료를 자동 감지하는 중입니다..."})
                ok, detail = await _wait_for_binary(binary)
                if ok:
                    repair_path_for(binary)
                    yield _sse({"id": item_id, "status": "done",
                                "msg": f"{name} 설치 · PATH 연결 · 검증 완료 ✅\n{detail}"})
                else:
                    yield _sse({"id": item_id, "status": "error",
                                "msg": f"{detail}\n공식 페이지에서 설치 후 다시 시도하세요."})
            else:
                yield _sse({"id": item_id, "status": "waiting",
                            "msg": "브라우저에서 설치 또는 인증을 완료한 뒤 다시 시도하세요"})

        else:
            yield _sse({"id": item_id, "status": "error", "msg": f"알 수 없는 액션: {atype}"})

    yield _sse({"status": "complete", "msg": "모든 항목 처리 완료!"})


async def _pip_install(
    package: str,
    *,
    confirmation_token: str | None = None,
    confirmed: bool = False,
    actor: str | None = None,
) -> Tuple[bool, str]:
    command = [sys.executable, "-m", "pip", "install", "--upgrade", package]
    plan = command_plan(
        command,
        name=f"pip:{package}",
        purpose="setup_wizard_install",
        metadata={"package": package},
    )
    try:
        if not confirmed:
            require_command_confirmation(command, confirmation_token, purpose="setup_wizard_install")
        append_process_audit_event("setup_wizard_install", plan=plan, status="started", user_email=actor)
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        stderr_text = stderr.decode(errors="replace")
        append_process_audit_event(
            "setup_wizard_install",
            plan=plan,
            status="finished",
            user_email=actor,
            returncode=proc.returncode,
            stderr=stderr_text,
        )
        if proc.returncode == 0:
            return True, ""
        return False, stderr_text
    except CommandConfirmationError as e:
        append_process_audit_event("setup_wizard_install", plan=plan, status="denied", user_email=actor, error=str(e))
        return False, str(e)
    except asyncio.TimeoutError:
        append_process_audit_event("setup_wizard_install", plan=plan, status="timeout", user_email=actor)
        return False, "설치 시간 초과 (10분)"
    except Exception as e:
        append_process_audit_event("setup_wizard_install", plan=plan, status="error", user_email=actor, error=str(e))
        return False, str(e)


async def _brew_install(
    package: str,
    *,
    confirmation_token: str | None = None,
    confirmed: bool = False,
    actor: str | None = None,
) -> Tuple[bool, str]:
    brew = shutil.which("brew")
    if not brew:
        return False, "Homebrew 미설치 — https://brew.sh 에서 설치하세요"
    command = [brew, "install", package]
    plan = command_plan(
        command,
        name=f"brew:{package}",
        purpose="setup_wizard_install",
        metadata={"package": package},
    )
    try:
        if not confirmed:
            require_command_confirmation(command, confirmation_token, purpose="setup_wizard_install")
        append_process_audit_event("setup_wizard_install", plan=plan, status="started", user_email=actor)
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        stderr_text = stderr.decode(errors="replace")
        append_process_audit_event(
            "setup_wizard_install",
            plan=plan,
            status="finished",
            user_email=actor,
            returncode=proc.returncode,
            stderr=stderr_text,
        )
        if proc.returncode == 0:
            return True, ""
        return False, stderr_text
    except CommandConfirmationError as e:
        append_process_audit_event("setup_wizard_install", plan=plan, status="denied", user_email=actor, error=str(e))
        return False, str(e)
    except asyncio.TimeoutError:
        append_process_audit_event("setup_wizard_install", plan=plan, status="timeout", user_email=actor)
        return False, "설치 시간 초과 (5분)"
    except Exception as e:
        append_process_audit_event("setup_wizard_install", plan=plan, status="error", user_email=actor, error=str(e))
        return False, str(e)


def open_url(url: str) -> None:
    command: List[str]
    try:
        system = platform.system()
        if system == "Darwin":
            command = ["open", url]
            plan = command_plan(command, name="open_url", purpose="setup_wizard_open_url")
            append_process_audit_event("setup_wizard_open_url", plan=plan, status="started")
            subprocess.Popen(command)
            append_process_audit_event("setup_wizard_open_url", plan=plan, status="spawned")
        elif system == "Windows":
            command = ["os.startfile", url]
            plan = command_plan(command, name="open_url", purpose="setup_wizard_open_url")
            append_process_audit_event("setup_wizard_open_url", plan=plan, status="started")
            os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606 — fixed program, arguments validated by the caller
            append_process_audit_event("setup_wizard_open_url", plan=plan, status="spawned")
        else:
            command = ["xdg-open", url]
            plan = command_plan(command, name="open_url", purpose="setup_wizard_open_url")
            append_process_audit_event("setup_wizard_open_url", plan=plan, status="started")
            subprocess.Popen(command)
            append_process_audit_event("setup_wizard_open_url", plan=plan, status="spawned")
    except Exception as exc:
        try:
            append_process_audit_event(
                "setup_wizard_open_url",
                plan=command_plan(["open_url", url], name="open_url", purpose="setup_wizard_open_url"),
                status="error",
                error=str(exc),
            )
        except Exception:
            quiet()
        pass
