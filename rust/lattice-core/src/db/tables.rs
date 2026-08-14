//! Table and state-file ownership map — extracted from `db.rs`.

/// Which runtime is allowed to write a table or file after v11.6.0.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Owner {
    /// Rust writes it. The worker must not, once the family is ported.
    RustPlatform,
    /// The Python worker is the single writer. Rust reads it directly and
    /// changes it only by delegating over a seam.
    ///
    /// No table in [`TABLES`] carries this any more (v11.6.0 §W3b flipped
    /// the last seventeen); `telegram_chats.json` in [`state_files`] still
    /// does, and the variant stays so handing something back is a row
    /// change rather than a re-derivation.
    Worker,
    /// Nobody writes it as such: a view, an FTS index, or a shadow table
    /// maintained as a side effect of whoever owns the base table. Rust
    /// reads it; Rust must never write it.
    SharedRead,
}

impl Owner {
    /// Whether a Rust crate may open a write connection for this.
    pub fn rust_may_write(self) -> bool {
        matches!(self, Owner::RustPlatform)
    }

    /// Stable name for logs, docs and test failure messages.
    pub fn as_str(self) -> &'static str {
        match self {
            Owner::RustPlatform => "RUST_PLATFORM",
            Owner::Worker => "WORKER",
            Owner::SharedRead => "SHARED_READ",
        }
    }
}

/// One row of the map.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TableOwnership {
    /// The database file, relative to the data directory.
    pub file: &'static str,
    /// The table or view name.
    pub table: &'static str,
    /// Who may write it.
    pub owner: Owner,
    /// The Python module that creates/writes it today — the evidence.
    pub written_by: &'static str,
    /// Anything a porter would otherwise get wrong.
    pub note: &'static str,
}

/// The only SQLite file in the product.
///
/// Named `knowledge_graph.sqlite` for historical reasons; it has carried
/// non-graph platform tables (`workspace_os_state`, `conversation_messages`)
/// since long before v11.6.0, which is why ownership is per table and not
/// per file.
pub const GRAPH_DB: &str = "knowledge_graph.sqlite";

/// Every table and view, with its writer.
pub const TABLES: &[TableOwnership] = &[
    // ── the Brain: Rust is the single writer (v11.6.0 §W3b) ──────────
    TableOwnership {
        file: GRAPH_DB,
        table: "nodes",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/graph/store.py",
        note: "legacy node table; written by graph_write::GraphWriter since v11.6.0 §W3b",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "edges",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/graph/store.py",
        note: "FK to nodes with ON DELETE CASCADE; foreign_keys=ON makes that real on every write connection",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "chunks",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/graph/store.py",
        note: "chunking happens in lattice-ingest; the write is GraphWriter's",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "nodes_v2",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/graph/schema.py",
        note: "kgv2 write-side; read path is LATTICEAI_KG_READ_V2-gated",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "edges_v2",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/graph/schema.py",
        note: "",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "edge_occurrences",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/graph/schema.py",
        note: "one row per observation behind an edges_v2 row",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "vector_embeddings",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/graph/store.py",
        note: "the vector VALUES are still worker compute (POST /worker/embed); the ROW is written by GraphWriter",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "vector_jobs",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/graph/vector_index/jobs.py",
        note: "enqueued by GraphWriter; drained natively by lattice_jobs::index_api. Python no longer serves /api/index/drain",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "vector_index_operations",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/graph/store.py",
        note: "reindex audit trail",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "image_embeddings",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/graph/image_vectors.py",
        note: "multimodal vectors; absent until an image is ingested",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "knowledge_sources",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/graph/store.py",
        note: "",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "local_file_index",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/graph/store.py",
        note: "the folder-watch bookkeeping GraphWriter writes as it indexes",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "ingestion_provenance",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/graph/store.py",
        note: "",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "ingestion_jobs",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/ingestion_jobs.py",
        note: "job rows are written by the pipeline that runs the job; resume is still an HTTP call to the worker, never a Rust UPDATE",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "graph_meta",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/graph/store.py",
        note: "",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "kg_meta",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/graph/schema.py",
        note: "stamped by GraphWriter::bootstrap; latticeai/core/users.py also touches it during a one-shot identity migration, which is an admin path, not a route",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "storage_meta",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/storage/sqlite.py",
        note: "written by StorageEngine.initialize(); GraphWriter deliberately does not create it, so it is absent on a Rust-built store",
    },
    // ── projections: read-only for everybody ─────────────────────────
    TableOwnership {
        file: GRAPH_DB,
        table: "node_fts",
        owner: Owner::SharedRead,
        written_by: "lattice_brain/graph/projection/v2_schema.py",
        note: "fts5(tokenize='trigram'); its node_fts_{data,idx,content,docsize,config} shadow tables are SQLite's, not ours",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "kgv2_nodes",
        owner: Owner::SharedRead,
        written_by: "lattice_brain/graph/projection/v2_schema.py",
        note: "VIEW over nodes_v2; lattice_core::read picks it when LATTICEAI_KG_READ_V2 allows",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "kgv2_edges",
        owner: Owner::SharedRead,
        written_by: "lattice_brain/graph/projection/v2_schema.py",
        note: "VIEW over edges_v2",
    },
    // ── platform state that happens to live in the same file ─────────
    TableOwnership {
        file: GRAPH_DB,
        table: "workspace_os_state",
        owner: Owner::RustPlatform,
        written_by: "latticeai/core/workspace_os.py",
        note: "one JSON blob per workspace; R1 becomes its writer",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "workspace_os_meta",
        owner: Owner::RustPlatform,
        written_by: "latticeai/core/workspace_os.py",
        note: "",
    },
    TableOwnership {
        file: GRAPH_DB,
        table: "conversation_messages",
        owner: Owner::RustPlatform,
        written_by: "lattice_brain/conversations.py",
        note: "R4/R5 own direct history state (list, search, clear); since W3a the chat-turn record is native too (lattice_chat::turn), so there is no exception left",
    },
];

