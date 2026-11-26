mod common;

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use common::{client, FixedProvider, TestGateway};
use lattice_auth::password::hash_password;
use lattice_auth::{AuthConfig, Users};
use lattice_core::db::RuntimeConfig;
use lattice_core::graph_write::types::{
    ChunkPiece, IngestContentRequest, IngestMessageRequest, SemanticSpec,
};
use lattice_host::gateway::onedoor::OneDoorState;
use lattice_host::gateway::posture::Posture;
use lattice_host::gateway::GatewayState;
use serde_json::{json, Map, Value};

const ALICE: &str = "alice@acme.test";
const BOB: &str = "bob@acme.test";
const CHAT_SECRET: &str = "zebraquartz";
const CHAT_CANARY: &str = "moonstonefjord";
const DOC_SECRET: &str = "okapimarble";

fn seed_users(data_dir: &std::path::Path) {
    let mut users = Users::new();
    for email in [ALICE, BOB] {
        let mut rec = Map::new();
        rec.insert(
            "password".into(),
            json!(hash_password("Pass1234").expect("hash password")),
        );
        rec.insert("role".into(), json!("user"));
        rec.insert("tenant_id".into(), json!("acme"));
        rec.insert("disabled".into(), json!(false));
        rec.insert("name".into(), json!(email));
        users.insert(email, rec);
    }
    lattice_auth::UserStore::new(data_dir).save(&users);
}

fn alice_turn(role: &str, content: &str) -> IngestMessageRequest {
    IngestMessageRequest {
        role: role.into(),
        content: content.into(),
        user_email: Some(ALICE.into()),
        source: Some("web".into()),
        conversation_id: Some("alice-conversation".into()),
        workspace_id: Some("personal".into()),
        chunks: vec![ChunkPiece {
            text: content.into(),
            ..Default::default()
        }],
        semantic: vec![SemanticSpec {
            item_type: "Decision".into(),
            title: format!("{CHAT_SECRET} {CHAT_CANARY} decision"),
            summary: content.into(),
            raw: Default::default(),
        }],
        ..Default::default()
    }
}

struct Probe {
    label: String,
    status: u16,
    body: String,
}

async fn probe(
    gateway: &TestGateway,
    token: &str,
    method: &str,
    path: &str,
    body: Option<Value>,
) -> Probe {
    let url = gateway.url(path);
    let request = match method {
        "POST" => client()
            .post(&url)
            .header("content-type", "application/json")
            .body(serde_json::to_vec(&body.unwrap_or_else(|| json!({}))).expect("json")),
        _ => client().get(&url),
    };
    let response = request.bearer_auth(token).send().await.expect("send");
    let status = response.status().as_u16();
    let body = response.text().await.unwrap_or_default();
    Probe {
        label: format!("{method} {path}"),
        status,
        body,
    }
}

