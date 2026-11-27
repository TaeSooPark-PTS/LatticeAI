use std::collections::HashSet;

use rusqlite::{params, OptionalExtension, Transaction};
use serde_json::Value;

use crate::db::CoreError;
use crate::temporal::stamp_temporal_fact;

const EDGE_PREFIX: &str = "temporal_supersedes:";
const UPLOAD_URI_PREFIX: &str = "upload://";
const LEGACY_UPLOAD_MARKER: &str = "lattice-upload-";
const UPLOAD_MIN_OVERLAP: f64 = 0.5;

pub const SAME_SOURCE_URI_SQL: &str = "SELECT id, metadata_json FROM nodes
     WHERE id <> ?1
       AND type = (SELECT type FROM nodes WHERE id = ?1)
       AND CASE WHEN json_valid(metadata_json) THEN json_extract(metadata_json, '$.source_uri') END = ?2";

pub const SAME_FILENAME_SQL: &str = "SELECT id, metadata_json FROM nodes
     WHERE id <> ?1
       AND type = (SELECT type FROM nodes WHERE id = ?1)
       AND CASE WHEN json_valid(metadata_json) THEN json_extract(metadata_json, '$.filename') END = ?2";

pub const SAME_DOCUMENT_INDEX_SQL: &str = "
CREATE INDEX IF NOT EXISTS idx_nodes_type_source_uri ON nodes(
  type, (CASE WHEN json_valid(metadata_json) THEN json_extract(metadata_json, '$.source_uri') END));
CREATE INDEX IF NOT EXISTS idx_nodes_type_filename ON nodes(
  type, (CASE WHEN json_valid(metadata_json) THEN json_extract(metadata_json, '$.filename') END));
";

pub fn upload_source_uri(filename: &str) -> String {
    format!("{UPLOAD_URI_PREFIX}{filename}")
}

struct Version {
    id: String,
    superseded: bool,
}

fn field<'a>(meta: &'a Value, key: &str) -> &'a str {
    meta.get(key).and_then(Value::as_str).unwrap_or_default()
}

fn same_identity(new: &Value, old: &Value) -> bool {
    if ["workspace_id", "owner", "source_type"]
        .iter()
        .any(|key| field(new, key) != field(old, key))
    {
        return false;
    }
    let (uri, old_uri) = (field(new, "source_uri"), field(old, "source_uri"));
    if uri.is_empty() {
        return false;
    }
    if uri.starts_with(UPLOAD_URI_PREFIX) {
        let name = field(new, "filename");
        return !name.is_empty()
            && name == field(old, "filename")
            && (old_uri == uri || old_uri.contains(LEGACY_UPLOAD_MARKER));
    }
    uri == old_uri
}

fn metadata_of(txn: &Transaction<'_>, id: &str) -> Result<Value, CoreError> {
    let raw: Option<String> = txn.query_row(
        "SELECT metadata_json FROM nodes WHERE id = ?1",
        params![id],
        |row| row.get(0),
    )?;
    Ok(raw
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or(Value::Null))
}

fn other_versions(txn: &Transaction<'_>, id: &str) -> Result<Vec<Version>, CoreError> {
    let meta = metadata_of(txn, id)?;
    if field(&meta, "source_uri").is_empty() {
        return Ok(Vec::new());
    }
    let (sql, value) = if field(&meta, "source_uri").starts_with(UPLOAD_URI_PREFIX) {
        (SAME_FILENAME_SQL, field(&meta, "filename"))
    } else {
        (SAME_SOURCE_URI_SQL, field(&meta, "source_uri"))
    };
    let mut statement = txn.prepare(sql)?;
    let rows = statement.query_map(params![id, value], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    })?;
    let mut out = Vec::new();
    for row in rows {
        let (other, raw) = row?;
        let old: Value = serde_json::from_str(&raw).unwrap_or(Value::Null);
        if same_identity(&meta, &old) {
            out.push(Version {
                id: other,
                superseded: !field(&old, "superseded_by").is_empty(),
            });
        }
    }
    Ok(out)
}

