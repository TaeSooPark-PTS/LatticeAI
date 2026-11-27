use std::collections::{HashMap, HashSet};
use std::path::Path;

use axum::body::{Body, Bytes};
use axum::extract::State;
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::Response;
use lattice_core::redact::redact_secret_text;
use rusqlite::Connection;
use serde_json::{json, Map, Value};

use crate::http_reply::{self, detail, parse_json_object};

use super::share::{
    belongs_to_other_workspace, emit_portability_audit, foreign_conversation_nodes,
    is_node_share_blocked, matches_workspace_boundary, metadata_of, node_workspace, v2_workspaces,
};
use super::status::require_graph;
use super::vault_render::{write_vault, VaultCounts, VaultLink, VaultNote};
use super::PortabilityState;

const FRAGMENT_TYPES: &[&str] = &[
    "airesponse",
    "chat",
    "chunk",
    "codesymbol",
    "computer",
    "conversation",
    "drive",
    "folder",
    "imagetext",
    "message",
    "model",
    "page",
    "section",
    "sheet",
    "slide",
    "source",
    "tool",
];
const OVERLAP_SLACK: i64 = 16;

#[derive(Clone, Copy)]
struct VaultLimits {
    notes: usize,
    note_bytes: usize,
    total_bytes: usize,
}

const LIMITS: VaultLimits = VaultLimits {
    notes: 5_000,
    note_bytes: 1024 * 1024,
    total_bytes: 64 * 1024 * 1024,
};

struct Scope {
    workspace_id: Option<String>,
    viewer: Option<String>,
}

struct NodeRow {
    value: Value,
    superseded_by: Option<String>,
    valid_from: Option<String>,
    valid_to: Option<String>,
    owner_id: Option<String>,
}

fn text_of<'a>(value: &'a Value, key: &str) -> &'a str {
    value.get(key).and_then(Value::as_str).unwrap_or("")
}

fn is_fragment(kind: &str) -> bool {
    let folded = kind.replace('_', "").to_ascii_lowercase();
    FRAGMENT_TYPES.contains(&folded.as_str())
}

fn owners_of(row: &NodeRow) -> Vec<String> {
    let meta = metadata_of(&row.value);
    [
        row.owner_id.as_deref(),
        meta.get("user_email").and_then(Value::as_str),
        meta.get("owner").and_then(Value::as_str),
    ]
    .into_iter()
    .flatten()
    .map(|owner| owner.trim().to_lowercase())
    .filter(|owner| !owner.is_empty())
    .collect()
}

fn owned_by(owners: &[String], viewer: &str) -> bool {
    let email = viewer.trim().to_lowercase();
    let user_id = lattice_auth::stable_user_id(&email).to_lowercase();
    owners
        .iter()
        .any(|owner| *owner == email || *owner == user_id)
}

fn foreign_personal(row: &NodeRow, scope: &Scope) -> bool {
    let Some(viewer) = scope.viewer.as_deref() else {
        return false;
    };
    if scope.workspace_id.as_deref() != Some("personal") {
        return false;
    }
    let owners = owners_of(row);
    !owners.is_empty() && !owned_by(&owners, viewer)
}

fn account_person(row: &NodeRow) -> bool {
    text_of(&row.value, "type").eq_ignore_ascii_case("person")
        && metadata_of(&row.value).get("email").is_some()
}

fn load_nodes(conn: &Connection) -> rusqlite::Result<Vec<NodeRow>> {
    let mut stmt = conn.prepare(
        "SELECT n.id, n.type, n.title, n.summary, n.metadata_json, n.created_at, \
                v.superseded_by, v.valid_from, v.valid_to, v.owner_id \
         FROM nodes n LEFT JOIN nodes_v2 v ON v.id = n.id \
         ORDER BY n.created_at, n.id",
    )?;
    let rows = stmt.query_map([], |row| {
        let optional = |index: usize| -> rusqlite::Result<Option<String>> {
            Ok(row
                .get::<_, Option<String>>(index)?
                .filter(|value| !value.is_empty()))
        };
        Ok(NodeRow {
            value: json!({
                "id": row.get::<_, String>(0)?,
                "type": row.get::<_, String>(1)?,
                "title": optional(2)?,
                "summary": optional(3)?,
                "metadata_json": optional(4)?,
                "created_at": optional(5)?,
            }),
            superseded_by: optional(6)?,
            valid_from: optional(7)?,
            valid_to: optional(8)?,
            owner_id: optional(9)?,
        })
    })?;
    rows.collect()
}