/// The table's owner, or `None` when the map does not know it.
///
/// `None` is a finding, not a default: a table the product has and this map
/// does not is exactly what `tests/db_write_ownership.rs` fails on.
pub fn owner_of(table: &str) -> Option<Owner> {
    TABLES
        .iter()
        .find(|row| row.table == table)
        .map(|row| row.owner)
}

/// Whether a Rust crate may write this table.
///
/// Unknown tables answer `false` — the safe direction, since the unknown
/// half of this product is the Brain.
pub fn rust_may_write(table: &str) -> bool {
    owner_of(table).is_some_and(Owner::rust_may_write)
}

/// Every table Rust owns.
pub fn rust_owned() -> impl Iterator<Item = &'static TableOwnership> {
    TABLES.iter().filter(|row| row.owner.rust_may_write())
}

/// Durable state that is not SQLite: the JSON/JSONL files and directories
/// under the data directory.
///
/// They are in this module because they answer the same question the table
/// map answers, and a Wave-2 crate that reaches for `users.json` needs the
/// same "may I write this" verdict it needs for a table.
pub mod state_files {
    use super::Owner;

    /// One durable file or directory under the data directory.
    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub struct StateStore {
        /// Name under the data directory.
        pub name: &'static str,
        /// Who may write it.
        pub owner: Owner,
        /// The Python module that owns it today.
        pub written_by: &'static str,
    }

    /// Accounts and roles.
    pub const USERS: &str = "users.json";
    /// Login sessions.
    pub const SESSIONS: &str = "sessions.json";
    /// Invite tokens.
    pub const INVITATIONS: &str = "invitations.json";
    /// Audit events (the `.jsonl` sibling is the append log).
    pub const AUDIT_LOG: &str = "audit_log.json";
    /// Process-level audit trail.
    pub const PROCESS_AUDIT: &str = "process_audit.jsonl";
    /// VPC settings.
    pub const VPC_CONFIG: &str = "vpc_config.json";
    /// SSO/OIDC settings.
    pub const SSO_CONFIG: &str = "sso_config.json";
    /// Hook registry.
    pub const HOOKS: &str = "hooks.json";
    /// Hook run history.
    pub const HOOKS_RUNS: &str = "hooks_runs.json";
    /// Pending tool approvals.
    pub const PERMISSION_QUEUE: &str = "permission_queue.json";
    /// The autonomy dial.
    pub const PERMISSION_MODE: &str = "permission_mode.json";
    /// Registered agents.
    pub const AGENT_REGISTRY: &str = "agent_registry.json";
    /// User-added MCP servers.
    pub const CUSTOM_MCPS: &str = "custom_mcps.json";
    /// Onboarding funnel counters.
    pub const FUNNEL_METRICS: &str = "funnel_metrics.json";
    /// Automation trigger state.
    pub const TRIGGERS_STATE: &str = "triggers_state.json";
    /// Workspace/org state, runs, snapshot index, skills, workflows.
    pub const WORKSPACE_OS: &str = "workspace_os.json";
    /// Flat chat message log.
    pub const CHAT_HISTORY: &str = "chat_history.json";
    /// One JSON file per project session.
    pub const PROJECT_SESSIONS: &str = "project_sessions";
    /// Workspace snapshot tree.
    pub const WORKSPACE_SNAPSHOTS: &str = "workspace_snapshots";
    /// Workspace export tree.
    pub const WORKSPACE_EXPORTS: &str = "workspace_exports";
    /// Graph blob payloads.
    pub const KNOWLEDGE_GRAPH_BLOBS: &str = "knowledge_graph_blobs";
    /// This device's signing key for peer sync.
    pub const DEVICE_IDENTITY: &str = "device_identity.key";
    /// Telegram chat allowlist.
    pub const TELEGRAM_CHATS: &str = "telegram_chats.json";

