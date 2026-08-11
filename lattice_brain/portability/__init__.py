"""Knowledge Graph portability — local export / import / backup / restore.

The Knowledge Graph is the user's durable asset, so it must be portable without
any cloud service. Two complementary mechanisms, both fully local:

* **Logical export/import** (JSON): nodes/edges/chunks/sources/provenance with a
  versioned header (schema + projection + embed-dim). Vectors are not in the
  artifact; the importer re-embeds with its own embedder and reports the
  resulting index state under ``result["index"]`` (``degraded: true`` means the
  content landed but recall is lexical-only until a rebuild succeeds). That is
  what makes it portable across machines.
* **Binary backup/restore** (ZIP): a faithful snapshot of the SQLite DB (incl.
  vector embeddings) plus the blob directory, integrity-checked, for
  same-machine recovery.

Split into cohesive submodules in v11.3.0 (no behaviour change): ``constants``
(formats + gates), ``fsops`` (atomic swaps), ``bundles`` (subgraph shaping),
``sharing`` and ``backups`` (the two halves of the service), ``service`` (the
composed class). This module re-exports every name the single file exposed, so
``lattice_brain.portability.X`` keeps working.
"""

from __future__ import annotations

# The single file had no ``__all__``, so its public surface was "every module
# global". The redundant-alias form reproduces exactly that surface and marks
# each name as a deliberate re-export rather than a leftover import.
#
# Stubbing note: rebinding one of these *here* changes only this module's name.
# The submodule that calls it holds its own reference, so a test standing in for
# ``_stamp`` patches ``lattice_brain.portability.fsops``, for ``_sha256_file``
# or ``SQLiteToPostgresMigrator`` ``…portability.backups``, and for
# ``load_recipient_identity`` ``…portability.sharing``.
from ..archive import KDF_ITERATIONS as KDF_ITERATIONS
from ..archive import BrainArchivePaths as BrainArchivePaths
from ..archive import EncryptedBrainArchive as EncryptedBrainArchive
from ..archive import _derive_key as _derive_key
from ..gates import FeatureGate as FeatureGate
from ..quiet import quiet as quiet
from ..sealed_box import SEALED_BOX_ALGORITHM as SEALED_BOX_ALGORITHM
from ..sealed_box import public_key_fingerprint as public_key_fingerprint
from ..sealed_box import seal as seal
from ..storage import DockerPostgresWizard as DockerPostgresWizard
from ..storage import PostgresEngine as PostgresEngine
from ..storage import SQLiteToPostgresMigrator as SQLiteToPostgresMigrator
from ..utils import utc_now_iso as utc_now_iso
from .backups import KGPortabilityBackupMixin as KGPortabilityBackupMixin
from .backups import _sha256_file as _sha256_file
from .bundles import _canonical_digest as _canonical_digest
from .bundles import _expand_neighbors as _expand_neighbors
from .bundles import _node_source_type as _node_source_type
from .bundles import _redact_node as _redact_node
from .bundles import _redact_provenance_row as _redact_provenance_row
from .bundles import _scope_node as _scope_node
from .bundles import _select_node_ids as _select_node_ids
from .bundles import _shared_node_summary as _shared_node_summary
from .bundles import _strip_fields as _strip_fields
from .constants import _NEIGHBOR_EXCLUDED_TYPES as _NEIGHBOR_EXCLUDED_TYPES
from .constants import _OPEN_REVIEW_STATUSES as _OPEN_REVIEW_STATUSES
from .constants import _PAYLOAD_KEYS as _PAYLOAD_KEYS
from .constants import _REDACTED_FIELDS as _REDACTED_FIELDS
from .constants import _TRUTHY as _TRUTHY
from .constants import BACKUP_FORMAT as BACKUP_FORMAT
from .constants import BRAIN_NETWORK_DISABLED_DETAIL as BRAIN_NETWORK_DISABLED_DETAIL
from .constants import BRAIN_NETWORK_ENV as BRAIN_NETWORK_ENV
from .constants import BRAIN_NETWORK_GATE as BRAIN_NETWORK_GATE
from .constants import ENCRYPTION_MODES as ENCRYPTION_MODES
from .constants import FORMAT as FORMAT
from .constants import FORMAT_VERSION as FORMAT_VERSION
from .constants import SUBGRAPH_ARCHIVE_FORMAT as SUBGRAPH_ARCHIVE_FORMAT
from .constants import SUBGRAPH_FORMAT as SUBGRAPH_FORMAT
from .constants import SUBGRAPH_FORMAT_VERSION as SUBGRAPH_FORMAT_VERSION
from .constants import SUBGRAPH_PROPOSAL_CAP as SUBGRAPH_PROPOSAL_CAP
from .constants import SUBGRAPH_REVIEW_KIND as SUBGRAPH_REVIEW_KIND
from .constants import SUBGRAPH_REVIEW_SOURCE as SUBGRAPH_REVIEW_SOURCE
from .constants import BrainNetworkDisabled as BrainNetworkDisabled
from .constants import _stamp as _stamp
from .constants import brain_network_enabled as brain_network_enabled
from .constants import require_brain_network as require_brain_network
from .fsops import _checkpoint_sqlite as _checkpoint_sqlite
from .fsops import _pre_restore_backup_dir as _pre_restore_backup_dir
from .fsops import _replace_sqlite_atomically as _replace_sqlite_atomically
from .fsops import _replace_tree_with_backup as _replace_tree_with_backup
from .fsops import _restore_sibling as _restore_sibling
from .fsops import _rollback_sqlite_from_backup as _rollback_sqlite_from_backup
from .fsops import _safe_zip_names as _safe_zip_names
from .fsops import _sqlite_siblings as _sqlite_siblings
from .service import KGPortabilityService as KGPortabilityService
from .sharing import KGPortabilitySharingMixin as KGPortabilitySharingMixin
from .sharing import load_recipient_identity as load_recipient_identity
