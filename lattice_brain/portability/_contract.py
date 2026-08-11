"""The seam the two portability mixins share.

``KGPortabilityService`` is assembled from :class:`KGPortabilitySharingMixin`
and :class:`KGPortabilityBackupMixin`. Both read constructor state they do not
own and both call the availability guard the service defines. That contract
existed as an unwritten convention; this module writes it down.

Typing-only, exactly like :mod:`lattice_brain.graph._kg_contract`: at runtime
the mixins alias it to ``object``, so the MRO and every method resolution stay
byte-for-byte what the single-file class had. Adding a cross-mixin call without
declaring it here is a type error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class PortabilityCore:
    """What either portability mixin may assume about ``self``.

    Never instantiated and never inherited at runtime — see the module
    docstring. Members are declared, not implemented: the implementation lives
    in ``service.py`` (state and availability) or in whichever mixin owns it.
    """

    # ── State owned by KGPortabilityService.__init__ ─────────────────────────
    _kg: Any
    _data_dir: Path
    _enable: bool
    _exports_dir: Path
    _identity: Any
    _recipient: Any
    _recipient_loaded: bool

    # ── service.py: the availability guard every write door calls ────────────
    def available(self) -> bool:
        raise NotImplementedError

    def _require(self) -> None:
        raise NotImplementedError