fn merge_chunk(body: &mut String, piece: &str, declared_overlap: i64) {
    let mut skip = 0usize;
    if declared_overlap > 0 {
        let window = (declared_overlap + OVERLAP_SLACK) as usize;
        for (count, (offset, ch)) in piece.char_indices().enumerate() {
            if count >= window {
                break;
            }
            let end = offset + ch.len_utf8();
            if body.ends_with(&piece[..end]) {
                skip = end;
            }
        }
    }
    let rest = &piece[skip..];
    if rest.is_empty() {
        return;
    }
    if skip == 0 && !body.is_empty() {
        body.push_str("\n\n");
    }
    body.push_str(rest);
}

struct Chunk {
    id: String,
    text: String,
    metadata: Value,
}

fn load_chunks(conn: &Connection, source: &str) -> rusqlite::Result<Vec<Chunk>> {
    let mut stmt = conn.prepare_cached(
        "SELECT id, text, metadata_json FROM chunks WHERE source_node = ?1 \
         ORDER BY CASE WHEN json_valid(metadata_json) \
                       THEN CAST(json_extract(metadata_json, '$.index') AS INTEGER) END, rowid",
    )?;
    let rows = stmt.query_map([source], |row| {
        let metadata_json: Option<String> = row.get(2)?;
        Ok(Chunk {
            id: row.get(0)?,
            text: row.get(1)?,
            metadata: json!({ "metadata_json": metadata_json }),
        })
    })?;
    rows.collect()
}

struct Collector<'a> {
    conn: &'a Connection,
    scope: &'a Scope,
    lookup: HashMap<&'a str, &'a Value>,
    workspaces: HashMap<String, String>,
}

impl Collector<'_> {
    fn foreign(&self, ids: &HashSet<String>) -> Result<HashSet<String>, String> {
        match self.scope.viewer.as_deref() {
            Some(viewer) => foreign_conversation_nodes(Some(self.conn), &self.lookup, ids, viewer),
            None => Ok(HashSet::new()),
        }
    }

    fn body(&self, row: &NodeRow) -> Result<String, String> {
        let id = text_of(&row.value, "id");
        let chunks = load_chunks(self.conn, id).map_err(|e| e.to_string())?;
        let ids: HashSet<String> = chunks.iter().map(|chunk| chunk.id.clone()).collect();
        let foreign = self.foreign(&ids)?;
        let mut body = String::new();
        let mut previous_end = 0i64;
        let mut withheld = false;
        for chunk in &chunks {
            let node = self.lookup.get(chunk.id.as_str()).copied();
            let blocked = foreign.contains(&chunk.id)
                || is_node_share_blocked(&chunk.metadata)
                || node.is_some_and(|node| {
                    is_node_share_blocked(node)
                        || belongs_to_other_workspace(
                            node_workspace(node, &self.workspaces),
                            self.scope.workspace_id.as_deref(),
                        )
                });
            if blocked {
                withheld = true;
                previous_end = 0;
                continue;
            }
            let meta = metadata_of(&chunk.metadata);
            let start = meta.get("start_char").and_then(Value::as_i64);
            let overlap = start.map_or(0, |start| previous_end - start);
            merge_chunk(&mut body, &chunk.text, overlap);
            previous_end = meta.get("end_char").and_then(Value::as_i64).unwrap_or(0);
        }
        if !chunks.is_empty() || withheld {
            return Ok(body);
        }
        let meta = metadata_of(&row.value);
        Ok(["content", "text"]
            .iter()
            .find_map(|key| meta.get(*key).and_then(Value::as_str))
            .unwrap_or_else(|| text_of(&row.value, "summary"))
            .to_string())
    }
}

fn load_links(
    conn: &Connection,
    positions: &HashMap<&str, usize>,
) -> rusqlite::Result<Vec<VaultLink>> {
    let mut parents: HashMap<String, String> = HashMap::new();
    let mut stmt = conn.prepare("SELECT id, source_node FROM chunks")?;
    for row in stmt.query_map([], |row| Ok((row.get(0)?, row.get(1)?)))? {
        let (id, source) = row?;
        parents.insert(id, source);
    }
    let position = |id: &str| {
        let owner = parents.get(id).map_or(id, String::as_str);
        positions.get(owner).copied()
    };
    let mut stmt = conn
        .prepare("SELECT from_node, to_node, type FROM edges ORDER BY from_node, to_node, type")?;
    let rows = stmt.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
        ))
    })?;
    let mut links = Vec::new();
    for row in rows {
        let (from, to, kind) = row?;
        if let (Some(from), Some(to)) = (position(&from), position(&to)) {
            if from != to {
                let kind = redact_secret_text(&kind).replace(['[', ']', '\n', '\r'], " ");
                links.push(VaultLink { from, to, kind });
            }
        }
    }
    Ok(links)
}

