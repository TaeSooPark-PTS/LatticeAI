//! `CommandCenterService.search` — the Cmd+K answer, across three surfaces.
//!
//! ## The knowledge group fills now: a deliberate divergence from the oracle
//!
//! Python's `_search_knowledge` called `SearchService.keyword_search(...)` and
//! then read `payload.get("results")`. That service answers `{"query", "mode",
//! "matches"}` — there is no `results` key and there never has been — so the
//! knowledge group was `[]` on every query, and `search()` dropped it because it
//! only keeps groups whose `items` are non-empty. Cmd+K could never surface a
//! knowledge node; the one third of the feature that gave the palette its reason
//! to exist was dead on arrival. v11.6.0 ported the bug faithfully and disclosed
//! it (§5.2); v11.7.0 fixes it.
//!
//! What is fixed is **only the key**. Everything else is the oracle's code read
//! literally: the same `keyword_search(query, limit=limit, **graph_scope_kwargs)`
//! call, the same `[:limit]` slice on top of a lane that already capped itself,
//! the same four-field projection in the same order
//! (`id` / `_clip(title, 120)` / `_clip(summary, 160)` / `type`), the same
//! `except Exception: return []`, and the same `self._enable_graph` guard that
//! makes a graph-less install answer an empty group rather than an error.
//!
//! Three fixture cases pinned the empty group and no longer can (`search`,
//! `search_conversation_hit`, `search_korean`) — see `tests/command_replay.rs`
//! and the DIVERGENCE FROM ORACLE notes those cases carry in
//! `rust/fixtures/http/memory_brain.json`.

use std::collections::BTreeSet;

use lattice_auth::OrderedMap;
use rusqlite::Connection;
use serde_json::Value;

use super::store::{clip, py_text};
use crate::history::{self, HistoryScope};
use crate::service::{self, Scope};

/// `_SEARCH_HISTORY_LIMIT`.
const SEARCH_HISTORY_LIMIT: i64 = 2000;

/// One `GET /api/command/search` call, after query validation.
pub(crate) struct SearchRequest<'a> {
    /// `str(query or "").strip()`, already applied.
    pub(crate) query: &'a str,
    pub(crate) user_email: &'a str,
    pub(crate) workspace_id: Option<&'a str>,
    /// The Workspace OS state document, already loaded.
    pub(crate) state: &'a Value,
    /// `max(1, min(int(limit or 8), 20))`, already applied.
    pub(crate) limit: i64,
    /// `CommandCenterService._enable_graph` — a graph-less install still
    /// answers, it just answers without the knowledge group.
    pub(crate) enable_graph: bool,
}

/// The three groups, and the total across all of them.
///
/// `total` sums every group including the ones `search()` drops for being
/// empty — which, since an empty group contributes zero, is the same number.
pub(crate) fn groups(conn: &Connection, request: &SearchRequest<'_>) -> (Vec<Value>, i64) {
    let built = [
        ("knowledge", knowledge(conn, request)),
        ("conversation", conversations(conn, request)),
        ("automation", automations(request)),
    ];
    let total: i64 = built.iter().map(|(_, items)| items.len() as i64).sum();
    let kept = built
        .into_iter()
        .filter(|(_, items)| !items.is_empty())
        .map(|(kind, items)| {
            let mut group = OrderedMap::new();
            group.insert("kind", Value::String(kind.to_string()));
            group.insert("items", Value::Array(items));
            serde_json::to_value(&group).unwrap_or(Value::Null)
        })
        .collect();
    (kept, total)
}

/// `_search_knowledge` — the lexical lane, projected down to four fields.
///
/// The oracle read `payload["results"]`; the producer emits `matches`. This
/// reads `matches` (the one divergence, see the module header) and is otherwise
/// the oracle line for line.
fn knowledge(conn: &Connection, request: &SearchRequest<'_>) -> Vec<Value> {
    // `if self._search is None or not self._enable_graph: return []`. The search
    // service is not optional in this port — it is a function in this crate — so
    // only the graph switch survives as a condition.
    if !request.enable_graph {
        return Vec::new();
    }
    // `graph_scope_kwargs(workspace_id)`: a named workspace reads only itself and
    // never the legacy pool; an unscoped caller reads everything it may.
    let scope = Scope {
        allowed_workspaces: request.workspace_id.map(|id| {
            let mut set = BTreeSet::new();
            set.insert(id.to_string());
            set
        }),
        include_legacy_global: request.workspace_id.is_none(),
    };
    // `except Exception: LOGGER.exception(...); return []` — an unreadable graph
    // costs the group, never the query.
    let Ok(payload) = service::keyword_search(conn, request.query, request.limit, &scope) else {
        return Vec::new();
    };
    payload
        .get("matches")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        // `[:limit]`. The lane already caps itself at `limit`, and scoping can
        // only shorten the list, so this slice is the oracle's belt to its own
        // braces — kept because a future lane change must not widen the group.
        .take(request.limit.max(0) as usize)
        .map(|item| {
            let mut hit = OrderedMap::new();
            hit.insert("id", item.get("id").cloned().unwrap_or(Value::Null));
            hit.insert(
                "title",
                Value::String(clip(item.get("title").unwrap_or(&Value::Null), 120)),
            );
            hit.insert(
                "summary",
                Value::String(clip(item.get("summary").unwrap_or(&Value::Null), 160)),
            );
            hit.insert("type", item.get("type").cloned().unwrap_or(Value::Null));
            serde_json::to_value(&hit).unwrap_or(Value::Null)
        })
        .collect()
}

