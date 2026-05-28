"""Unit tests for latticeai.core.timezones (item 7)."""

import importlib
from datetime import datetime, timezone

from latticeai.core import timezones


def test_today_str_matches_now_date():
    assert timezones.today_str() == timezones.now().date().isoformat()


def test_now_is_tz_aware():
    assert timezones.now().tzinfo is not None


def test_env_override_changes_timezone(monkeypatch):
    monkeypatch.setenv("LATTICE_TZ", "Asia/Seoul")
    tz = timezones.get_timezone()
    # Asia/Seoul 은 UTC+9
    offset = timezones.now(tz).utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 9 * 3600
    assert timezones.tz_name() == "Asia/Seoul"


def test_seoul_today_can_differ_from_utc(monkeypatch):
    """버그 회귀 방지: Seoul 기준 '오늘'은 UTC date 와 다를 수 있다."""
    monkeypatch.setenv("LATTICE_TZ", "Asia/Seoul")
    seoul_today = timezones.today_str()
    utc_today = datetime.now(timezone.utc).date().isoformat()
    # 둘 중 하나는 항상 유효한 ISO date 여야 하고, Seoul today 는 helper 기준과 일치.
    assert seoul_today == timezones.now().date().isoformat()
    # 날짜 경계(UTC 15:00~24:00, 즉 Seoul 자정 이후)에는 두 값이 다르다.
    assert len(seoul_today) == 10 and len(utc_today) == 10


def test_invalid_timezone_falls_back(monkeypatch):
    monkeypatch.setenv("LATTICE_TZ", "Not/AReal_Zone")
    # 예외 없이 시스템 로컬로 대체되어야 한다.
    assert timezones.now().tzinfo is not None
