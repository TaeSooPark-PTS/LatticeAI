"""UX funnel metrics — lightweight runtime counters (backlog #16, review §4.3).

Tracks the product's core value funnel with honest, local-only counters:

* ``file_requests``          — chat requests recognized as file intents;
* ``real_file_delivered``    — file intents that produced actual artifacts;
* ``code_only_responses``    — file intents that finished with only a
  code/prose answer (the failure mode the >95% real-file goal watches);
* ``agent_runs``             — completed chat agent runs (rate denominator);
* ``needs_review_runs``      — agent runs that ended in ``NEEDS_REVIEW``;
* ``ingest_completions``     — successful ingestion pipeline completions;
* ``recall_successes``       — chat turns whose context was grounded in at
  least one Brain node;
* ``approval_pauses``        — agent/workflow runs paused for human approval;
* ``approval_resumes``       — paused runs a human explicitly resumed
  (approved), the ``approval_resume_rate`` numerator.

TTFV ("time to first value") derives from two first-occurrence timestamps:
the first successful ingest and the first grounded recall/answer.

Design constraints (deliberate):

* **Cheap.** One JSON file under the data dir, atomic replace on write, a
  single ``threading.Lock`` — no background threads, no new dependencies.
* **Never breaks the product.** Every public method swallows I/O errors;
  metrics are advisory observability, not a gate.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

LOGGER = logging.getLogger(__name__)

COUNTER_NAMES = (
    "file_requests",
    "real_file_delivered",
    "code_only_responses",
    "agent_runs",
    "needs_review_runs",
    "ingest_completions",
    "recall_successes",
    "approval_pauses",
    "approval_resumes",
)

_FIRST_NAMES = ("first_ingest_at", "first_value_at")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _rate(numerator: int, denominator: int) -> Optional[float]:
    """Honest rate: ``None`` (not 0.0) when there is no denominator yet."""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


class FunnelMetricsService:
    """Thread-safe, JSON-persisted funnel counters."""

    def __init__(self, path: Any) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = self._load()

    # ── persistence ──────────────────────────────────────────────────────

    def _load(self) -> Dict[str, Any]:
        state: Dict[str, Any] = {name: 0 for name in COUNTER_NAMES}
        for name in _FIRST_NAMES:
            state[name] = None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return state
        except Exception as exc:  # noqa: BLE001 — corrupt metrics never break startup
            LOGGER.warning("funnel metrics load failed (%s); starting fresh", exc)
            return state
        if isinstance(raw, dict):
            for name in COUNTER_NAMES:
                try:
                    state[name] = max(0, int(raw.get(name) or 0))
                except (TypeError, ValueError):
                    state[name] = 0
            for name in _FIRST_NAMES:
                value = raw.get(name)
                state[name] = str(value) if value else None
        return state

    def _save_locked(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=self._path.name, dir=str(self._path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self._state, handle, ensure_ascii=False, indent=2)
                os.replace(tmp_name, self._path)
            finally:
                if os.path.exists(tmp_name):
                    try:
                        os.unlink(tmp_name)
                    except OSError:
                        pass
        except Exception as exc:  # noqa: BLE001 — metrics persistence is best-effort
            LOGGER.warning("funnel metrics save failed: %s", exc)

    # ── mutation ─────────────────────────────────────────────────────────

    def increment(self, name: str, by: int = 1) -> None:
        """Increment a known counter; unknown names are ignored (logged)."""
        if name not in COUNTER_NAMES:
            LOGGER.warning("funnel metrics: unknown counter %r ignored", name)
            return
        try:
            step = int(by)
        except (TypeError, ValueError):
            step = 1
        if step <= 0:
            return
        with self._lock:
            self._state[name] = int(self._state.get(name) or 0) + step
            self._save_locked()

    def _mark_first_locked(self, name: str) -> None:
        if not self._state.get(name):
            self._state[name] = _utc_now_iso()

    def record_ingest(self, *, duplicate: bool = False) -> None:
        """A successful ingestion completed; the first one starts the TTFV clock."""
        with self._lock:
            self._state["ingest_completions"] = (
                int(self._state.get("ingest_completions") or 0) + 1
            )
            self._mark_first_locked("first_ingest_at")
            self._save_locked()

    def record_recall_success(self) -> None:
        """A chat answer was grounded in the Brain; the first one ends TTFV."""
        with self._lock:
            self._state["recall_successes"] = (
                int(self._state.get("recall_successes") or 0) + 1
            )
            # First value only counts after knowledge actually entered the Brain.
            if self._state.get("first_ingest_at"):
                self._mark_first_locked("first_value_at")
            self._save_locked()

    # ── reads ────────────────────────────────────────────────────────────

    def ttfv_seconds(self) -> Optional[float]:
        with self._lock:
            first_ingest = _parse_iso(self._state.get("first_ingest_at"))
            first_value = _parse_iso(self._state.get("first_value_at"))
        if first_ingest is None or first_value is None:
            return None
        delta = (first_value - first_ingest).total_seconds()
        return round(delta, 1) if delta >= 0 else None

    def snapshot(self) -> Dict[str, Any]:
        """Counters + derived rates + actionable alerts for the admin surface."""
        with self._lock:
            counters = {name: int(self._state.get(name) or 0) for name in COUNTER_NAMES}
            firsts = {name: self._state.get(name) for name in _FIRST_NAMES}
        rates = {
            "real_file_rate": _rate(
                counters["real_file_delivered"], counters["file_requests"]
            ),
            "code_only_rate": _rate(
                counters["code_only_responses"], counters["file_requests"]
            ),
            "needs_review_rate": _rate(
                counters["needs_review_runs"], counters["agent_runs"]
            ),
            "approval_resume_rate": _rate(
                counters["approval_resumes"], counters["approval_pauses"]
            ),
        }
        return {
            "counters": counters,
            "firsts": firsts,
            "rates": rates,
            "alerts": funnel_alerts(counters, rates),
            "ttfv_seconds": self.ttfv_seconds(),
            "generated_at": _utc_now_iso(),
        }


# ── alerts (review 2026-07-27 P2 #10) ────────────────────────────────────────
# The funnel already measured the right things; nothing turned a bad number
# into a decision, so a regression was only visible to whoever opened the
# admin page. These thresholds convert rates into named, actionable signals.
#
# Minimum samples per rule exist so a single unlucky run never raises an
# alarm — an alert with n=1 is noise, and noisy alerts get ignored.

REAL_FILE_RATE_FLOOR = 0.95
CODE_ONLY_RATE_CEILING = 0.05
NEEDS_REVIEW_RATE_CEILING = 0.25
APPROVAL_RESUME_RATE_FLOOR = 0.5
MIN_SAMPLES = 10


def _alert(
    key: str, severity: str, ko: str, en: str, **detail: Any
) -> Dict[str, Any]:
    return {"key": key, "severity": severity, "ko": ko, "en": en, **detail}


def funnel_alerts(
    counters: Dict[str, int], rates: Dict[str, Optional[float]]
) -> list:
    """Turn funnel rates into named signals a person can act on.

    Pure function of the snapshot — no I/O, no clock — so the thresholds are
    testable and the same numbers always produce the same alerts. Rules stay
    silent below :data:`MIN_SAMPLES`: an alert nobody can trust is worse than
    no alert.
    """
    alerts: list = []
    file_requests = int(counters.get("file_requests") or 0)
    agent_runs = int(counters.get("agent_runs") or 0)
    approval_pauses = int(counters.get("approval_pauses") or 0)

    real_file_rate = rates.get("real_file_rate")
    if file_requests >= MIN_SAMPLES and real_file_rate is not None:
        if real_file_rate < REAL_FILE_RATE_FLOOR:
            alerts.append(_alert(
                "real_file_rate_low", "warning",
                f"파일 요청 중 실제 파일이 나온 비율이 {real_file_rate:.0%}입니다 "
                f"(목표 {REAL_FILE_RATE_FLOOR:.0%}). 파일 생성 파이프라인을 확인하세요.",
                f"Only {real_file_rate:.0%} of file requests produced a real file "
                f"(target {REAL_FILE_RATE_FLOOR:.0%}). Check the file-generation pipeline.",
                value=real_file_rate, threshold=REAL_FILE_RATE_FLOOR, samples=file_requests,
            ))

    code_only_rate = rates.get("code_only_rate")
    if file_requests >= MIN_SAMPLES and code_only_rate is not None:
        if code_only_rate > CODE_ONLY_RATE_CEILING:
            alerts.append(_alert(
                "code_only_rate_high", "warning",
                f"파일을 요청했는데 코드/설명만 돌아온 비율이 {code_only_rate:.0%}입니다.",
                f"{code_only_rate:.0%} of file requests came back as code or prose only.",
                value=code_only_rate, threshold=CODE_ONLY_RATE_CEILING, samples=file_requests,
            ))

    needs_review_rate = rates.get("needs_review_rate")
    if agent_runs >= MIN_SAMPLES and needs_review_rate is not None:
        if needs_review_rate > NEEDS_REVIEW_RATE_CEILING:
            alerts.append(_alert(
                "needs_review_rate_high", "warning",
                f"에이전트 실행의 {needs_review_rate:.0%}가 '검토 필요'로 끝났습니다. "
                "더 큰 모델을 쓰거나 요청을 작게 나누세요.",
                f"{needs_review_rate:.0%} of agent runs ended as NEEDS_REVIEW. "
                "Use a larger model or split requests into smaller steps.",
                value=needs_review_rate, threshold=NEEDS_REVIEW_RATE_CEILING, samples=agent_runs,
            ))

    resume_rate = rates.get("approval_resume_rate")
    if approval_pauses >= MIN_SAMPLES and resume_rate is not None:
        if resume_rate < APPROVAL_RESUME_RATE_FLOOR:
            alerts.append(_alert(
                "approval_resume_rate_low", "info",
                f"승인 대기 중 실제로 이어서 실행된 비율이 {resume_rate:.0%}입니다. "
                "승인 카드가 잘 보이는지 확인하세요.",
                f"Only {resume_rate:.0%} of paused runs were resumed. "
                "Check that the approval card is actually reaching users.",
                value=resume_rate, threshold=APPROVAL_RESUME_RATE_FLOOR, samples=approval_pauses,
            ))

    if int(counters.get("ingest_completions") or 0) > 0 and not counters.get("recall_successes"):
        alerts.append(_alert(
            "no_grounded_recall", "warning",
            "자료는 들어왔지만 근거 있는 회상이 아직 한 번도 없었습니다. 검색/인덱싱을 확인하세요.",
            "Content was ingested but no answer has ever been grounded in it yet — "
            "check retrieval and indexing.",
            samples=int(counters.get("ingest_completions") or 0),
        ))
    return alerts


__all__ = ["FunnelMetricsService", "COUNTER_NAMES", "funnel_alerts"]