fn words(txn: &Transaction<'_>, id: &str) -> Result<HashSet<String>, CoreError> {
    let mut statement = txn.prepare("SELECT text FROM chunks WHERE source_node = ?1")?;
    let rows = statement.query_map(params![id], |row| row.get::<_, String>(0))?;
    let mut out = HashSet::new();
    for row in rows {
        out.extend(
            row?.split(|c: char| !c.is_alphanumeric())
                .filter(|word| !word.is_empty())
                .map(str::to_lowercase),
        );
    }
    Ok(out)
}

fn overlaps(new: &HashSet<String>, old: &HashSet<String>) -> bool {
    let union = new.union(old).count();
    union > 0 && new.intersection(old).count() as f64 / union as f64 >= UPLOAD_MIN_OVERLAP
}

pub(crate) fn foreign_owner(
    txn: &Transaction<'_>,
    id: &str,
    incoming: Option<&str>,
) -> Result<Option<String>, CoreError> {
    let existing: Option<String> = txn
        .query_row(
            "SELECT CASE WHEN json_valid(metadata_json)
                         THEN json_extract(metadata_json, '$.owner') END
             FROM nodes WHERE id = ?1",
            params![id],
            |row| row.get(0),
        )
        .optional()?
        .flatten();
    Ok(existing.filter(|owner| !owner.is_empty() && Some(owner.as_str()) != incoming))
}

pub(crate) fn revive_version(
    txn: &Transaction<'_>,
    id: &str,
    from_ids: &[String],
) -> Result<(), CoreError> {
    let has_v2_edges = table_has(txn, "edges_v2", "id")?;
    let mut revived = false;
    for from in from_ids {
        let edge = format!("{EDGE_PREFIX}{from}->{id}");
        revived |= txn.execute("DELETE FROM edges WHERE id = ?1", params![edge])? > 0;
        if has_v2_edges {
            txn.execute("DELETE FROM edges_v2 WHERE id = ?1", params![edge])?;
        }
    }
    if !revived {
        return Ok(());
    }
    if table_has(txn, "nodes_v2", "superseded_by")? {
        txn.execute(
            "UPDATE nodes_v2 SET superseded_by = NULL WHERE id = ?1",
            params![id],
        )?;
    }
    for (column, value) in [("lifecycle_status", "'active'"), ("valid_to", "NULL")] {
        if table_has(txn, "nodes_v2", column)? {
            txn.execute(
                &format!("UPDATE nodes_v2 SET {column} = {value} WHERE id = ?1"),
                params![id],
            )?;
        }
    }
    Ok(())
}

fn table_has(txn: &Transaction<'_>, table: &str, column: &str) -> Result<bool, CoreError> {
    Ok(txn.query_row(
        "SELECT EXISTS(SELECT 1 FROM pragma_table_info(?1) WHERE name = ?2)",
        params![table, column],
        |row| row.get(0),
    )?)
}

pub(crate) fn supersede_prior_versions(
    txn: &Transaction<'_>,
    id: &str,
    subject: &str,
    recorded_at: &str,
) -> Result<(), CoreError> {
    let versions = other_versions(txn, id)?;
    let ids: Vec<String> = versions.iter().map(|version| version.id.clone()).collect();
    revive_version(txn, id, &ids)?;
    let upload = field(&metadata_of(txn, id)?, "source_uri").starts_with(UPLOAD_URI_PREFIX);
    let new_words = if upload {
        words(txn, id)?
    } else {
        HashSet::new()
    };
    for version in versions.iter().filter(|version| !version.superseded) {
        if upload && !overlaps(&new_words, &words(txn, &version.id)?) {
            continue;
        }
        stamp_temporal_fact(
            txn,
            id,
            subject,
            recorded_at,
            None,
            recorded_at,
            Some(&version.id),
        )?;
    }
    Ok(())
}
