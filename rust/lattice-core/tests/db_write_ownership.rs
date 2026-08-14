//! The table-ownership map, checked against a database the product built.
//!
//! `db::tables::TABLES` is load-bearing: Wave-2 packages read it to decide
//! whether a family may open a write connection or must delegate over the
//! worker seam. A map assembled by reading Python once and never checked again
//! rots silently — a new table appears, nobody classifies it, and the first
//! crate to meet it guesses.
//!
//! So this walks `sqlite_master` of `rust/fixtures/parity_store.sqlite` — a
//! Brain built by the real Python write path and committed — and fails on any
//! object the map does not name. It is one-directional on purpose: the map also
//! carries tables the fixture happens not to have (`vector_jobs` before a first
//! embed, `image_embeddings` before a first picture, `storage_meta` on a store
//! never opened through `StorageEngine.initialize`), and those are documented
//! by their `note`, not by their absence here.

use std::collections::BTreeSet;
use std::path::PathBuf;

use lattice_core::db::tables::{state_files, Owner, GRAPH_DB, TABLES};
use lattice_core::db::{open_read_only, RuntimeConfig};

/// FTS5 keeps its index in sibling tables named after the virtual table. They
/// are SQLite's bookkeeping, not the product's schema, so they are not map rows.
const FTS_SHADOW_SUFFIXES: [&str; 5] = ["_data", "_idx", "_content", "_docsize", "_config"];

fn fixtures() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("rust/ workspace root")
        .join("fixtures")
}

/// Open the committed store from a copy, so a stray `-wal`/`-shm` sidecar can
/// never land next to a checked-in fixture (the hygiene rule the retrieval
/// suites already follow).
fn fixture_objects() -> BTreeSet<(String, String)> {
    let dir = tempfile::tempdir().expect("tempdir");
    let target = dir.path().join("parity_store.sqlite");
    std::fs::copy(fixtures().join("parity_store.sqlite"), &target)
        .expect("the committed fixture store must exist");
    let conn = open_read_only(&target).expect("the fixture must open read-only");
    let mut statement = conn
        .prepare(
            "SELECT type, name FROM sqlite_master \
             WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' \
             ORDER BY name",
        )
        .expect("sqlite_master query");
    let rows = statement
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })
        .expect("rows")
        .collect::<Result<BTreeSet<_>, _>>()
        .expect("rows");
    drop(statement);
    drop(conn);
    rows
}

fn is_fts_shadow(name: &str, mapped: &BTreeSet<&str>) -> bool {
    FTS_SHADOW_SUFFIXES.iter().any(|suffix| {
        name.strip_suffix(suffix)
            .is_some_and(|base| mapped.contains(base))
    })
}

#[test]
fn every_object_in_a_real_brain_has_an_owner() {
    let mapped: BTreeSet<&str> = TABLES.iter().map(|row| row.table).collect();
    let mut unclassified = Vec::new();
    for (kind, name) in fixture_objects() {
        if is_fts_shadow(&name, &mapped) {
            continue;
        }
        if !mapped.contains(name.as_str()) {
            unclassified.push(format!("{kind} {name}"));
        }
    }
    assert!(
        unclassified.is_empty(),
        "these objects exist in rust/fixtures/parity_store.sqlite and are not \
         in db::tables::TABLES, so no Wave-2 package can tell whether Rust may \
         write them: {unclassified:?}"
    );
}

#[test]
fn the_fixture_proves_the_interesting_rows_are_real() {
    // A map that named tables the product does not have would pass the sweep
    // above while being fiction. These four are the ones the ownership split
    // actually turns on, so they are checked to exist.
    let present: BTreeSet<String> = fixture_objects()
        .into_iter()
        .map(|(_, name)| name)
        .collect();
    for table in ["nodes", "edges", "conversation_messages", "kgv2_nodes"] {
        assert!(
            present.contains(table),
            "{table} is classified in db::tables but absent from a real Brain"
        );
    }
}

#[test]
fn the_split_matches_the_plan() {
    // v11.6.0 §W3b: `knowledge_graph.sqlite` has exactly one writer and it is
    // this process. The seventeen graph tables that were the Python worker's
    // are now `graph_write::GraphWriter`'s, so the *only* rows a Rust crate may
    // not write are the projections — a view and an FTS index, which nobody
    // writes directly. Spot-check that against the map rather than the prose.
    let rust_owned: BTreeSet<&str> = lattice_core::db::tables::rust_owned()
        .map(|row| row.table)
        .collect();
    let projections: BTreeSet<&str> = TABLES
        .iter()
        .filter(|row| row.owner == Owner::SharedRead)
        .map(|row| row.table)
        .collect();
    assert_eq!(
        projections,
        BTreeSet::from(["kgv2_edges", "kgv2_nodes", "node_fts"]),
        "the read-only half is the projection layer and nothing else"
    );
    let all: BTreeSet<&str> = TABLES.iter().map(|row| row.table).collect();
    assert_eq!(
        &rust_owned | &projections,
        all,
        "every table is either Rust's to write or a projection; a WORKER row \
         would mean a second writer of the one SQLite file"
    );
    assert!(
        rust_owned.contains("nodes") && rust_owned.contains("conversation_messages"),
        "both halves of the file — the Brain and the platform state — are Rust's"
    );

    for row in TABLES {
        assert_eq!(row.file, GRAPH_DB);
        match row.owner {
            Owner::Worker | Owner::SharedRead => assert!(
                !row.owner.rust_may_write(),
                "{} must not be Rust-writable",
                row.table
            ),
            Owner::RustPlatform => assert!(row.owner.rust_may_write()),
        }
    }
}

/// The invariant stated as one assertion, so it fails by name.
#[test]
fn no_table_in_the_graph_database_is_written_by_the_worker() {
    let worker: Vec<&str> = TABLES
        .iter()
        .filter(|row| row.owner == Owner::Worker)
        .map(|row| row.table)
        .collect();
    assert!(
        worker.is_empty(),
        "v11.6.0 §W3b made Rust the single writer of {GRAPH_DB}; these rows \
         still name the Python worker: {worker:?}"
    );
}

#[test]
fn the_state_file_map_resolves_to_paths_under_the_data_dir() {
    let config = RuntimeConfig::resolve(Some("/srv/brain"), None, None, None);
    for row in state_files::STATE_FILES {
        let path = config.state_file(row.name);
        assert!(
            path.starts_with("/srv/brain"),
            "{} escaped the data directory",
            row.name
        );
        assert_eq!(
            state_files::owner_of(row.name),
            Some(row.owner),
            "{} looks up to a different owner than it declares",
            row.name
        );
    }
    // The graph itself is not a state file; it has its own accessor.
    assert_eq!(
        config.graph_db_path(),
        std::path::Path::new("/srv/brain").join(GRAPH_DB)
    );
}
