"""v11.2.0 — opt-in gates that can be answered at runtime.

The point of :class:`FeatureGate` is a promise in two directions:

1. **Nothing changes for an untouched install.** Same environment variable,
   same truthy words, same default — the gate is a drop-in for the frozen
   ``os.getenv`` read it replaced.
2. **Something *can* change without a restart.** A bound resolver or an
   explicit override wins over the environment, which is what a settings screen
   needs and what a constructor-frozen boolean could never offer.

Both are asserted here, along with the ordering between the four layers.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.gates import FALSY, TRUTHY, FeatureGate  # noqa: E402

ENV = "LATTICEAI_TEST_GATE"


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    return FeatureGate(ENV, name="test gate", detail="a gate for tests")


# ── the environment layer (the historical behaviour) ─────────────────────────
@pytest.mark.parametrize("word", sorted(TRUTHY))
def test_every_truthy_word_this_product_has_ever_accepted_still_works(gate, monkeypatch, word):
    monkeypatch.setenv(ENV, f"  {word.upper()} ")
    assert gate.enabled() is True
    assert gate() is True
    assert gate.source() == "env"


@pytest.mark.parametrize("word", sorted(FALSY))
def test_falsy_words_turn_a_default_on_gate_off(monkeypatch, word):
    monkeypatch.setenv(ENV, word)
    on_by_default = FeatureGate(ENV, default=True)
    assert on_by_default.enabled() is False
    assert on_by_default.source() == "env"


def test_an_unset_variable_falls_back_to_the_declared_default(gate, monkeypatch):
    assert gate.enabled() is False
    assert gate.source() == "default"
    assert FeatureGate(ENV, default=True).enabled() is True
    # A word the parser does not know is not a decision either way.
    monkeypatch.setenv(ENV, "maybe")
    assert gate.enabled() is False
    assert gate.source() == "default"


# ── the injection layers (what the settings screen will use) ─────────────────
def test_an_override_beats_the_environment_and_can_be_handed_back(gate, monkeypatch):
    monkeypatch.setenv(ENV, "0")
    gate.set(True)
    assert gate.enabled() is True
    assert gate.source() == "override"
    # Still reports what the environment alone would have said.
    assert gate.from_env() is False
    gate.set(None)
    assert gate.enabled() is False
    assert gate.source() == "env"


def test_a_bound_resolver_beats_everything_including_an_override(gate, monkeypatch):
    monkeypatch.setenv(ENV, "0")
    gate.set(False)
    answers = iter([True, False])
    gate.bind(lambda: next(answers))

    assert gate.source() == "resolver"
    assert gate.enabled() is True   # resolved per call…
    assert gate.enabled() is False  # …not once at bind time

    gate.reset()
    assert gate.enabled() is False
    assert gate.source() == "env"


def test_describe_reports_the_state_and_where_it_came_from(gate, monkeypatch):
    monkeypatch.setenv(ENV, "yes")
    assert gate.describe() == {
        "name": "test gate",
        "flag": ENV,
        "enabled": True,
        "default": False,
        "source": "env",
        "detail": "a gate for tests",
    }
    gate.bind(lambda: False)
    described = gate.describe()
    assert described["enabled"] is False and described["source"] == "resolver"


def test_a_gate_without_a_name_is_named_after_its_flag(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert FeatureGate(ENV).describe()["name"] == ENV
