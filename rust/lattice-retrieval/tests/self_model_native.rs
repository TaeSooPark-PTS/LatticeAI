//! The Self-Model's four write routes and the contradiction stamps, natively.
//!
//! Until v11.7.0 all five posted to `POST /worker/graph/mutate` — a door the
//! Python worker stopped serving in v11.6.0. On a live install
//! `POST /api/memory/self-model` answered 404, `…/propose` proposed nothing,
//! `…/apply` applied nothing, `DELETE …/{node_id}` deleted nothing, and
//! `resolve_contradiction` reported `stamps: []` on every resolution. The
//! fixture replay stayed green throughout, because the harness's stand-in
//! worker answered on the seam's behalf.
//!
//! `memory_api_replay` pins the recorded *bodies* (and asserts the seam is
//! never called). This file pins what the recorded bodies cannot: that the
//! rows land in `knowledge_graph.sqlite` and `workspace_os.json`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod common;

use common::brain::Install;
use serde_json::{json, Value};

async fn post(install: &Install, path: &str, body: Value) -> (u16, Value) {
    let response = reqwest::Client::new()
        .post(format!("{}{path}", install.origin))
        .header("cookie", format!("session_token={}", install.token))
        .header("content-type", "application/json")
        .body(body.to_string())
        .send()
        .await
        .expect("post");
    let status = response.status().as_u16();
    let text = response.text().await.expect("text");
    (status, serde_json::from_str(&text).unwrap_or(json!(text)))
}

async fn get(install: &Install, path: &str) -> (u16, Value) {
    let response = reqwest::Client::new()
        .get(format!("{}{path}", install.origin))
        .header("cookie", format!("session_token={}", install.token))
        .send()
        .await
        .expect("get");
    let status = response.status().as_u16();
    let text = response.text().await.expect("text");
    (status, serde_json::from_str(&text).unwrap_or(json!(text)))
}

fn graph(install: &Install) -> rusqlite::Connection {
    rusqlite::Connection::open(install.data_dir().join("knowledge_graph.sqlite")).expect("graph")
}

fn node(conn: &rusqlite::Connection, id: &str) -> Option<(String, String, String)> {
    conn.query_row(
        "SELECT type, title, metadata_json FROM nodes WHERE id=?",
        rusqlite::params![id],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
    )
    .ok()
}

/// Change one stored review item's `kind`, through the document's real medium.
///
/// Rewriting `workspace_os.json` alone would be ignored: `wsos::load` reads the
/// `workspace_os_state` SQLite row **first** and only falls back to the file.
/// That asymmetry is exactly why the standalone `JsonProposalStore` needed a
/// document owner (§F-G §3), and a test that forgot it would be testing a file
/// nothing reads.
fn retype_review_item(install: &Install, item_id: &str, kind: &str) {
    let conn = rusqlite::Connection::open(install.data_dir().join("knowledge_graph.sqlite"))
        .expect("store");
    let stored: String = conn
        .query_row(
            "SELECT state_json FROM workspace_os_state WHERE id='current'",
            [],
            |row| row.get(0),
        )
        .expect("workspace_os_state row");
    let mut document: Value = serde_json::from_str(&stored).expect("json");
    let mut hit = 0;
    for row in document["review_items"].as_array_mut().expect("rows") {
        if row["id"] == json!(item_id) {
            row["kind"] = json!(kind);
            hit += 1;
        }
    }
    assert_eq!(hit, 1, "{item_id} is not stored");
    conn.execute(
        "UPDATE workspace_os_state SET state_json=? WHERE id='current'",
        rusqlite::params![document.to_string()],
    )
    .expect("update");
    std::fs::write(
        install.data_dir().join("workspace_os.json"),
        document.to_string(),
    )
    .expect("mirror");
}

