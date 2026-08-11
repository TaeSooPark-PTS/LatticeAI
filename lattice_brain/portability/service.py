"""The composed portability service — state, availability, and the two halves.

``KGPortabilityService`` owns the constructor state every surface reads
(``_kg``, ``_data_dir``, ``_exports_dir``, ``_identity``, the lazily created
recipient key) and inherits its behaviour from the sharing and backup mixins.
Same public surface, same method resolution, in two readable files instead of
one thousand-line one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .backups import KGPortabilityBackupMixin
from .sharing import KGPortabilitySharingMixin


class KGPortabilityService(KGPortabilitySharingMixin, KGPortabilityBackupMixin):
    def __init__(self, *, knowledge_graph: Any, data_dir, enable_graph: bool = True, device_identity: Any = None) -> None:
        self._kg = knowledge_graph
        self._data_dir = Path(data_dir)
        self._enable = bool(enable_graph)
        self._exports_dir = self._data_dir / "workspace_exports"
        # v4 sovereignty: when a DeviceIdentity is wired, exports are signed
        # and imports record origin provenance. Pre-v4 unsigned bundles stay
        # importable locally (origin='unsigned-legacy') — signatures are
        # mandatory only on the Brain Network peer path.
        self._identity = device_identity
        # The receiving X25519 key is created lazily: a Brain that never asks
        # anyone to seal anything to it should not be minting key material on
        # every startup (v11.2.0).
        self._recipient: Any = None
        self._recipient_loaded = False

    def available(self) -> bool:
        return self._enable and self._kg is not None

    def _require(self) -> None:
        if not self.available():
            raise RuntimeError("Knowledge Graph is disabled (LATTICEAI_ENABLE_GRAPH).")
