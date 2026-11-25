use std::collections::HashSet;
use std::path::Path;

use lattice_core::db::Store;
use lattice_core::graph_write::GraphWriter;
use serde_json::{json, Map, Value};

use crate::hashes::file_content_hash;
use crate::pystr::round3;
use crate::watch::ScannedFile;

pub const META_SIZE: &str = "file_size";
pub const META_MTIME: &str = "file_mtime";
pub const META_SHA: &str = "file_sha256";

#[derive(Debug, Clone, PartialEq)]
pub struct StoredFingerprint {
    pub size: u64,
    pub mtime: f64,
    pub sha256: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SkipDecision {
    SkipByStamp,
    SkipByHash,
    Ingest,
}

impl StoredFingerprint {
    pub fn from_metadata(meta: &Map<String, Value>) -> Option<Self> {
        let size = meta.get(META_SIZE).and_then(Value::as_u64).or_else(|| {
            meta.get(META_SIZE)
                .and_then(Value::as_i64)
                .filter(|value| *value >= 0)
                .map(|value| value as u64)
        })?;
        let mtime = meta.get(META_MTIME).and_then(Value::as_f64)?;
        let sha256 = meta
            .get(META_SHA)
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())?
            .to_string();
        Some(Self {
            size,
            mtime: round3(mtime),
            sha256,
        })
    }

    pub fn stamp_matches(&self, size: u64, mtime: f64) -> bool {
        self.size == size && round3(self.mtime) == round3(mtime)
    }

    pub fn hash_matches(&self, sha256: &str) -> bool {
        self.sha256 == sha256
    }
}

pub fn attach(meta: &mut Map<String, Value>, size: u64, mtime: f64, sha256: &str) {
    meta.insert(META_SIZE.into(), json!(size));
    meta.insert(META_MTIME.into(), json!(round3(mtime)));
    meta.insert(META_SHA.into(), json!(sha256));
}

pub fn hash_bytes(bytes: &[u8]) -> String {
    file_content_hash(bytes)
}

pub fn restamp(graph: &GraphWriter, source_uri: &str, size: u64, mtime: f64, sha256: &str) -> bool {
    let mut patch = Map::new();
    attach(&mut patch, size, mtime, sha256);
    graph
        .update_ingestion_metadata(source_uri, &patch)
        .unwrap_or(false)
}

pub fn lookup(store: &Store, source_uri: &str) -> Option<StoredFingerprint> {
    if source_uri.is_empty() {
        return None;
    }
    let uri = source_uri.to_string();
    store
        .with_read_conn(move |conn| {
            let raw: Option<String> = conn
                .query_row(
                    "SELECT metadata_json FROM ingestion_provenance \
                     WHERE source_uri = ?1 \
                     ORDER BY created_at DESC, id DESC LIMIT 1",
                    rusqlite::params![uri],
                    |row| row.get(0),
                )
                .ok()
                .flatten();
            let Some(raw) = raw else {
                return Ok(None);
            };
            let parsed: Value = serde_json::from_str(&raw).unwrap_or(Value::Null);
            Ok(parsed
                .as_object()
                .and_then(StoredFingerprint::from_metadata))
        })
        .ok()
        .flatten()
}

pub fn decide(
    stored: Option<&StoredFingerprint>,
    file: &ScannedFile,
    bytes: Option<&[u8]>,
) -> SkipDecision {
    let Some(stored) = stored else {
        return SkipDecision::Ingest;
    };
    if stored.stamp_matches(file.size, file.mtime) {
        return SkipDecision::SkipByStamp;
    }
    match bytes {
        Some(data) if stored.hash_matches(&hash_bytes(data)) => SkipDecision::SkipByHash,
        _ => SkipDecision::Ingest,
    }
}

