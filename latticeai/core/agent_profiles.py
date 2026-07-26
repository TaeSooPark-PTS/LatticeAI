"""Agent profiles — matching the loop to the model driving it (v9.9.7).

The single-agent loop asks a model to emit one strict JSON tool call per turn.
Large models do that reliably; a 1–4B local model routinely does not, and the
loop's answer was always the same: repair, correct, retry, and eventually give
up with nothing written. The counters showed the strain (``model_strain`` in
the run explanation), but every model still ran the same loop.

A profile is that missing dial:

* **standard** — today's behaviour, unchanged, for models that can hold a
  tool-call contract.
* **compact** — for small local models: a shorter transcript window, an
  earlier escalation to naming the valid tools, and — the important one — a
  **direct-path fallback**. When JSON tool calls keep failing, the loop stops
  asking for JSON at all and executes the plan's own file steps, requesting
  only *file content* in plain text. Weak models are bad at tool protocols and
  fine at writing a file; the profile plays to that.

Selection is deterministic and inspectable: a model id names its size, so the
profile is a pure function of the id plus an explicit environment override.
Nothing here guesses at quality — a model absent from the size heuristic gets
``standard``, which is the conservative choice (no behaviour change).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Optional

__all__ = ["AgentProfile", "COMPACT", "STANDARD", "profile_for_model", "model_size_b"]


@dataclass(frozen=True)
class AgentProfile:
    """How hard the loop should work to keep a given model on contract."""

    name: str
    # Executor transcript window (steps kept in full). Small models drown in
    # long transcripts far sooner than large ones.
    transcript_window: int
    # Format slips tolerated before the run stops retrying.
    parse_failure_budget: int
    # Slip count at which the correction hint starts naming valid tool names.
    escalate_after: int
    # When JSON tool calls are exhausted, execute the plan's file steps
    # directly and ask only for file content in plain text.
    direct_path_fallback: bool


STANDARD = AgentProfile(
    name="standard",
    transcript_window=8,
    parse_failure_budget=3,
    escalate_after=2,
    direct_path_fallback=False,
)

COMPACT = AgentProfile(
    name="compact",
    transcript_window=4,
    parse_failure_budget=4,
    escalate_after=1,
    direct_path_fallback=True,
)

_PROFILES = {profile.name: profile for profile in (STANDARD, COMPACT)}

# "gemma-3-4b-it-4bit", "qwen2.5-1.5b", "llama-3.2-3B" → parameter count in B.
# The quantization suffix ("4bit", "8bit") is deliberately excluded: it is not
# a parameter count, and reading it as one would mislabel every quantized model.
_SIZE_RE = re.compile(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)\s*b(?![a-z0-9])", re.IGNORECASE)
_QUANT_RE = re.compile(r"\d+\s*bit", re.IGNORECASE)

# At or below this parameter count, the compact profile applies.
COMPACT_MAX_PARAMS_B = 4.0


def model_size_b(model_id: str) -> Optional[float]:
    """Parameter count in billions parsed from a model id, or None.

    Returns None rather than guessing when the id names no size — an unknown
    model must not be silently downgraded to the compact loop.
    """
    text = _QUANT_RE.sub(" ", str(model_id or ""))
    sizes = [float(match) for match in _SIZE_RE.findall(text)]
    return min(sizes) if sizes else None


def profile_for_model(
    model_id: Optional[str], *, env: Optional[Mapping[str, str]] = None
) -> AgentProfile:
    """Pick the loop profile for a model.

    Order: explicit ``LATTICEAI_AGENT_PROFILE`` override → size heuristic →
    ``standard``. An unrecognized override name falls through to the
    heuristic rather than failing the run.
    """
    if env is None:
        import os

        env = os.environ
    override = str(env.get("LATTICEAI_AGENT_PROFILE") or "").strip().lower()
    if override in _PROFILES:
        return _PROFILES[override]
    size = model_size_b(model_id or "")
    if size is not None and size <= COMPACT_MAX_PARAMS_B:
        return COMPACT
    return STANDARD