#[tokio::test]
async fn a_users_own_edit_writes_the_root_the_fact_and_the_edge() {
    let install = Install::start().await;
    let (status, fact) = post(
        &install,
        "/api/memory/self-model",
        json!({"kind": "habit", "text": "매일 아침 회의록을 정리합니다."}),
    )
    .await;
    assert_eq!(status, 200, "{fact}");
    let node_id = fact["id"].as_str().expect("id").to_string();
    assert_eq!(fact["type"], json!("Habit"));
    assert_eq!(fact["origin"], json!("user"));
    assert_eq!(fact["signal"], json!("user_edit"));
    // The trailing full stop is normalised away before the id is derived, so
    // the same statement typed twice lands on one node rather than two.
    assert_eq!(fact["text"], json!("매일 아침 회의록을 정리합니다"));

    let conn = graph(&install);
    let (node_type, title, metadata) = node(&conn, &node_id).expect("the fact node exists");
    assert_eq!(node_type, "Habit");
    assert_eq!(title, "매일 아침 회의록을 정리합니다");
    let metadata: Value = serde_json::from_str(&metadata).expect("metadata");
    assert_eq!(metadata["self_model"], json!(true));
    assert_eq!(metadata["self_model_kind"], json!("habit"));
    assert_eq!(metadata["confidence"], json!(1.0));
    assert!(
        node(&conn, "self:root").is_some(),
        "the root is written too"
    );
    let edges: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM edges WHERE from_node=? AND to_node='self:root' \
             AND type='PART_OF'",
            rusqlite::params![node_id],
            |row| row.get(0),
        )
        .expect("edges");
    assert_eq!(edges, 1, "one PART_OF edge to the root");

    // …and the profile route reads back what was written.
    let (status, profile) = get(&install, "/api/memory/self-model").await;
    assert_eq!(status, 200);
    let ids: Vec<&str> = profile["facts"]
        .as_array()
        .expect("facts")
        .iter()
        .map(|fact| fact["id"].as_str().unwrap_or_default())
        .collect();
    assert!(ids.contains(&node_id.as_str()), "{ids:?}");
    assert_eq!(profile["counts"]["habit"], json!(1));

    // Writing it again is an upsert, not a second fact.
    let (status, again) = post(
        &install,
        "/api/memory/self-model",
        json!({"kind": "habit", "text": "매일 아침 회의록을 정리합니다"}),
    )
    .await;
    assert_eq!(status, 200);
    assert_eq!(again["id"], fact["id"]);
    let (_, profile) = get(&install, "/api/memory/self-model").await;
    assert_eq!(profile["count"], json!(1), "an upsert is not an append");
}

#[tokio::test]
async fn a_proposal_writes_nothing_until_it_is_applied() {
    let install = Install::start().await;
    let (status, proposed) = post(
        &install,
        "/api/memory/self-model/propose",
        json!({"text": "저는 커피를 좋아합니다.", "source": "chat", "max_proposals": 3}),
    )
    .await;
    assert_eq!(status, 200, "{proposed}");
    assert_eq!(proposed["candidate_count"], json!(1));
    assert_eq!(proposed["proposed_count"], json!(1));
    let item = &proposed["proposed"][0];
    let item_id = item["id"].as_str().expect("item id").to_string();
    let fact_id = item["payload"]["fact"]["id"]
        .as_str()
        .expect("fact id")
        .to_string();
    assert_eq!(item["kind"], json!("self_model_fact"));
    assert_eq!(item["source"], json!("kg_change_digest"));
    assert_eq!(item["status"], json!("pending"));
    assert_eq!(item["payload"]["node_type"], json!("Preference"));

    // **Extraction never writes.** The candidate is a review item and nothing
    // else, which is the whole governance rule for this subgraph.
    assert!(
        node(&graph(&install), &fact_id).is_none(),
        "a proposal must not write the node"
    );
    let (_, profile) = get(&install, "/api/memory/self-model").await;
    assert_eq!(profile["count"], json!(0));

    // The same text again proposes nothing: the subject is already open.
    let (_, again) = post(
        &install,
        "/api/memory/self-model/propose",
        json!({"text": "저는 커피를 좋아합니다.", "source": "chat"}),
    )
    .await;
    assert_eq!(again["proposed_count"], json!(0));
    assert_eq!(again["suppressed"], json!(1));

    // Applying is the single door from a proposal to a node.
    let (status, applied) = post(
        &install,
        "/api/memory/self-model/apply",
        json!({"item_id": item_id}),
    )
    .await;
    assert_eq!(status, 200, "{applied}");
    assert_eq!(applied["status"], json!("approved"));
    assert_eq!(applied["fact"]["id"], json!(fact_id));
    assert_eq!(applied["fact"]["origin"], json!("proposal"));
    let (node_type, _, metadata) = node(&graph(&install), &fact_id).expect("the node exists now");
    assert_eq!(node_type, "Preference");
    let metadata: Value = serde_json::from_str(&metadata).expect("metadata");
    assert_eq!(
        metadata["review_item_id"],
        json!(item_id),
        "the node remembers which decision created it"
    );
    assert_eq!(metadata["origin"], json!("proposal"));

    // The review item is approved in the shared document, not just in memory.
    let document: Value = serde_json::from_str(
        &std::fs::read_to_string(install.data_dir().join("workspace_os.json")).expect("read"),
    )
    .expect("json");
    let stored = document["review_items"]
        .as_array()
        .expect("review_items")
        .iter()
        .find(|row| row["id"] == json!(item_id))
        .expect("the item");
    assert_eq!(stored["status"], json!("approved"));

    // …and now that the fact is known, it is never proposed again.
    let (_, third) = post(
        &install,
        "/api/memory/self-model/propose",
        json!({"text": "저는 커피를 좋아합니다.", "source": "chat"}),
    )
    .await;
    assert_eq!(third["already_known"], json!(1));
    assert_eq!(third["proposed_count"], json!(0));
}