fn clip_body(body: &mut String, limit: usize) -> bool {
    if body.len() <= limit {
        return false;
    }
    let mut end = limit;
    while !body.is_char_boundary(end) {
        end -= 1;
    }
    body.truncate(end);
    true
}

fn collect(
    conn: &Connection,
    scope: &Scope,
    limits: VaultLimits,
) -> Result<(Vec<VaultNote>, Vec<VaultLink>, VaultCounts), String> {
    let rows = load_nodes(conn).map_err(|e| e.to_string())?;
    let collector = Collector {
        conn,
        scope,
        lookup: rows
            .iter()
            .map(|row| (text_of(&row.value, "id"), &row.value))
            .collect(),
        workspaces: v2_workspaces(conn),
    };
    let mut counts = VaultCounts::default();
    let mut candidates: Vec<&NodeRow> = Vec::new();
    for row in &rows {
        let workspace = node_workspace(&row.value, &collector.workspaces);
        if !matches_workspace_boundary(workspace, scope.workspace_id.as_deref(), false)
            || foreign_personal(row, scope)
        {
            continue;
        }
        if is_node_share_blocked(&row.value) {
            counts.withheld += 1;
            continue;
        }
        if is_fragment(text_of(&row.value, "type")) || account_person(row) {
            continue;
        }
        candidates.push(row);
    }
    let ids: HashSet<String> = candidates
        .iter()
        .map(|row| text_of(&row.value, "id").to_string())
        .collect();
    let foreign = collector.foreign(&ids)?;
    candidates.retain(|row| !foreign.contains(text_of(&row.value, "id")));

    let now = http_reply::now_iso_utc();
    let mut notes: Vec<VaultNote> = Vec::new();
    let mut kept: Vec<&str> = Vec::new();
    let mut total = 0usize;
    for row in candidates {
        if notes.len() >= limits.notes {
            counts.omitted += 1;
            continue;
        }
        let mut body = redact_secret_text(&collector.body(row)?);
        let truncated = clip_body(&mut body, limits.note_bytes);
        if total + body.len() > limits.total_bytes {
            counts.omitted += 1;
            continue;
        }
        total += body.len();
        counts.truncated += usize::from(truncated);
        let meta = metadata_of(&row.value);
        let id = text_of(&row.value, "id");
        let created = text_of(&row.value, "created_at");
        let source_visible = scope
            .viewer
            .as_deref()
            .is_none_or(|viewer| owned_by(&owners_of(row), viewer));
        let superseded = row.superseded_by.is_some()
            || row
                .valid_to
                .as_deref()
                .is_some_and(|end| end <= now.as_str());
        counts.history += usize::from(superseded);
        kept.push(id);
        notes.push(VaultNote {
            id: id.to_string(),
            kind: text_of(&row.value, "type").to_string(),
            title: redact_secret_text(text_of(&row.value, "title")),
            body,
            valid_from: row
                .valid_from
                .clone()
                .or_else(|| (!created.is_empty()).then(|| created.to_string())),
            valid_to: row.valid_to.clone(),
            superseded_by: row.superseded_by.clone(),
            source_uri: meta
                .get("source_uri")
                .and_then(Value::as_str)
                .filter(|uri| source_visible && !uri.is_empty())
                .map(redact_secret_text),
            workspace: node_workspace(&row.value, &collector.workspaces),
            superseded,
        });
    }
    counts.notes = notes.len();
    let positions: HashMap<&str, usize> = kept
        .iter()
        .enumerate()
        .map(|(index, id)| (*id, index))
        .collect();
    let mut links = load_links(conn, &positions).map_err(|e| e.to_string())?;
    for (index, note) in notes.iter_mut().enumerate() {
        let successor = note
            .superseded_by
            .as_deref()
            .and_then(|id| positions.get(id))
            .copied();
        match successor {
            Some(to) => links.push(VaultLink {
                from: index,
                to,
                kind: "superseded by".into(),
            }),
            None => note.superseded_by = None,
        }
    }
    Ok((notes, links, counts))
}

fn build_vault(db: &Path, scope: &Scope) -> Result<(Vec<u8>, VaultCounts), String> {
    let (notes, links, counts) = if db.exists() {
        let conn = lattice_core::db::open_read_only(db).map_err(|e| e.to_string())?;
        let snapshot = conn.unchecked_transaction().map_err(|e| e.to_string())?;
        collect(&snapshot, scope, LIMITS)?
    } else {
        Default::default()
    };
    Ok((write_vault(&notes, &links, counts)?, counts))
}

