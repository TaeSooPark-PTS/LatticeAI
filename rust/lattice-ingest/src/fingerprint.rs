//! Per-file ingest fingerprint: path + size + mtime + content sha256.
//!
//! The honest home is ``ingestion_provenance`` (v3.6). A folder file's
//! absolute path is already ``source_uri``; the stamp and the file-bytes
//! digest ride in ``metadata_json`` so we do not invent a second table.
//! sha256 is computed only when size or mtime moved.
//!
//! Deleted files are **reported, never removed** — dropping a node is a
//! product decision, not an incremental-ingest side effect.

use std::collections::HashSet;
use std::path::Path;

use lattice_core::db::Store;
use serde_json::{json, Map, Value};

use crate::hashes::file_content_hash;
use crate::pystr::round3;
use crate::watch::ScannedFile;

/// ``metadata_json`` key for the on-disk size in bytes.
pub const META_SIZE: &str = "file_size";
/// ``metadata_json`` key for ``round(st_mtime, 3)``.
pub const META_MTIME: &str = "file_mtime";
/// ``metadata_json`` key for sha256 of the file's bytes.
pub const META_SHA: &str = "file_sha256";

/// What a previous ingest recorded for one path.
#[derive(Debug, Clone, PartialEq)]
pub struct StoredFingerprint {
    /// Size in bytes.
    pub size: u64,
    /// ``round(st_mtime, 3)``.
    pub mtime: f64,
    /// Lowercase hex sha256 of the file bytes.
    pub sha256: String,
}

/// Whether this file must go through parse / extract / embed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SkipDecision {
    /// Size and mtime match — do not even open the file.
    SkipByStamp,
    /// Stamp moved but the bytes are the same — skip parse/extract/embed.
    SkipByHash,
    /// New or changed: the caller must ingest.
    Ingest,
}

impl StoredFingerprint {
    /// Parse the three keys out of a provenance ``metadata_json`` object.
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

    /// True when the live ``(size, mtime)`` stamp is the one we stored.
    pub fn stamp_matches(&self, size: u64, mtime: f64) -> bool {
        self.size == size && round3(self.mtime) == round3(mtime)
    }

    /// True when the live file-bytes digest is the one we stored.
    pub fn hash_matches(&self, sha256: &str) -> bool {
        self.sha256 == sha256
    }
}

/// Write the stamp + digest onto a note's provenance metadata.
pub fn attach(meta: &mut Map<String, Value>, size: u64, mtime: f64, sha256: &str) {
    meta.insert(META_SIZE.into(), json!(size));
    meta.insert(META_MTIME.into(), json!(round3(mtime)));
    meta.insert(META_SHA.into(), json!(sha256));
}

/// sha256 of the file's raw bytes — the identity half of the fingerprint.
pub fn hash_bytes(bytes: &[u8]) -> String {
    file_content_hash(bytes)
}

/// Latest stored fingerprint for this absolute path, if one exists.
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

/// Decide whether ``file`` can skip the ingest pipeline.
///
/// ``bytes`` is ``Some`` only after the caller already opened the file
/// (stamp moved). Passing ``None`` never hashes.
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

/// Provenance ``source_uri``s under ``root`` that are not in ``present``.
///
/// Report-only: the caller must not delete the corresponding nodes.
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

        let present = HashSet::from([uri]);
        assert!(missing_under_root(&store, dir.path(), &present).is_empty());
        let empty = HashSet::new();
        assert_eq!(missing_under_root(&store, dir.path(), &empty).len(), 1);
    }
}