#[tokio::test]
async fn applying_something_that_is_not_a_self_model_proposal_refuses() {
    let install = Install::start().await;
    // An unknown id is a 404 about the *item*.
    let (status, body) = post(
        &install,
        "/api/memory/self-model/apply",
        json!({"item_id": "review-nope"}),
    )
    .await;
    assert_eq!(status, 404, "{body}");

    // A real review item of another kind is a 400 about the *proposal*.
    let (_, proposed) = post(
        &install,
        "/api/memory/self-model/propose",
        json!({"text": "I decided to ship on Friday.", "source": "chat"}),
    )
    .await;
    let item_id = proposed["proposed"][0]["id"]
        .as_str()
        .expect("item id")
        .to_string();
    retype_review_item(&install, &item_id, "file_update");
    let (status, body) = post(
        &install,
        "/api/memory/self-model/apply",
        json!({"item_id": item_id}),
    )
    .await;
    assert_eq!(status, 400, "{body}");
}

#[tokio::test]
async fn deleting_a_fact_removes_the_row_and_refuses_anything_else() {
    let install = Install::start().await;
    let (_, fact) = post(
        &install,
        "/api/memory/self-model",
        json!({"kind": "trait", "text": "저는 개발자입니다"}),
    )
    .await;
    let node_id = fact["id"].as_str().expect("id").to_string();
    assert!(node(&graph(&install), &node_id).is_some());

    let client = reqwest::Client::new();
    let answer = client
        .delete(format!(
            "{}/api/memory/self-model/{node_id}",
            install.origin
        ))
        .header("cookie", format!("session_token={}", install.token))
        .send()
        .await
        .expect("delete");
    assert_eq!(answer.status().as_u16(), 200);
    assert!(
        node(&graph(&install), &node_id).is_none(),
        "the row is gone from `nodes`"
    );
    let v2: i64 = graph(&install)
        .query_row(
            "SELECT COUNT(*) FROM nodes_v2 WHERE id=?",
            rusqlite::params![node_id],
            |row| row.get(0),
        )
        .expect("count");
    assert_eq!(v2, 0, "and from its v2 projection");

    // Deleting it twice is a 404 about the fact…
    let again = client
        .delete(format!(
            "{}/api/memory/self-model/{node_id}",
            install.origin
        ))
        .header("cookie", format!("session_token={}", install.token))
        .send()
        .await
        .expect("delete");
    assert_eq!(again.status().as_u16(), 404);

    // …and a node that is not a Self-Model node is refused before the store is
    // touched, so this route can never delete an ordinary memory.
    let stray = client
        .delete(format!(
            "{}/api/memory/self-model/doc:whatever",
            install.origin
        ))
        .header("cookie", format!("session_token={}", install.token))
        .send()
        .await
        .expect("delete");
    assert_eq!(stray.status().as_u16(), 400);
}

