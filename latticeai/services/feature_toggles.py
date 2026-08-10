"""One user-facing switchboard for every opt-in feature (v11.2.0).

11.2.0 replaced the frozen ``os.getenv`` reads behind each opt-in feature with
:class:`lattice_brain.gates.FeatureGate`, which answers *when it is asked* and
lets an app-layer resolver win over the environment. That made the features
movable at runtime. It did not make them reachable: a person still had to know
an environment variable's name, edit a shell profile, and restart the server.

This service is the reachable half.

* **The server renders the catalog.** Ids, labels, one-line explanations,
  defaults, and which choices are even installable all come from here, so the
  panel cannot drift from what the server actually honours (the 10.1.1 rule:
  no hardcoded catalogs in the client).
* **Precedence is user → env → default.** An untouched install follows its
  environment exactly as before and says so (``source: "env"``). The first time
  a person moves a switch, their choice is persisted and wins from then on.
* **Persistence is a small atomic JSON file** under the data dir — the same
  shape ``PermissionModeService`` uses. Nothing here is derived state worth a
  table, and a settings file that can be read with ``cat`` is a feature.
* **Every string a person reads comes from the message catalog**, so the panel
  is Korean or English because of the request, not because of who wrote it.

The gates themselves are bound to this service in
``latticeai/runtime/feature_toggle_wiring.py``. With no service bound, every
gate keeps reading its environment variable exactly as it did — which is what
makes this whole surface additive.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from lattice_brain.gates import FALSY, TRUTHY
from latticeai.core.io_utils import atomic_write_json
from latticeai.core.messages import DEFAULT_LANGUAGE, translate

#: A two-state switch.
TOGGLE = "toggle"
#: A pick-one-of-N setting (``vector_backend``), where some options may not be
#: installable on this machine.
CHOICE = "choice"

#: File under the data dir. Deliberately human-readable and hand-editable.
STORE_FILENAME = "feature_toggles.json"
STORE_VERSION = 1


class UnknownFeature(KeyError):
    """Raised for a feature id this build does not have."""


class InvalidFeatureValue(ValueError):
    """Raised for a value this feature cannot take (wrong type, or not installed)."""


@dataclass(frozen=True)
class FeatureChoice:
    """One option of a ``choice`` feature.

    ``probe`` names an availability check: an option whose optional dependency
    is missing is *shown*, disabled, with the reason — hiding it would leave a
    person wondering why the docs mention a backend their product does not.
    """

    id: str
    label_key: str
    probe: Optional[str] = None


@dataclass(frozen=True)
class FeatureDefinition:
    """One row of the switchboard.

    ``env_var`` is the seed this feature followed before there was a panel, and
    still follows until someone moves it. ``live`` records whether the switch
    takes effect immediately: every feature here does, because each is backed by
    a :class:`FeatureGate` (or the equivalent resolver seam) that is asked per
    call — the catalog reports it rather than asserting it, so a future feature
    that genuinely needs a restart can say so instead of lying.
    """

    id: str
    kind: str
    env_var: str
    default: Any
    caution: bool = False
    parent: Optional[str] = None
    choices: Tuple[FeatureChoice, ...] = ()
    live: bool = True

    @property
    def label_key(self) -> str:
        return f"features.{self.id}.label"

    @property
    def summary_key(self) -> str:
        return f"features.{self.id}.summary"

    @property
    def caution_key(self) -> str:
        return f"features.{self.id}.caution"

    def choice_ids(self) -> Tuple[str, ...]:
        return tuple(choice.id for choice in self.choices)


def _hnsw_probe() -> Tuple[bool, str]:
    """Whether the optional ANN engine is importable here, and why not."""
    from lattice_brain.graph.vector_index.hnsw import load_hnswlib

    module, reason = load_hnswlib()
    return module is not None, reason or ""


#: Availability probes for ``choice`` options, injectable so a test never has to
#: install a compiled extension to exercise both branches.
DEFAULT_PROBES: Dict[str, Callable[[], Tuple[bool, str]]] = {"hnsw": _hnsw_probe}


#: The switchboard. Order is the order the panel renders, grouped by what a
#: person is deciding: what the Brain takes in, what leaves it, then how it
#: searches. ``video_ingest`` declares ``allow_multimodal`` as its parent so the
#: panel can indent it rather than presenting a sub-switch as a peer.
CATALOG: Tuple[FeatureDefinition, ...] = (
    FeatureDefinition(
        id="allow_multimodal",
        kind=TOGGLE,
        env_var="LATTICEAI_ALLOW_MULTIMODAL",
        default=False,
    ),
    FeatureDefinition(
        id="video_ingest",
        kind=TOGGLE,
        env_var="LATTICEAI_ALLOW_VIDEO",
        default=True,
        parent="allow_multimodal",
    ),
    FeatureDefinition(
        id="vault_watch",
        kind=TOGGLE,
        env_var="LATTICEAI_VAULT_WATCH",
        default=False,
    ),
    FeatureDefinition(
        id="brain_network",
        kind=TOGGLE,
        env_var="LATTICEAI_BRAIN_NETWORK",
        default=False,
        caution=True,
    ),
    FeatureDefinition(
        id="synthesis",
        kind=TOGGLE,
        env_var="LATTICEAI_SYNTHESIS",
        default=True,
    ),
    FeatureDefinition(
        id="auto_vector_index",
        kind=TOGGLE,
        env_var="LATTICEAI_AUTO_VECTOR_INDEX",
        default=True,
    ),
    FeatureDefinition(
        id="auto_late_fusion",
        kind=TOGGLE,
        env_var="LATTICEAI_TEXT_IMAGE_FUSION",
        default=False,
    ),
    FeatureDefinition(
        id="fusion_rrf",
        kind=TOGGLE,
        env_var="LATTICEAI_FUSION_RRF",
        default=False,
    ),
    FeatureDefinition(
        id="graph_expansion",
        kind=TOGGLE,
        env_var="LATTICEAI_GRAPH_EXPANSION",
        default=False,
    ),
    FeatureDefinition(
        id="vector_backend",
        kind=CHOICE,
        env_var="LATTICEAI_VECTOR_INDEX",
        default="brute",
        choices=(
            FeatureChoice("brute", "features.vector_backend.choice.brute"),
            FeatureChoice("quantized", "features.vector_backend.choice.quantized"),
            FeatureChoice("hnsw", "features.vector_backend.choice.hnsw", probe="hnsw"),
        ),
    ),
)

CATALOG_BY_ID: Dict[str, FeatureDefinition] = {item.id: item for item in CATALOG}


class FeatureToggleService:
    """Resolve, persist, and describe every opt-in feature.

    Thread-safe by a single lock around the file, which is all the contention
    this can see: the store is written only when a person moves a switch.
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        probes: Optional[Mapping[str, Callable[[], Tuple[bool, str]]]] = None,
        audit: Optional[Callable[..., None]] = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.probes: Mapping[str, Callable[[], Tuple[bool, str]]] = (
            dict(DEFAULT_PROBES) if probes is None else probes
        )
        self.audit = audit
        self._lock = threading.Lock()

    # ── plumbing ────────────────────────────────────────────────────────────
    @property
    def path(self) -> Path:
        return self.data_dir / STORE_FILENAME

    def rebind_data_dir(self, data_dir: Path) -> None:
        """Point the store at the app's real data dir (see permission mode)."""
        with self._lock:
            self.data_dir = Path(data_dir)

    def rebind_audit(self, audit: Callable[..., None]) -> None:
        """Attach the real audit sink once app wiring provides one."""
        with self._lock:
            self.audit = audit

    def _read(self) -> Dict[str, Any]:
        """Stored user choices. A missing or unreadable file means "none yet"."""
        path = self.path
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a corrupt file must not break the app
            return {}
        features = data.get("features") if isinstance(data, dict) else None
        return dict(features) if isinstance(features, dict) else {}

    def _write(self, features: Mapping[str, Any]) -> None:
        atomic_write_json(
            self.path, {"version": STORE_VERSION, "features": dict(features)}
        )

    # ── resolution ──────────────────────────────────────────────────────────
    @staticmethod
    def _definition(feature_id: str) -> FeatureDefinition:
        definition = CATALOG_BY_ID.get(str(feature_id))
        if definition is None:
            raise UnknownFeature(str(feature_id))
        return definition

    @staticmethod
    def _coerce(definition: FeatureDefinition, value: Any) -> Optional[Any]:
        """A stored/env value as this feature's type, or ``None`` if it is not.

        Anything unrecognised resolves to ``None`` rather than raising, so a
        hand-edited file with a typo falls through to the next layer instead of
        taking the settings panel down.
        """
        if definition.kind == CHOICE:
            text = str(value).strip().lower()
            return text if text in definition.choice_ids() else None
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in TRUTHY:
            return True
        if text in FALSY:
            return False
        return None

    def _env_value(self, definition: FeatureDefinition) -> Optional[Any]:
        """What the environment seeds this feature with (``None`` = nothing)."""
        raw = os.getenv(definition.env_var, "").strip()
        if not raw:
            return None
        return self._coerce(definition, raw)

    def _resolve(
        self, definition: FeatureDefinition, stored: Mapping[str, Any]
    ) -> Tuple[Any, str]:
        """``(value, source)`` under user → env → default precedence."""
        if definition.id in stored:
            user = self._coerce(definition, stored[definition.id])
            if user is not None:
                return user, "user"
        seeded = self._env_value(definition)
        if seeded is not None:
            return seeded, "env"
        return definition.default, "default"

    def value(self, feature_id: str) -> Any:
        """The effective value of one feature right now."""
        definition = self._definition(feature_id)
        with self._lock:
            stored = self._read()
        return self._resolve(definition, stored)[0]

    def user_value(self, feature_id: str) -> Optional[Any]:
        """What *this person* chose, or ``None`` if they never touched it.

        The difference from :meth:`value` is the whole precedence story: the
        switchboard speaks only for the switches someone actually moved, and
        stays quiet about the rest so an operator's environment variable — and
        the honest reporting that hangs off it, like an unknown backend name —
        keeps working underneath.
        """
        definition = self._definition(feature_id)
        with self._lock:
            stored = self._read()
        if definition.id not in stored:
            return None
        return self._coerce(definition, stored[definition.id])

    def enabled(self, feature_id: str) -> bool:
        """The effective value of one *toggle*, as a bool for gate binding."""
        return bool(self.value(feature_id))

    def resolver(
        self, feature_id: str, fallback: Optional[Callable[[], bool]] = None
    ) -> Callable[[], bool]:
        """A zero-argument callable to hand :meth:`FeatureGate.bind`.

        ``fallback`` answers for a feature this person never touched — pass the
        gate's own :meth:`FeatureGate.local` and an untouched switch behaves
        byte-identically to an unbound gate, override and all. Without one, the
        service answers from env → default itself.
        """
        self._definition(feature_id)  # fail loudly at wiring time, not per call

        def _resolve() -> bool:
            chosen = self.user_value(feature_id)
            if chosen is not None:
                return bool(chosen)
            if fallback is not None:
                return bool(fallback())
            return self.enabled(feature_id)

        return _resolve

    def choice_resolver(self, feature_id: str) -> Callable[[], Optional[str]]:
        """A zero-argument callable for a *string* seam (the vector backend).

        ``None`` means "this person has not chosen", which the seam reads as
        "ask the environment" — so an install that never opened the panel still
        gets the env var's answer *and* its diagnostics (a typo'd backend name
        is reported rather than quietly resolved to the default).
        """
        self._definition(feature_id)

        def _resolve() -> Optional[str]:
            chosen = self.user_value(feature_id)
            return None if chosen is None else str(chosen)

        return _resolve

    # ── availability ────────────────────────────────────────────────────────
    def _availability(self, choice: FeatureChoice) -> Tuple[bool, str]:
        """``(installable, reason)`` for one option of a choice feature."""
        if choice.probe is None:
            return True, ""
        probe = self.probes.get(choice.probe)
        if probe is None:
            # An install that registered no probe for this option cannot prove
            # it is missing, and "we could not check" is not "not installed".
            return True, ""
        try:
            return probe()
        except Exception as exc:  # noqa: BLE001 — a probe failure is "not installed"
            return False, str(exc)

    # ── rendering ───────────────────────────────────────────────────────────
    def _render_choices(
        self, definition: FeatureDefinition, language: str
    ) -> List[Dict[str, Any]]:
        rendered: List[Dict[str, Any]] = []
        for choice in definition.choices:
            available, reason = self._availability(choice)
            rendered.append(
                {
                    "id": choice.id,
                    "label": translate(choice.label_key, language),
                    "available": available,
                    # An unavailable option says "install required" *and* what
                    # the import actually complained about, so the answer is
                    # actionable rather than a shrug.
                    "detail": (
                        None
                        if available
                        else translate(
                            "features.choice.install_required", language, reason=reason
                        )
                    ),
                }
            )
        return rendered

    def _render(
        self, definition: FeatureDefinition, stored: Mapping[str, Any], language: str
    ) -> Dict[str, Any]:
        current, source = self._resolve(definition, stored)
        return {
            "id": definition.id,
            "kind": definition.kind,
            "label": translate(definition.label_key, language),
            "summary": translate(definition.summary_key, language),
            "default": definition.default,
            "current": current,
            "source": source,
            "env_var": definition.env_var,
            "live": definition.live,
            "restart_required": not definition.live,
            "caution": (
                translate(definition.caution_key, language) if definition.caution else None
            ),
            "parent": definition.parent,
            "choices": self._render_choices(definition, language),
        }

    def catalog(self, language: str = DEFAULT_LANGUAGE) -> Dict[str, Any]:
        """The whole switchboard, localized, with each feature's live value."""
        with self._lock:
            stored = self._read()
        return {
            "features": [self._render(item, stored, language) for item in CATALOG],
            "note": translate("features.note", language),
        }

    # ── writing ─────────────────────────────────────────────────────────────
    def set(
        self,
        feature_id: str,
        value: Any,
        *,
        language: str = DEFAULT_LANGUAGE,
        user_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist one person's choice and return the feature as it now reads.

        Refuses a value the feature cannot take, including an option whose
        optional dependency is not installed — accepting it would produce a
        panel that shows ``hnsw`` while every search quietly ran brute force.
        """
        definition = self._definition(feature_id)
        coerced = self._coerce(definition, value)
        if coerced is None:
            raise InvalidFeatureValue(
                translate("features.invalid_value", language, value=str(value))
            )
        if definition.kind == CHOICE:
            choice = next(item for item in definition.choices if item.id == coerced)
            available, reason = self._availability(choice)
            if not available:
                raise InvalidFeatureValue(
                    translate("features.choice.install_required", language, reason=reason)
                )
        with self._lock:
            stored = self._read()
            previous = self._resolve(definition, stored)[0]
            stored[definition.id] = coerced
            self._write(stored)
            audit = self.audit
        # Rendered outside the lock: it may run an availability probe, and the
        # first one imports an optional compiled extension.
        rendered = self._render(definition, stored, language)
        if audit is not None:
            audit(
                "feature_toggle_changed",
                feature=definition.id,
                previous=previous,
                value=coerced,
                user_email=user_email,
            )
        return rendered


__all__ = [
    "CATALOG",
    "CATALOG_BY_ID",
    "CHOICE",
    "DEFAULT_PROBES",
    "STORE_FILENAME",
    "STORE_VERSION",
    "TOGGLE",
    "FeatureChoice",
    "FeatureDefinition",
    "FeatureToggleService",
    "InvalidFeatureValue",
    "UnknownFeature",
]