fn read_paths(message_id: &str, chat_id: &str) -> Vec<(&'static str, String, Option<Value>)> {
    let q = CHAT_SECRET;
    vec![
        ("GET", format!("/api/search/hybrid?q={q}"), None),
        (
            "GET",
            format!("/api/search/hybrid?q={q}&mode=graphrag"),
            None,
        ),
        (
            "POST",
            "/api/search/hybrid".into(),
            Some(json!({"query": q, "q": q})),
        ),
        ("GET", format!("/api/search/keyword?q={q}"), None),
        ("GET", format!("/api/search/vector?q={q}&min_score=0"), None),
        (
            "POST",
            "/api/search/graph".into(),
            Some(json!({"query": q, "q": q})),
        ),
        ("GET", "/api/graph".into(), None),
        ("GET", format!("/api/graph/node?node_id={message_id}"), None),
        (
            "GET",
            format!("/api/graph/relationship?node_id={chat_id}"),
            None,
        ),
        ("GET", format!("/api/graph/relationship?q={q}"), None),
        ("GET", "/knowledge-graph/graph".into(), None),
        (
            "GET",
            format!("/knowledge-graph/search?q={q}&query={q}"),
            None,
        ),
        (
            "GET",
            format!("/knowledge-graph/context?q={q}&query={q}"),
            None,
        ),
        ("GET", format!("/knowledge-graph/neighbors/{chat_id}"), None),
        (
            "GET",
            format!("/knowledge-graph/neighbors/{message_id}"),
            None,
        ),
        (
            "POST",
            "/api/memory/recall".into(),
            Some(json!({"query": q, "q": q})),
        ),
        ("GET", format!("/api/memory/brain-brief?query={q}"), None),
        ("GET", format!("/api/memory/brain-proof?query={q}"), None),
        ("GET", "/api/memory/inspect".into(), None),
        ("GET", format!("/api/command/search?q={q}"), None),
        ("GET", "/api/command/briefing".into(), None),
        ("GET", "/api/brain/clusters".into(), None),
        ("GET", "/api/brain/gaps".into(), None),
        ("GET", "/api/brain/insights".into(), None),
        ("GET", "/api/brain/duplicates".into(), None),
        ("GET", "/api/brain/garden".into(), None),
        ("GET", "/api/brain/contradictions".into(), None),
        ("GET", "/api/brain/quality-report".into(), None),
        ("GET", "/api/brain/importance".into(), None),
        ("GET", "/api/brain/proactive-brief".into(), None),
        (
            "GET",
            format!("/api/brain/entities/{message_id}/timeline"),
            None,
        ),
        ("GET", "/api/chronicle/overview".into(), None),
        (
            "GET",
            "/api/chronicle/as-of?ts=2999-01-01T00:00:00".into(),
            None,
        ),
        (
            "POST",
            "/api/evidence/actions".into(),
            Some(json!({"node_id": message_id, "query": q})),
        ),
    ]
}