/// Resolving a contradiction stamps the graph, and the stamps are real.
///
/// `resolve_contradiction` has reported `stamps: []` on every install since
/// v11.6.0 — it posted `stamp_contradiction` to the retired seam and swallowed
/// the failure (`Err(_) => Value::Array(Vec::new())`), so the Review Center
/// said "applied" and the two memories kept claiming to be true at once.
#[tokio::test]
async fn resolving_a_contradiction_writes_the_validity_window() {
    let install = Install::start().await;
    // Two memories that disagree, and a contradiction proposal over them. The
    // synthesis pipeline raises these itself; seeding one directly keeps this
    // test about the *stamp*, which is the half that was broken.
    let conn = graph(&install);
    for (id, title) in [
        ("mem:older", "알파 퓨전 유지"),
        ("mem:newer", "알파 퓨전 제거"),
    ] {
        conn.execute(
            "INSERT OR REPLACE INTO nodes(id, type, title, summary, metadata_json, raw_json, \
             created_at, updated_at) VALUES (?, 'Memory', ?, '', '{}', '{}', ?, ?)",
            rusqlite::params![id, title, "2026-08-14T12:00:00", "2026-08-14T12:00:00"],
        )
        .expect("seed node");
        conn.execute(
            "INSERT OR REPLACE INTO nodes_v2(id, type, legacy_type, label, summary, attrs, \
             workspace_id, visibility, created_at, updated_at, importance_score) \
             VALUES (?, 'MEMORY', 'Memory', ?, '', '{}', 'personal', 'private', ?, ?, 0.0)",
            rusqlite::params![id, title, "2026-08-14T12:00:00", "2026-08-14T12:00:00"],
        )
        .expect("seed v2 node");
    }
    let item_id = seed_contradiction(&install, "mem:older", "mem:newer");

    let (status, resolved) = post(
        &install,
        "/api/brain/contradictions/resolve",
        json!({"item_id": item_id, "resolution": "replace"}),
    )
    .await;
    assert_eq!(status, 200, "{resolved}");
    assert_eq!(resolved["status"], json!("approved"));
    let stamps = resolved["stamps"].as_array().expect("stamps");
    assert_eq!(stamps.len(), 2, "replace stamps both sides: {stamps:?}");
    // Python's key order: `node_id`, the fields this arm supplied, `updated`.
    assert_eq!(
        stamps[0],
        json!({"node_id": "mem:older", "valid_to": "2026-08-14T12:00:00",
               "superseded_by": "mem:newer", "updated": true})
    );
    assert_eq!(
        stamps[1],
        json!({"node_id": "mem:newer", "valid_from": "2026-08-14T12:00:00",
               "updated": true})
    );

    let conn = graph(&install);
    let read = |id: &str| -> (Option<String>, Option<String>, Option<String>) {
        conn.query_row(
            "SELECT valid_from, valid_to, superseded_by FROM nodes_v2 WHERE id=?",
            rusqlite::params![id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )
        .expect("row")
    };
    let (from, to, superseded) = read("mem:older");
    assert_eq!(to.as_deref(), Some("2026-08-14T12:00:00"));
    assert_eq!(superseded.as_deref(), Some("mem:newer"));
    assert!(
        from.is_none(),
        "an unsupplied field is left alone, not cleared"
    );
    let (from, to, superseded) = read("mem:newer");
    assert_eq!(from.as_deref(), Some("2026-08-14T12:00:00"));
    assert!(to.is_none() && superseded.is_none());

    // The item is approved once; resolving it again is a 400 about the kind.
    let (status, _) = post(
        &install,
        "/api/brain/contradictions/resolve",
        json!({"item_id": "review-nope", "resolution": "replace"}),
    )
    .await;
    assert_eq!(status, 404, "an unknown item is a 404");
}

/// Append one contradiction proposal, through the medium `wsos::load` reads.
fn seed_contradiction(install: &Install, older: &str, newer: &str) -> String {
    let conn = rusqlite::Connection::open(install.data_dir().join("knowledge_graph.sqlite"))
        .expect("store");
    let stored: String = conn
        .query_row(
            "SELECT state_json FROM workspace_os_state WHERE id='current'",
            [],
            |row| row.get(0),
        )
        .unwrap_or_else(|_| json!({"review_items": []}).to_string());
    let mut document: Value = serde_json::from_str(&stored).expect("json");
    let item_id = "review-contradiction".to_string();
    let item = json!({
        "id": item_id,
        "status": "pending",
        "title": "모순된 기억",
        "summary": "s",
        "source": "kg_change_digest",
        "kind": "contradiction",
        "payload": {"older": {"id": older}, "newer": {"id": newer}},
        "provenance": {"pipeline": "brain-synthesis"},
        "effective_status": "pending",
        "snoozed_until": null,
        "user_email": "owner@fixture.local",
        "workspace_id": "personal",
        "created_at": "2026-08-14T12:00:00",
        "updated_at": "2026-08-14T12:00:00",
    });
    document
        .as_object_mut()
        .expect("object")
        .entry("review_items")
        .or_insert_with(|| json!([]))
        .as_array_mut()
        .expect("rows")
        .push(item);
    conn.execute(
        "INSERT OR REPLACE INTO workspace_os_state(id, state_json, updated_at) \
         VALUES('current', ?, ?)",
        rusqlite::params![document.to_string(), "2026-08-14T12:00:00"],
    )
    .expect("write row");
    std::fs::write(
        install.data_dir().join("workspace_os.json"),
        document.to_string(),
    )
    .expect("mirror");
    item_id
}
