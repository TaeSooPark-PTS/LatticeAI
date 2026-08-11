"""Long-Term Memory platform + Memory Manager (v3.2.0).

Parts 7, 8 and 13. Lattice AI already persists memory in several real stores;
before this service they were unrelated. ``MemoryService`` unifies them behind
one façade and adds a Memory Manager that reports usage / sources / health /
size / type and supports recall / inspect / prune / compact / rebuild / clear.

Memory tiers and their real backing store (nothing is fabricated — a tier with
no backing reports ``unavailable``):

* **workspace**     — personal workspace memories (``WorkspaceOS`` memories)
* **project**       — memories scoped to a non-personal (organization) workspace
* **agent**         — agent memory snapshots captured during runs
* **conversation**  — chat history conversations
* **graph**         — Knowledge Graph nodes (entities + relations)
* **vector**        — local embedding vector index

The service never invents counts or health: every number is read from the
underlying store, and missing stores surface as ``unavailable``.

Split into cohesive submodules in v11.3.0 (no behaviour change): ``constants``
(tier vocabulary + the visual passthrough), ``stores`` (reads over the real
backends), ``manager`` (the cross-tier report), ``brief`` and ``proof`` (the
home screen's briefing and its evidence), ``recall`` (unified retrieval and
per-tier inspection), ``maintenance`` (the mutating half), and ``service`` (the
composed class). This module re-exports every name the single file exposed, so
``latticeai.services.memory_service.X`` keeps working.
"""

from __future__ import annotations

# The single file had no ``__all__``, so its public surface was "every module
# global" — including the names it imported for its own use. The redundant-alias
# form reproduces exactly that surface and marks each name as a deliberate
# re-export rather than a leftover import.
#
# Stubbing note: rebinding one of these *here* changes only this module's name.
# The submodule that calls it holds its own reference, so a test standing in for
# ``LOGGER`` or ``_now`` patches the submodule that uses it.
# noqa on the next line, not a redundant alias: the single file exposed this
# name *renamed* (``memory_service._now``), and reproducing that surface exactly
# is the point — importing it as ``now_iso`` would add a name the module never had.
from latticeai.core.timeutil import now_iso as _now  # noqa: F401
from latticeai.core.workspace_os_utils import _file_size as _file_size

from .constants import LOGGER as LOGGER
from .constants import MAX_RECALL_THUMBNAIL_CHARS as MAX_RECALL_THUMBNAIL_CHARS
from .constants import TIERS as TIERS
from .constants import WORKSPACE_KINDS as WORKSPACE_KINDS
from .constants import MemoryServiceError as MemoryServiceError
from .constants import _visual_fields as _visual_fields
from .service import MemoryService as MemoryService
