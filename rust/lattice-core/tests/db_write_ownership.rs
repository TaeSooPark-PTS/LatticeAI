use std::collections::BTreeSet;
use std::path::PathBuf;

use lattice_core::db::tables::{state_files, Owner, GRAPH_DB, TABLES};
use lattice_core::db::{open_read_only, RuntimeConfig};

const FTS_SHADOW_SUFFIXES: [&str; 5] = ["_data", "_idx", "_content", "_docsize", "_config"];

fn fixtures() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("rust/ workspace root")
        .join("fixtures")
}

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
    assert_eq!(
        config.graph_db_path(),
        std::path::Path::new("/srv/brain").join(GRAPH_DB)
    );
}
