"""Formats, gates and the vocabulary the portability surfaces agree on.

Everything here is a literal or a gate: no I/O, no graph, no service. Keeping
it in one leaf module is what lets the file helpers, the sharing surface and
the backup surface all import the same constants without importing each other.
"""

from __future__ import annotations

from ..gates import FeatureGate
from ..utils import utc_now_iso

FORMAT = "latticeai.kg.export"
FORMAT_VERSION = 1
BACKUP_FORMAT = "latticeai.kg.backup"

# ── selective subgraph share (v11.1.0 prototype, off by default) ─────────────
#: Logical format of a *partial* bundle: a chosen slice of the graph plus its
#: provenance, signed by this device. Deliberately distinct from ``FORMAT`` so
#: the whole-graph importer can never be handed a subgraph by accident.
SUBGRAPH_FORMAT = "latticeai.kg.subgraph"
SUBGRAPH_FORMAT_VERSION = 1
#: Encrypted on-disk envelope for the same bundle, reusing the ``.latticebrain``
#: passphrase mechanism (PBKDF2-SHA256 + AES-256-GCM) from ``archive.py``.
SUBGRAPH_ARCHIVE_FORMAT = "latticebrain.subgraph"
#: Opt-in gate. Off unless the operator sets it: sharing knowledge with another
#: Brain is a network act, and local-first means those are never on by default.
BRAIN_NETWORK_ENV = "LATTICEAI_BRAIN_NETWORK"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
#: How a bundle can be encrypted. ``passphrase`` needs a secret agreed out of
#: band; ``recipient_public_key`` needs nothing secret to travel at all.
ENCRYPTION_MODES = ("passphrase", "recipient_public_key")
#: A received bundle becomes review items, never a merge. The cap keeps one
#: peer from flooding the inbox; what it dropped is reported, not hidden.
SUBGRAPH_PROPOSAL_CAP = 200
#: Review-queue vocabulary. ``kg_change_digest`` is the existing source for
#: "the graph would change"; the kind distinguishes shared-subgraph items.
SUBGRAPH_REVIEW_SOURCE = "kg_change_digest"
SUBGRAPH_REVIEW_KIND = "shared_subgraph_node"
#: Mirrors ``services.review_queue.OPEN_STATUSES``. Duplicated as a literal
#: rather than imported because ``lattice_brain`` must not depend on the
#: ``latticeai`` service layer; the review sink is injected, not imported.
_OPEN_REVIEW_STATUSES = frozenset({"pending", "snoozed"})

BRAIN_NETWORK_DISABLED_DETAIL = (
    "Brain Network sharing is off. It is opt-in by design: set "
    f"{BRAIN_NETWORK_ENV}=1 to enable selective subgraph export and receipt."
)


#: The share gate, resolved when it is asked. The environment variable is still
#: the default answer — an untouched install behaves exactly as it did — but a
#: settings surface can now bind a resolver and move it without a restart.
BRAIN_NETWORK_GATE = FeatureGate(
    BRAIN_NETWORK_ENV,
    default=False,
    name="brain_network",
    detail=BRAIN_NETWORK_DISABLED_DETAIL,
)


class BrainNetworkDisabled(PermissionError):
    """Raised when a share surface is used while the opt-in flag is off."""

    def __init__(self, detail: str = BRAIN_NETWORK_DISABLED_DETAIL) -> None:
        super().__init__(detail)


def brain_network_enabled() -> bool:
    """True only when the operator explicitly opted in (default: off)."""
    return BRAIN_NETWORK_GATE.enabled()


def require_brain_network() -> None:
    if not brain_network_enabled():
        raise BrainNetworkDisabled()


def _stamp() -> str:
    return utc_now_iso().replace(":", "").replace("-", "").replace(".", "")[:15]


#: Sender-identifying fields stripped from a shared bundle by default. The
#: knowledge travels; "which account, on which machine, at which path" does not.
_REDACTED_FIELDS = frozenset({"owner", "user_email", "source_uri", "permissions"})
#: The signed payload's key set. One tuple so export and verify can never
#: disagree about what the digest covers.
_PAYLOAD_KEYS = (
    "nodes", "chunk_nodes", "edges", "chunks", "knowledge_sources", "provenance",
)
#: Node types a one-hop expansion never admits — they describe the sender
#: (identity, local paths), not the knowledge being shared.
_NEIGHBOR_EXCLUDED_TYPES = frozenset({"Person", "Source"})
