"""Regression tests for Rust <-> Python UTC timestamp consistency (v14.1.0)."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pytest

from lattice_brain.graph._kg_fsutil import _recency_score, _safe_iso_from_stat_mtime
from lattice_brain.utils import local_now, now_iso, parse_iso, utc_now_iso


def _set_tz(monkeypatch: pytest.MonkeyPatch, tz_value: str) -> None:
    monkeypatch.setenv("TZ", tz_value)
    if hasattr(time, "tzset"):
        time.tzset()


@pytest.fixture
def restore_tz():
    original = os.environ.get("TZ")
    yield
    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    if hasattr(time, "tzset"):
        time.tzset()


@pytest.mark.parametrize("tz_name", ["Asia/Seoul", "America/New_York", "UTC"])
def test_local_now_and_now_iso_are_utc_under_non_utc_timezones(
    monkeypatch: pytest.MonkeyPatch, restore_tz: None, tz_name: str
) -> None:
    """Python's local_now()/now_iso() must emit offset-free UTC matching Rust's SystemClock."""
    _set_tz(monkeypatch, tz_name)

    before = datetime.now(timezone.utc).replace(tzinfo=None)
    stamp_dt = local_now()
    stamp_iso = now_iso()
    after = datetime.now(timezone.utc).replace(tzinfo=None)

    assert stamp_dt.tzinfo is None
    assert before <= stamp_dt <= after

    # Offset-free YYYY-MM-DDTHH:MM:SS matching Rust's SystemClock::now_iso().
    assert len(stamp_iso) == 19
    assert "+" not in stamp_iso and not stamp_iso.endswith("Z")

    parsed = parse_iso(stamp_iso)
    assert parsed is not None
    assert parsed.tzinfo is None
    skew_seconds = abs((parsed - before.replace(microsecond=0)).total_seconds())
    assert skew_seconds <= 2.0, (
        f"Unexpected timezone skew under TZ={tz_name}: {skew_seconds}s "
        f"(stamp={stamp_iso}, utc={before.isoformat(timespec='seconds')})"
    )


def test_now_iso_matches_rust_iso_seconds_for_and_utc_now_iso(
    monkeypatch: pytest.MonkeyPatch, restore_tz: None
) -> None:
    """now_iso() matches the offset-free prefix of utc_now_iso() even on UTC+9."""
    _set_tz(monkeypatch, "Asia/Seoul")

    naive_iso = now_iso()
    aware_iso = utc_now_iso(timespec="seconds")
    assert aware_iso.endswith("+00:00")

    naive_dt = parse_iso(naive_iso)
    aware_dt = parse_iso(aware_iso)
    assert naive_dt is not None and aware_dt is not None
    diff = abs((naive_dt - aware_dt.replace(tzinfo=None)).total_seconds())
    assert diff <= 2.0


def test_recency_score_and_stat_mtime_have_no_9h_skew_under_utc_plus_9(
    monkeypatch: pytest.MonkeyPatch, restore_tz: None
) -> None:
    """Freshly written UTC stamps and file mtimes score ~1.0 recency under UTC+9."""
    _set_tz(monkeypatch, "Asia/Seoul")

    fresh_naive_utc = now_iso()
    fresh_aware_utc = utc_now_iso(timespec="seconds")
    assert _recency_score(fresh_naive_utc) > 0.999
    assert _recency_score(fresh_aware_utc) > 0.999

    # Known epoch 2026-08-01T00:00:00Z matches Rust's clock::iso_seconds_for(1_785_542_400).
    assert _safe_iso_from_stat_mtime(1_785_542_400) == "2026-08-01T00:00:00"