#[tokio::test]
async fn a_signed_in_user_cannot_read_another_users_conversation_from_the_shared_graph() {
    let scratch = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join("conversation_privacy_e2e")
        .join("two_users");
    let _ = std::fs::remove_dir_all(&scratch);
    let data_dir = scratch.join("data");
    let agent_root = scratch.join("agent_workspace");
    std::fs::create_dir_all(&data_dir).expect("data_dir");
    std::fs::create_dir_all(&agent_root).expect("agent_root");
    seed_users(&data_dir);

    let worker_origin = "http://127.0.0.1:1".to_string();
    let config = RuntimeConfig::resolve(
        Some(&data_dir.to_string_lossy()),
        None,
        Some(&worker_origin),
        None,
    );
    let mut auth_env = HashMap::new();
    auth_env.insert("LATTICEAI_REQUIRE_AUTH".into(), "true".into());
    auth_env.insert(
        "LATTICEAI_DATA_DIR".into(),
        data_dir.to_string_lossy().into_owned(),
    );
    let mut auth_cfg = AuthConfig::from_map(&auth_env, None);
    auth_cfg.data_dir = data_dir.clone();
    let loop_config = lattice_agent::surface::looproutes::LoopConfig {
        worker_origin: worker_origin.clone(),
        runs_dir: scratch.join("rust_agent_runs"),
        client: Some(client()),
        proposals: None,
        hooks: None,
        external: None,
    };
    let product = OneDoorState::open_with_auth(
        config,
        &worker_origin,
        client(),
        &agent_root,
        loop_config,
        auth_cfg,
    )
    .expect("product");

    let question = format!("my private {CHAT_SECRET} {CHAT_CANARY} diagnosis question");
    let message_id = product
        .graph
        .ingest_message(&alice_turn("user", &question))
        .expect("alice turn")
        .node_id;
    let answer = product
        .graph
        .ingest_message(&alice_turn(
            "assistant",
            &format!("{CHAT_SECRET} {CHAT_CANARY} answer for alice"),
        ))
        .expect("alice answer")
        .node_id;
    let document = product
        .graph
        .ingest_content(&IngestContentRequest {
            source_type: "upload".into(),
            title: format!("{DOC_SECRET} handbook"),
            text: format!("{DOC_SECRET} team handbook shared in personal"),
            owner: Some(ALICE.into()),
            workspace_id: Some("personal".into()),
            chunks: vec![ChunkPiece {
                text: format!("{DOC_SECRET} team handbook shared in personal"),
                ..Default::default()
            }],
            ..Default::default()
        })
        .expect("document")
        .node_id;
    for node in [&message_id, &answer, &document] {
        let _ = product.graph.write_vectors(node);
    }
    let chat_id: String = {
        let conn = lattice_core::db::open_read_only(&data_dir.join("knowledge_graph.sqlite"))
            .expect("read graph");
        conn.query_row(
            "SELECT id FROM nodes WHERE type = 'Chat' LIMIT 1",
            [],
            |row| row.get(0),
        )
        .expect("chat node")
    };

    let alice_tok = product.auth.sessions().create("user:alice", Some(ALICE));
    let bob_tok = product.auth.sessions().create("user:bob", Some(BOB));

    let gw_state = GatewayState::new(Arc::new(FixedProvider::new(worker_origin.clone(), 1)))
        .expect("gateway")
        .with_db_path(data_dir.join("knowledge_graph.sqlite"))
        .with_agent_root(&agent_root)
        .with_agent_runs_dir(scratch.join("rust_agent_runs"))
        .with_pinned_posture(Posture::Open)
        .with_product(Arc::new(product));
    let gateway = TestGateway::start_with_state(gw_state).await;

    let alice_keyword = probe(
        &gateway,
        &alice_tok,
        "GET",
        &format!("/api/search/keyword?q={CHAT_SECRET}"),
        None,
    )
    .await;
    assert_eq!(alice_keyword.status, 200, "{}", alice_keyword.body);
    assert!(
        alice_keyword.body.contains(&message_id),
        "the owner still finds her own conversation: {}",
        alice_keyword.body
    );
    let alice_hybrid = probe(
        &gateway,
        &alice_tok,
        "GET",
        &format!("/api/search/hybrid?q={CHAT_SECRET}"),
        None,
    )
    .await;
    assert!(
        alice_hybrid.body.contains(CHAT_CANARY),
        "{}",
        alice_hybrid.body
    );

    let bob_document = probe(
        &gateway,
        &bob_tok,
        "GET",
        &format!("/api/search/keyword?q={DOC_SECRET}"),
        None,
    )
    .await;
    assert_eq!(bob_document.status, 200, "{}", bob_document.body);
    assert!(
        bob_document.body.contains(&document),
        "documents in personal stay workspace scoped: {}",
        bob_document.body
    );

    let mut leaks: Vec<String> = Vec::new();
    let mut answered = 0;
    for (method, path, body) in read_paths(&message_id, &chat_id) {
        let seen = probe(&gateway, &bob_tok, method, &path, body).await;
        if seen.status == 200 {
            answered += 1;
        }
        let lowered = seen.body.to_lowercase();
        let leaked_content = lowered.contains(CHAT_CANARY)
            || seen.body.contains(&answer)
            || (seen.status == 200 && seen.body.contains(&message_id));
        if leaked_content {
            leaks.push(format!(
                "{} [{}]: {}",
                seen.label,
                seen.status,
                preview(&seen.body)
            ));
        }
    }
    assert!(
        answered >= 20,
        "most read paths must answer bob, got {answered}"
    );
    assert!(
        leaks.is_empty(),
        "bob read alice's conversation through {} path(s):\n{}",
        leaks.len(),
        leaks.join("\n")
    );
    gateway.stop().await;
    let _ = std::fs::remove_dir_all(&scratch);
}

fn preview(body: &str) -> String {
    body.chars().take(240).collect()
}