fn zip_response(bytes: Vec<u8>, counts: VaultCounts) -> Response {
    let day: String = http_reply::now_iso_utc().chars().take(10).collect();
    let mut builder = Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/zip")
        .header(
            header::CONTENT_DISPOSITION,
            format!("attachment; filename=\"lattice-brain-vault-{day}.zip\""),
        )
        .header(header::CACHE_CONTROL, "no-store");
    let mut exposed = Vec::new();
    if let Value::Object(map) = counts.to_json() {
        for (key, value) in map {
            let name = format!("x-lattice-vault-{key}");
            builder = builder.header(name.as_str(), value.to_string());
            exposed.push(name);
        }
    }
    builder
        .header(header::ACCESS_CONTROL_EXPOSE_HEADERS, exposed.join(", "))
        .body(Body::from(bytes))
        .unwrap_or_else(|error| detail(StatusCode::INTERNAL_SERVER_ERROR, &error.to_string()))
}

pub(crate) async fn export_vault(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match state.auth.require_admin(&headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    let object = if body.is_empty() {
        Map::new()
    } else {
        match parse_json_object(&body) {
            Ok(object) => object,
            Err(refusal) => return refusal,
        }
    };
    let workspace_id = object
        .get("workspace_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string);
    if state.auth.effective_require_auth() && workspace_id.is_none() {
        return detail(
            StatusCode::BAD_REQUEST,
            "workspace_id is required to export a vault when sign-in is required.",
        );
    }
    let scope = Scope {
        workspace_id: workspace_id.clone(),
        viewer: state.auth.graph_viewer(&identity.email),
    };
    let db = state.config.graph_db_path();
    match tokio::task::spawn_blocking(move || build_vault(&db, &scope)).await {
        Ok(Ok((bytes, counts))) => {
            emit_portability_audit(
                &state,
                "vault_exported",
                json!({ "counts": counts.to_json(), "workspace_id": workspace_id }),
            );
            zip_response(bytes, counts)
        }
        Ok(Err(message)) => detail(StatusCode::INTERNAL_SERVER_ERROR, &message),
        Err(error) => detail(StatusCode::INTERNAL_SERVER_ERROR, &error.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn graph() -> Connection {
        let conn = Connection::open_in_memory().expect("memory db");
        conn.execute_batch(
            "CREATE TABLE nodes (id TEXT, type TEXT, title TEXT, summary TEXT, metadata_json TEXT, created_at TEXT);
             CREATE TABLE nodes_v2 (id TEXT, workspace_id TEXT, superseded_by TEXT, valid_from TEXT, valid_to TEXT, owner_id TEXT);
             CREATE TABLE edges (from_node TEXT, to_node TEXT, type TEXT);
             CREATE TABLE chunks (id TEXT, source_node TEXT, text TEXT, metadata_json TEXT);
             INSERT INTO nodes VALUES ('a', 'Document', 'A', '', '{}', '1'), ('b', 'Document', 'B', 'bbbbbbbb', '{}', '2'), ('c', 'Document', 'C', 'c', '{}', '3');",
        )
        .expect("schema");
        conn
    }

    #[test]
    fn bounds_truncate_and_omit_honestly() {
        let conn = graph();
        let scope = Scope {
            workspace_id: None,
            viewer: None,
        };
        let limits = VaultLimits {
            notes: 2,
            note_bytes: 4,
            total_bytes: 1,
        };
        let (notes, _, counts) = collect(&conn, &scope, limits).expect("collect");
        assert_eq!(notes.len(), 2);
        assert_eq!(notes[1].title, "C");
        assert_eq!((counts.omitted, counts.truncated), (1, 0));
        let limits = VaultLimits {
            notes: 2,
            note_bytes: 4,
            total_bytes: 64,
        };
        let (notes, _, counts) = collect(&conn, &scope, limits).expect("collect");
        assert_eq!(notes[1].body, "bbbb");
        assert_eq!((counts.omitted, counts.truncated), (1, 1));
    }

    #[test]
    fn overlapping_chunks_are_joined_once() {
        let mut body = String::new();
        merge_chunk(&mut body, "하나 둘 셋", 0);
        merge_chunk(&mut body, "둘 셋 넷", 3);
        merge_chunk(&mut body, "다섯", 0);
        assert_eq!(body, "하나 둘 셋 넷\n\n다섯");
    }
}
