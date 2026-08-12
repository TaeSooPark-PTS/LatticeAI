"""The shared storage half of the scoped preference dials.

``PermissionModeService``, ``NetworkBoundaryService`` and ``HybridPolicyService``
are three different policies over one identical store: a JSON file under the
data dir holding ``{"default": …, "users": {…}, "workspaces": {…}}``, guarded by
a lock, rebindable after lazy construction, and read defensively enough that a
truncated or hand-edited file degrades to defaults instead of taking the app
down.

That half was written three times, character for character. Three copies of a
"corrupt file falls back to defaults" rule is three chances for one of them to
grow an exception path the others do not have — and the symptom would be one
dial silently forgetting a user's choice while the other two remember. The
storage lives here once; each service supplies only what actually differs:
the file name, what an unset ``default`` means, and how a scope resolves.

Deliberately *not* shared: ``set_mode``/``set_policy``. They look similar but
their write shapes differ (a scalar mode replaces, a policy patch merges) and
their audit events carry different payloads, so folding them together would
mean a parameterised method with more branches than the two bodies it replaced.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, Generic, Optional, TypeVar

from latticeai.core.io_utils import atomic_write_json

#: What ``resolve`` hands back — an enum member for the mode dials, a plain
#: dict for the policy service.
ResolvedT = TypeVar("ResolvedT")

__all__ = ["JsonBackedModeService"]


class JsonBackedModeService(Generic[ResolvedT]):
    """A lock-guarded, scope-aware JSON preference file.

    Subclasses set :attr:`FILENAME` and implement :meth:`_default_entry` and
    :meth:`_resolve_from`.
    """

    #: File name under the data dir. Subclasses must set this.
    FILENAME: ClassVar[str] = ""

    def __init__(
        self,
        *,
        data_dir: Path,
        audit: Optional[Callable[..., None]] = None,
    ) -> None:
        self._path = Path(data_dir) / self.FILENAME
        self._audit = audit or (lambda *a, **kw: None)
        self._lock = threading.Lock()

    # ── rebinding ────────────────────────────────────────────────────────────
    def rebind_data_dir(self, data_dir: Path) -> None:
        """Point the store at the app's real data dir.

        The wiring may instantiate a service lazily before routers know the
        configured data dir; rebinding keeps one file of record instead of
        stranding writes under the fallback path.
        """
        with self._lock:
            self._path = Path(data_dir) / self.FILENAME

    def rebind_audit(self, audit: Callable[..., None]) -> None:
        """Attach the real audit sink once app wiring provides one."""
        with self._lock:
            self._audit = audit

    # ── what each subclass must decide ───────────────────────────────────────
    def _default_entry(self) -> Any:
        """The ``default`` bucket's value when the file does not say.

        Called fresh at every use because the policy service's answer is a
        mutable dict; a shared instance would let one caller's edit leak into
        the next caller's defaults.
        """
        raise NotImplementedError

    def _resolve_from(
        self,
        data: Dict[str, Any],
        *,
        user_email: Optional[str],
        workspace_id: Optional[str],
    ) -> ResolvedT:
        """Apply this dial's scope precedence to already-loaded ``data``.

        Pure over ``data`` and lock-free, so holders of ``_lock`` can reuse it
        without re-entering a non-reentrant lock.
        """
        raise NotImplementedError

    # ── storage ──────────────────────────────────────────────────────────────
    def _empty(self) -> Dict[str, Any]:
        return {"default": self._default_entry(), "users": {}, "workspaces": {}}

    def _read(self) -> Dict[str, Any]:
        if not self._path.exists():
            return self._empty()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return self._empty()
        if not isinstance(data, dict):
            return self._empty()
        data.setdefault("default", self._default_entry())
        data.setdefault("users", {})
        data.setdefault("workspaces", {})
        return data

    def _write(self, data: Dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._path, data)

    # ── reading ──────────────────────────────────────────────────────────────
    def resolve(
        self,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> ResolvedT:
        with self._lock:
            data = self._read()
        return self._resolve_from(
            data, user_email=user_email, workspace_id=workspace_id,
        )
