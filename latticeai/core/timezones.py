"""Shared timezone helper (피드백 #5 / item 7).

문제: audit 로그 timestamp는 ``datetime.now()`` (시스템 로컬 시간)으로 기록되는데,
security_dashboard는 "오늘 이벤트 수"를 ``datetime.utcnow().date()`` (UTC) 기준으로
계산해서 한국 사용자에게 events_today가 어긋났다.

해결: 모든 날짜/시간 기준을 하나의 timezone 헬퍼로 통일한다.

- 환경변수 ``LATTICE_TZ`` (또는 호환용 ``LTCAI_TZ``)가 있으면 그 시간대를 쓴다.
  예: ``LATTICE_TZ=Asia/Seoul``.
- 없으면 시스템 로컬 시간대를 쓴다 (기존 audit timestamp와 동일한 기준).

이렇게 하면 "타임스탬프를 쓸 때 쓰는 시간대" === "오늘을 계산할 때 쓰는 시간대" 가
항상 일치한다.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, tzinfo
from typing import Optional

try:  # py3.9+
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - 매우 오래된 런타임
    ZoneInfo = None  # type: ignore

logger = logging.getLogger(__name__)

_TZ_ENV_VARS = ("LATTICE_TZ", "LTCAI_TZ")


def _system_local_tz() -> tzinfo:
    # tz-aware 시스템 로컬 시간대.
    return datetime.now().astimezone().tzinfo  # type: ignore[return-value]


def get_timezone() -> tzinfo:
    """설정된 시간대를 돌려준다. (환경변수 → 시스템 로컬 순)"""
    for var in _TZ_ENV_VARS:
        name = (os.environ.get(var) or "").strip()
        if not name:
            continue
        if ZoneInfo is None:
            logger.warning("zoneinfo 사용 불가, %s=%s 무시하고 시스템 로컬 사용", var, name)
            break
        try:
            return ZoneInfo(name)
        except Exception:
            logger.warning("알 수 없는 시간대 %s=%s, 시스템 로컬로 대체", var, name)
            break
    return _system_local_tz()


def now(tz: Optional[tzinfo] = None) -> datetime:
    """설정된 시간대의 현재 시각 (tz-aware)."""
    return datetime.now(tz or get_timezone())


def now_iso(tz: Optional[tzinfo] = None) -> str:
    """ISO8601 문자열. audit timestamp 기록용."""
    return now(tz).isoformat()


def today_str(tz: Optional[tzinfo] = None) -> str:
    """오늘 날짜 ``YYYY-MM-DD``. events_today 계산용."""
    return now(tz).date().isoformat()


def tz_name() -> str:
    """설정된 시간대 이름(있으면 환경변수 값, 없으면 'local')."""
    for var in _TZ_ENV_VARS:
        name = (os.environ.get(var) or "").strip()
        if name:
            return name
    return "local"


__all__ = ["get_timezone", "now", "now_iso", "today_str", "tz_name"]