    /// Every non-SQLite store, with its writer.
    pub const STATE_FILES: &[StateStore] = &[
        StateStore {
            name: USERS,
            owner: Owner::RustPlatform,
            written_by: "latticeai/core/users.py",
        },
        StateStore {
            name: SESSIONS,
            owner: Owner::RustPlatform,
            written_by: "latticeai/core/sessions.py",
        },
        StateStore {
            name: INVITATIONS,
            owner: Owner::RustPlatform,
            written_by: "latticeai/runtime/persistence_runtime.py",
        },
        StateStore {
            name: AUDIT_LOG,
            owner: Owner::RustPlatform,
            written_by: "latticeai/core/audit.py",
        },
        StateStore {
            name: PROCESS_AUDIT,
            owner: Owner::RustPlatform,
            written_by: "latticeai/services/process_audit.py",
        },
        StateStore {
            name: VPC_CONFIG,
            owner: Owner::RustPlatform,
            written_by: "latticeai/core/enterprise_admin.py",
        },
        StateStore {
            name: SSO_CONFIG,
            owner: Owner::RustPlatform,
            written_by: "latticeai/core/enterprise_admin.py",
        },
        StateStore {
            name: HOOKS,
            owner: Owner::RustPlatform,
            written_by: "lattice_brain/runtime/hooks.py",
        },
        StateStore {
            name: HOOKS_RUNS,
            owner: Owner::RustPlatform,
            written_by: "lattice_brain/runtime/hooks.py",
        },
        StateStore {
            name: PERMISSION_QUEUE,
            owner: Owner::RustPlatform,
            written_by: "latticeai/api/permissions.py",
        },
        StateStore {
            name: PERMISSION_MODE,
            owner: Owner::RustPlatform,
            written_by: "latticeai/services/permission_mode_service.py",
        },
        StateStore {
            name: AGENT_REGISTRY,
            owner: Owner::RustPlatform,
            written_by: "latticeai/runtime/persistence_runtime.py",
        },
        StateStore {
            name: CUSTOM_MCPS,
            owner: Owner::RustPlatform,
            written_by: "latticeai/api/mcp.py",
        },
        StateStore {
            name: FUNNEL_METRICS,
            owner: Owner::RustPlatform,
            written_by: "latticeai/runtime/persistence_runtime.py",
        },
        StateStore {
            name: TRIGGERS_STATE,
            owner: Owner::RustPlatform,
            written_by: "latticeai/services/triggers.py",
        },
        StateStore {
            name: WORKSPACE_OS,
            owner: Owner::RustPlatform,
            written_by: "latticeai/core/workspace_os.py",
        },
        StateStore {
            name: CHAT_HISTORY,
            owner: Owner::RustPlatform,
            written_by: "latticeai/services/memory_service/service.py",
        },
        StateStore {
            name: PROJECT_SESSIONS,
            owner: Owner::RustPlatform,
            written_by: "latticeai/runtime/build_phases/features.py",
        },
        StateStore {
            name: WORKSPACE_SNAPSHOTS,
            owner: Owner::RustPlatform,
            written_by: "latticeai/core/workspace_os.py",
        },
        StateStore {
            name: WORKSPACE_EXPORTS,
            owner: Owner::RustPlatform,
            written_by: "latticeai/core/workspace_os.py",
        },
        StateStore {
            name: KNOWLEDGE_GRAPH_BLOBS,
            owner: Owner::RustPlatform,
            written_by: "lattice_brain/core.py — GraphWriter::blob_dir since v11.6.0 §W1",
        },
        StateStore {
            name: DEVICE_IDENTITY,
            owner: Owner::RustPlatform,
            written_by: "lattice_brain/graph/identity.py",
        },
        StateStore {
            name: TELEGRAM_CHATS,
            owner: Owner::Worker,
            written_by: "latticeai/integrations/telegram_bot/config.py",
        },
    ];

    /// The store's owner, or `None` when the map does not know the name.
    pub fn owner_of(name: &str) -> Option<Owner> {
        STATE_FILES
            .iter()
            .find(|row| row.name == name)
            .map(|row| row.owner)
    }
}