/// `_search_conversations` — newest first, one hit per conversation.
fn conversations(conn: &Connection, request: &SearchRequest<'_>) -> Vec<Value> {
    let needle = request.query.to_lowercase();
    let scope = HistoryScope {
        user_email: Some(request.user_email.to_string()),
        allowed_workspaces: request.workspace_id.map(|id| vec![id.to_string()]),
        include_legacy_global: request.workspace_id.is_none(),
    };
    let Ok(items) = history::history(conn, None, Some(SEARCH_HISTORY_LIMIT), &scope) else {
        // `_history` logs and answers `[]`; the briefing never breaks on a
        // conversation store that cannot be read.
        return Vec::new();
    };
    let mut seen: Vec<String> = Vec::new();
    let mut matches: Vec<Value> = Vec::new();
    for item in items.iter().rev() {
        let content = py_text(item.get("content"));
        if !content.to_lowercase().contains(&needle) {
            continue;
        }
        let conversation_id = py_text(item.get("conversation_id"));
        if !conversation_id.is_empty() {
            if seen.contains(&conversation_id) {
                continue;
            }
            seen.push(conversation_id.clone());
        }
        let mut hit = OrderedMap::new();
        hit.insert("conversation_id", Value::String(conversation_id));
        hit.insert("role", item.get("role").cloned().unwrap_or(Value::Null));
        hit.insert("snippet", Value::String(clip(&Value::String(content), 140)));
        hit.insert("timestamp", Value::String(py_text(item.get("timestamp"))));
        matches.push(serde_json::to_value(&hit).unwrap_or(Value::Null));
        if matches.len() as i64 >= request.limit {
            break;
        }
    }
    matches
}

/// `_search_automations` — a substring scan over installed workflow names.
fn automations(request: &SearchRequest<'_>) -> Vec<Value> {
    let needle = request.query.to_lowercase();
    let mut matches: Vec<Value> = Vec::new();
    for workflow in super::store::workflows(request.state, request.workspace_id) {
        let name = py_text(workflow.get("name"));
        if !name.to_lowercase().contains(&needle) {
            continue;
        }
        let state = workflow
            .get("metadata")
            .and_then(|metadata| metadata.get("automation_state"))
            .and_then(Value::as_str);
        let mut hit = OrderedMap::new();
        hit.insert("id", workflow.get("id").cloned().unwrap_or(Value::Null));
        hit.insert("name", Value::String(clip(&Value::String(name), 120)));
        hit.insert("enabled", Value::Bool(state == Some("enabled")));
        matches.push(serde_json::to_value(&hit).unwrap_or(Value::Null));
        if matches.len() as i64 >= request.limit {
            break;
        }
    }
    matches
}

/// The whole answer body, in Python's key order.
pub(crate) fn body(
    request: &SearchRequest<'_>,
    kept: Vec<Value>,
    total: i64,
    now: &str,
) -> OrderedMap {
    let mut body = OrderedMap::new();
    body.insert("query", Value::String(request.query.to_string()));
    body.insert("groups", Value::Array(kept));
    body.insert("total", Value::from(total));
    body.insert("generated_at", Value::String(now.to_string()));
    body
}