pub fn missing_under_root(store: &Store, root: &Path, present: &HashSet<String>) -> Vec<String> {
    let root_s = root.display().to_string();
    let prefix = if root_s.ends_with('/') {
        root_s.clone()
    } else {
        format!("{root_s}/")
    };
    let like = format!("{prefix}%");
    let listed = store.with_read_conn(move |conn| {
        let mut statement = match conn.prepare(
            "SELECT DISTINCT source_uri FROM ingestion_provenance \
             WHERE source_type = 'note' \
               AND source_uri IS NOT NULL \
               AND (source_uri = ?1 OR source_uri LIKE ?2)",
        ) {
            Ok(statement) => statement,
            Err(_) => return Ok(Vec::new()),
        };
        let rows = statement.query_map(rusqlite::params![root_s, like], |row| {
            row.get::<_, Option<String>>(0)
        })?;
        Ok(rows
            .filter_map(Result::ok)
            .flatten()
            .filter(|uri| !uri.is_empty())
            .collect::<Vec<_>>())
    });
    match listed {
        Ok(uris) => uris
            .into_iter()
            .filter(|uri| !present.contains(uri))
            .collect(),
        Err(_) => Vec::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    use lattice_core::graph_write::types::IngestionRecord;
    use lattice_core::graph_write::GraphWriter;

    fn scanned(path: &str, size: u64, mtime: f64) -> ScannedFile {
        ScannedFile {
            relative_path: "a.md".into(),
            path: path.into(),
            extension: ".md".into(),
            size,
            mtime,
        }
    }

    #[test]
    fn a_matching_stamp_skips_without_hashing() {
        let stored = StoredFingerprint {
            size: 10,
            mtime: 1.5,
            sha256: "abc".into(),
        };
        let file = scanned("/tmp/a.md", 10, 1.5);
        assert_eq!(
            decide(Some(&stored), &file, None),
            SkipDecision::SkipByStamp
        );
    }

    #[test]
    fn a_touch_with_the_same_bytes_skips_by_hash() {
        let bytes = b"hello";
        let stored = StoredFingerprint {
            size: 4,
            mtime: 1.0,
            sha256: hash_bytes(bytes),
        };
        let file = scanned("/tmp/a.md", 5, 2.0);
        assert_eq!(
            decide(Some(&stored), &file, Some(bytes)),
            SkipDecision::SkipByHash
        );
    }

    #[test]
    fn changed_bytes_must_be_reingested() {
        let stored = StoredFingerprint {
            size: 4,
            mtime: 1.0,
            sha256: hash_bytes(b"old"),
        };
        let file = scanned("/tmp/a.md", 5, 2.0);
        assert_eq!(
            decide(Some(&stored), &file, Some(b"new!")),
            SkipDecision::Ingest
        );
        assert_eq!(decide(None, &file, Some(b"new!")), SkipDecision::Ingest);
    }

    #[test]
    fn provenance_is_the_source_of_truth() {
        let dir = tempfile::tempdir().expect("tmp");
        let store = Arc::new(Store::open(&dir.path().join("kg.sqlite")).expect("store"));
        let writer = GraphWriter::open(Arc::clone(&store), dir.path().join("blobs")).expect("w");
        let uri = dir.path().join("note.md").display().to_string();
        let mut metadata = Map::new();
        attach(&mut metadata, 12, 99.1234, "deadbeef");
        writer
            .record_ingestion(&IngestionRecord {
                node_id: "webdoc:test".into(),
                source_type: "note".into(),
                pipeline: "unified-ingestion".into(),
                source_uri: Some(uri.clone()),
                metadata,
                ..Default::default()
            })
            .expect("prov");
        let found = lookup(&store, &uri).expect("stored");
        assert_eq!(found.size, 12);
        assert_eq!(found.sha256, "deadbeef");
        assert_eq!(round3(found.mtime), round3(99.1234));

        assert!(restamp(&writer, &uri, 20, 100.5, "deadbeef"));
        let touched = lookup(&store, &uri).expect("restamped");
        assert_eq!(touched.size, 20);
        assert_eq!(touched.sha256, "deadbeef");
        assert_eq!(round3(touched.mtime), round3(100.5));

        let present = HashSet::from([uri]);
        assert!(missing_under_root(&store, dir.path(), &present).is_empty());
        let empty = HashSet::new();
        assert_eq!(missing_under_root(&store, dir.path(), &empty).len(), 1);
    }
}