/// The short answer an empty query gets: no `total` key at all.
pub(crate) fn empty_body(now: &str) -> OrderedMap {
    let mut body = OrderedMap::new();
    body.insert("query", Value::String(String::new()));
    body.insert("groups", Value::Array(Vec::new()));
    body.insert("generated_at", Value::String(now.to_string()));
    body
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn state() -> Value {
        json!({"workflows": [
            {"id": "w1", "name": "Daily digest", "metadata": {"automation_state": "enabled"}},
            {"id": "w2", "name": "Weekly DIGEST review", "metadata": {"automation_state": "draft_disabled"}},
            {"id": "w3", "name": "Unrelated"},
        ]})
    }

    fn request<'a>(query: &'a str, doc: &'a Value, limit: i64) -> SearchRequest<'a> {
        SearchRequest {
            query,
            user_email: "",
            workspace_id: None,
            state: doc,
            limit,
            enable_graph: true,
        }
    }

    /// A graph the lexical lane can actually answer from.
    fn graph() -> (tempfile::TempDir, Connection) {
        let dir = tempfile::tempdir().expect("tempdir");
        let conn = Connection::open(dir.path().join("g.sqlite")).expect("open");
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, updated_at TEXT);
             CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, workspace_id TEXT);
             INSERT INTO nodes VALUES
               ('a','Decision','Alpha ranking','  about   ranking  ','{}','2026-01-02T00:00:00'),
               ('b','Concept','Beta ranking',NULL,'{}','2026-01-03T00:00:00'),
               ('c','Concept','Gamma','unrelated','{}','2026-01-01T00:00:00');
             INSERT INTO nodes_v2 VALUES ('a','w1'),('b','w1'),('c','w2');",
        )
        .expect("schema");
        (dir, conn)
    }

    #[test]
    fn the_knowledge_group_carries_the_lanes_matches() {
        let (_dir, conn) = graph();
        let doc = state();
        let hits = knowledge(&conn, &request("ranking", &doc, 8));
        assert_eq!(
            hits.len(),
            2,
            "`matches`, not the oracle's absent `results`"
        );
        let keys: Vec<&str> = hits[0]
            .as_object()
            .expect("object")
            .keys()
            .map(String::as_str)
            .collect();
        assert_eq!(
            keys,
            vec!["id", "title", "summary", "type"],
            "the four fields _search_knowledge projects, in its order"
        );
        assert_eq!(hits[0]["id"], "a");
        assert_eq!(hits[0]["title"], "Alpha ranking");
        assert_eq!(
            hits[0]["summary"], "about ranking",
            "the lane cleaned the whitespace before _clip saw it"
        );
        assert_eq!(hits[0]["type"], "Decision");
        assert_eq!(hits[1]["id"], "b");
        assert_eq!(
            hits[1]["summary"], "",
            "a NULL summary is \"\", never null: str(None or \"\")"
        );
        // The group is no longer dropped, and it counts toward `total`.
        let (kept, total) = groups(&conn, &request("ranking", &doc, 8));
        assert_eq!(kept[0]["kind"], "knowledge");
        assert_eq!(kept[0]["items"].as_array().expect("items").len(), 2);
        assert_eq!(total, 2, "no conversation table and no matching workflow");
    }

    #[test]
    fn the_knowledge_group_clips_titles_at_120_and_summaries_at_160_characters() {
        let dir = tempfile::tempdir().expect("tempdir");
        let conn = Connection::open(dir.path().join("g.sqlite")).expect("open");
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, updated_at TEXT);
             CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, workspace_id TEXT);",
        )
        .expect("schema");
        conn.execute(
            "INSERT INTO nodes VALUES ('long','Document',?,?,'{\"topic\":\"ranking\"}',
                                       '2026-01-05T00:00:00')",
            rusqlite::params!["가".repeat(150), "나".repeat(400)],
        )
        .expect("row");
        conn.execute("INSERT INTO nodes_v2 VALUES ('long','w1')", [])
            .expect("v2");
        let doc = state();
        let hits = knowledge(&conn, &request("ranking", &doc, 8));
        assert_eq!(hits.len(), 1, "matched through metadata_json");
        assert_eq!(
            hits[0]["title"].as_str().expect("title").chars().count(),
            120,
            "characters, not bytes"
        );
        assert_eq!(
            hits[0]["summary"]
                .as_str()
                .expect("summary")
                .chars()
                .count(),
            160
        );
    }

    #[test]
    fn the_knowledge_group_honours_the_limit_and_the_workspace() {
        let (_dir, conn) = graph();
        let doc = state();
        assert_eq!(knowledge(&conn, &request("ranking", &doc, 1)).len(), 1);
        let scoped = SearchRequest {
            workspace_id: Some("w2"),
            ..request("ranking", &doc, 8)
        };
        assert!(
            knowledge(&conn, &scoped).is_empty(),
            "w2 owns only the node that does not match"
        );
        let other = SearchRequest {
            workspace_id: Some("w1"),
            ..request("ranking", &doc, 8)
        };
        assert_eq!(knowledge(&conn, &other).len(), 2);
    }

    #[test]
    fn personal_command_search_matches_null_workspace_nodes() {
        let dir = tempfile::tempdir().expect("tempdir");
        let conn = Connection::open(dir.path().join("g.sqlite")).expect("open");
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, updated_at TEXT);
             CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, workspace_id TEXT);
             INSERT INTO nodes VALUES
               ('note','Document','Phoenix launch notes','the phoenix plan','{}','2026-08-11'),
               ('team','Document','Acme ranking','secret','{}','2026-08-11');
             INSERT INTO nodes_v2 VALUES ('note', NULL), ('team', 'acme');
             CREATE TABLE conversation_messages(
               id INTEGER PRIMARY KEY, conversation_id TEXT, role TEXT, content TEXT,
               user_email TEXT, timestamp TEXT, workspace_id TEXT, metadata_json TEXT,
               user_nickname TEXT, source TEXT, organization_id TEXT);
             INSERT INTO conversation_messages(conversation_id, role, content, user_email,
               timestamp, workspace_id, metadata_json) VALUES
               ('c1','user','phoenix recap','a@x','2026-08-11T10:00:00',NULL,'{}');",
        )
        .expect("schema");
        let doc = state();
        let personal = SearchRequest {
            workspace_id: Some("personal"),
            user_email: "a@x",
            ..request("phoenix", &doc, 8)
        };
        let (groups, total) = groups(&conn, &personal);
        assert!(
            total > 0,
            "a populated personal workspace must not report 0"
        );
        let knowledge_hits = groups
            .iter()
            .find(|group| group["kind"] == "knowledge")
            .and_then(|group| group["items"].as_array())
            .cloned()
            .unwrap_or_default();
        assert_eq!(knowledge_hits.len(), 1);
        assert_eq!(knowledge_hits[0]["id"], "note");
        let named = SearchRequest {
            workspace_id: Some("acme"),
            user_email: "a@x",
            ..request("phoenix", &doc, 8)
        };
        assert!(
            knowledge(&conn, &named).is_empty(),
            "acme must not see the unstamped node"
        );
        assert_eq!(
            conversations(&conn, &personal).len(),
            1,
            "personal sees the unstamped chat turn"
        );
        assert!(conversations(&conn, &named).is_empty());
    }

    #[test]
    fn a_graph_less_install_and_an_unreadable_graph_both_cost_only_the_group() {
        let (_dir, conn) = graph();
        let doc = state();
        let off = SearchRequest {
            enable_graph: false,
            ..request("ranking", &doc, 8)
        };
        assert!(knowledge(&conn, &off).is_empty());
        assert_eq!(
            groups(&conn, &off).0.len(),
            0,
            "search() drops a group whose items are empty"
        );
        // No `nodes` table at all: the lane raises and `_search_knowledge`
        // swallows it, so the query still answers.
        let broken = Connection::open_in_memory().expect("memory");
        assert!(knowledge(&broken, &request("ranking", &doc, 8)).is_empty());
        let (kept, total) = groups(&broken, &request("digest", &doc, 8));
        assert_eq!(kept.len(), 1, "the automation group still answers");
        assert_eq!(kept[0]["kind"], "automation");
        assert_eq!(total, 2);
    }

    #[test]
    fn the_automation_scan_is_case_insensitive_and_respects_the_limit() {
        let doc = state();
        let hits = automations(&request("digest", &doc, 8));
        assert_eq!(hits.len(), 2);
        // `list_workflows` reverses, so w2 comes first.
        assert_eq!(hits[0]["id"], "w2");
        assert_eq!(hits[0]["enabled"], false);
        assert_eq!(hits[1]["id"], "w1");
        assert_eq!(hits[1]["enabled"], true);
        assert_eq!(automations(&request("digest", &doc, 1)).len(), 1);
    }

    #[test]
    fn an_empty_query_matches_every_name_because_python_substrings_do() {
        let doc = state();
        assert_eq!(
            automations(&request("", &doc, 8)).len(),
            3,
            "\"\" in name is True; the caller is what stops an empty query earlier"
        );
    }

    #[test]
    fn the_empty_query_body_carries_no_total() {
        let body = empty_body("2026-08-14T00:00:00");
        assert_eq!(body.get("query"), Some(&json!("")));
        assert_eq!(body.get("groups"), Some(&json!([])));
        assert!(
            !body.contains_key("total"),
            "search() returns three keys here"
        );
        let keys: Vec<&str> = body.iter().map(|(key, _)| key).collect();
        assert_eq!(keys, vec!["query", "groups", "generated_at"]);
    }

    #[test]
    fn a_full_body_keeps_pythons_key_order() {
        let doc = state();
        let body = body(
            &request("digest", &doc, 8),
            Vec::new(),
            2,
            "2026-08-14T00:00:00",
        );
        let keys: Vec<&str> = body.iter().map(|(key, _)| key).collect();
        assert_eq!(keys, vec!["query", "groups", "total", "generated_at"]);
    }
}
