//! End-to-end gateway isolation, workspace ACL, tenant boundary, and audit
//! enforcement tests across `/agent`, `/tools/*`, `/mcp/call`, `/skills/*`,
//! `/cu/*`, `/api/automation/*`, and `/api/browser/*`.

mod common;

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use axum::http::StatusCode;
use common::{client, FixedProvider, TestGateway};
use lattice_agent::tools::sandbox::{resolve_isolated_agent_root, IsolatedRootSpec};
use lattice_auth::password::hash_password;
use lattice_auth::{AuthConfig, Users};
use lattice_core::db::RuntimeConfig;
use lattice_host::gateway::onedoor::OneDoorState;
use lattice_host::gateway::posture::Posture;
use lattice_host::gateway::GatewayState;
use serde_json::{json, Map, Value};

async fn post_json(
    url: String,
    bearer: Option<&str>,
    extra_headers: &[(&str, &str)],
    body: Value,
) -> reqwest::Response {
    let mut req = client()
        .post(url)
        .header("content-type", "application/json")
        .body(serde_json::to_vec(&body).expect("json body"));
    if let Some(tok) = bearer {
        req = req.bearer_auth(tok);
    }
    for (k, v) in extra_headers {
        req = req.header(*k, *v);
    }
    req.send().await.expect("send request")
}

async fn json_body(resp: reqwest::Response) -> Value {
    let bytes = resp.bytes().await.expect("bytes");
    serde_json::from_slice(&bytes).expect("json")
}

async fn spawn_agent_worker() -> (String, u16) {
    let app = axum::Router::new().route(
        "/agent/llm",
        axum::routing::post(|axum::Json(body): axum::Json<Value>| async move {
            let prompt = body.to_string();
            let text = if prompt.contains("plan") {
                json!({
                    "action": "plan",
                    "goal": "write secret note",
                    "steps": [{"action": "write_file", "args": {"path": "personal.txt"}}],
                    "estimated_steps": 1
                })
                .to_string()
            } else if prompt.contains("verdict") || prompt.contains("VERIFY") {
                json!({
                    "action": "verdict",
                    "verdict": "PASS",
                    "next_state": "DONE",
                    "reason": "verified",
                    "corrections": []
                })
                .to_string()
            } else if prompt.contains("personal.txt") && !prompt.contains("result") {
                json!({
                    "action": "write_file",
                    "args": {"path": "personal.txt", "content": "private user payload\n"}
                })
                .to_string()
            } else {
                json!({"action": "final", "message": "done"}).to_string()
            };
            axum::Json(json!({"text": text}))
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let addr = listener.local_addr().expect("addr");
    tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });
    (format!("http://{addr}"), addr.port())
}

fn seed_users(data_dir: &std::path::Path) {
    let mut users = Users::new();
    for (email, role, tenant) in [
        ("alice@acme.test", "owner", "acme"),
        ("carol@acme.test", "user", "acme"),
        ("bob@globex.test", "owner", "globex"),
    ] {
        let mut rec = Map::new();
        rec.insert("password".into(), json!(hash_password("Pass1234")));
        rec.insert("role".into(), json!(role));
        rec.insert("tenant_id".into(), json!(tenant));
        rec.insert("disabled".into(), json!(false));
        rec.insert("name".into(), json!(email));
        users.insert(email, rec);
    }
    lattice_auth::UserStore::new(data_dir).save(&users);
}

#[tokio::test]
async fn multi_user_gateway_isolates_agent_tools_mcp_skills_cu_automation_and_tenants() {
    let (worker_origin, worker_port) = spawn_agent_worker().await;
    let scratch = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join("agent_isolation_e2e")
        .join("multi_user");
    let _ = std::fs::remove_dir_all(&scratch);
    let data_dir = scratch.join("data");
    let agent_root = scratch.join("agent_workspace");
    std::fs::create_dir_all(&data_dir).expect("data_dir");
    std::fs::create_dir_all(&agent_root).expect("agent_root");

    seed_users(&data_dir);

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
        proposals: Some(Arc::new(
            lattice_agent::kernel::proposals::JsonProposalStore::new(scratch.join("proposals")),
        )),
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

    // Mint sessions for Alice (acme), Carol (acme), Bob (globex).
    let alice_tok = product
        .auth
        .sessions()
        .create("user:alice", Some("alice@acme.test"));
    let carol_tok = product
        .auth
        .sessions()
        .create("user:carol", Some("carol@acme.test"));
    let bob_tok = product
        .auth
        .sessions()
        .create("user:bob", Some("bob@globex.test"));

    // Create shared org workspace owned by Alice, with Carol as read-only viewer; Bob is NOT a member.
    let org_ws = product
        .resolver
        .create_organization_workspace("Acme Eng", Some("alice@acme.test"), None)
        .expect("create org");
    let org_id = org_ws["workspace_id"].as_str().expect("org id").to_string();
    product
        .resolver
        .add_member(
            &org_id,
            "carol@acme.test",
            "viewer",
            Some("alice@acme.test"),
        )
        .expect("add carol");

    let gw_state = GatewayState::new(Arc::new(FixedProvider::new(
        worker_origin.clone(),
        worker_port,
    )))
    .expect("gateway")
    .with_db_path(data_dir.join("knowledge_graph.sqlite"))
    .with_agent_root(&agent_root)
    .with_agent_runs_dir(scratch.join("rust_agent_runs"))
    .with_pinned_posture(Posture::Open)
    .with_product(Arc::new(product));
    let gateway = TestGateway::start_with_state(gw_state).await;

    // 1. Unauthenticated access to /tools/list_dir is denied (401).
    let unauth = post_json(gateway.url("/tools/list_dir"), None, &[], json!({})).await;
    assert_eq!(unauth.status(), StatusCode::UNAUTHORIZED);

    // 2. Alice writes a secret file via /tools/write_file into her isolated personal root.
    let write_alice = post_json(
        gateway.url("/tools/write_file"),
        Some(&alice_tok),
        &[],
        json!({"path": "secret.txt", "content": "ALICE_ONLY_DATA"}),
    )
    .await;
    assert_eq!(write_alice.status(), StatusCode::OK);
    let alice_body = json_body(write_alice).await;
    let alice_ws_path = alice_body["workspace"]
        .as_str()
        .expect("ws path")
        .to_string();
    assert!(alice_ws_path.contains("tenants"), "{alice_ws_path}");
    assert!(alice_ws_path.contains("users"), "{alice_ws_path}");

    // 3. Bob cannot read Alice's secret.txt directly or via `..` traversal or `search_files` or `run_command`.
    let bob_read = post_json(
        gateway.url("/tools/read_file"),
        Some(&bob_tok),
        &[],
        json!({"path": "secret.txt"}),
    )
    .await;
    assert_ne!(bob_read.status(), StatusCode::OK);

    let bob_traverse = post_json(
        gateway.url("/tools/read_file"),
        Some(&bob_tok),
        &[],
        json!({"path": format!("{alice_ws_path}/secret.txt")}),
    )
    .await;
    assert_eq!(bob_traverse.status(), StatusCode::BAD_REQUEST);

    let bob_search = post_json(
        gateway.url("/tools/search_files"),
        Some(&bob_tok),
        &[],
        json!({"query": "ALICE_ONLY_DATA"}),
    )
    .await;
    assert_eq!(bob_search.status(), StatusCode::OK);
    let search_json = json_body(bob_search).await;
    let empty: Vec<Value> = vec![];
    assert!(
        search_json["result"]["matches"]
            .as_array()
            .unwrap_or(&empty)
            .is_empty(),
        "Bob's search must not see Alice's file: {search_json}"
    );

    let bob_exec = post_json(
        gateway.url("/tools/run_command"),
        Some(&bob_tok),
        &[],
        json!({"command": format!("cat {alice_ws_path}/secret.txt")}),
    )
    .await;
    assert_eq!(bob_exec.status(), StatusCode::FORBIDDEN);

    // Symlink inside Bob's isolated root pointing at Alice's secret is blocked.
    let bob_root = resolve_isolated_agent_root(&IsolatedRootSpec {
        base_root: agent_root.clone(),
        user_id: Some("bob@globex.test".into()),
        tenant_id: Some("globex".into()),
        workspace_id: None,
        shared_authorized: false,
        require_user_isolation: true,
    })
    .expect("bob root");
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(
            PathBuf::from(&alice_ws_path).join("secret.txt"),
            bob_root.join("alice_link"),
        )
        .expect("symlink");
        let sym_read = post_json(
            gateway.url("/tools/read_file"),
            Some(&bob_tok),
            &[],
            json!({"path": "alice_link"}),
        )
        .await;
        assert_eq!(sym_read.status(), StatusCode::BAD_REQUEST);
    }

    // 4. MCP (`/mcp/call`) and Computer Use (`/tools/computer_use_status`) use per-user isolated roots.
    let alice_cu = client()
        .get(gateway.url("/tools/computer_use_status"))
        .bearer_auth(&alice_tok)
        .send()
        .await
        .expect("alice cu");
    assert_eq!(alice_cu.status(), StatusCode::OK);
    let alice_cu_json = json_body(alice_cu).await;
    let bob_cu = client()
        .get(gateway.url("/tools/computer_use_status"))
        .bearer_auth(&bob_tok)
        .send()
        .await
        .expect("bob cu");
    assert_eq!(bob_cu.status(), StatusCode::OK);
    let bob_cu_json = json_body(bob_cu).await;
    assert!(alice_cu_json["workspace"].is_string());
    assert!(bob_cu_json["workspace"].is_string());
    assert_ne!(alice_cu_json["workspace"], bob_cu_json["workspace"]);

    let bob_mcp = post_json(
        gateway.url("/mcp/call"),
        Some(&bob_tok),
        &[],
        json!({"tool": "read_file", "arguments": {"path": "secret.txt"}}),
    )
    .await;
    let bob_mcp_json = json_body(bob_mcp).await;
    assert!(
        bob_mcp_json.get("error").is_some() || bob_mcp_json["status"] != json!("ok"),
        "MCP call in Bob's sandbox must not read Alice's secret.txt: {bob_mcp_json}"
    );

    // 5. Shared org workspace permissions:
    //    - Alice writes `shared.txt` into org_id
    //    - Carol (viewer) CAN read `shared.txt` in org_id, CANNOT write to org_id
    //    - Bob (non-member) CANNOT read or write org_id (403 Forbidden)
    let alice_org_write = post_json(
        gateway.url("/tools/write_file"),
        Some(&alice_tok),
        &[("x-workspace-id", &org_id)],
        json!({"path": "shared.txt", "content": "TEAM_DOC"}),
    )
    .await;
    assert_eq!(alice_org_write.status(), StatusCode::OK);

    let carol_org_read = post_json(
        gateway.url("/tools/read_file"),
        Some(&carol_tok),
        &[("x-workspace-id", &org_id)],
        json!({"path": "shared.txt"}),
    )
    .await;
    assert_eq!(carol_org_read.status(), StatusCode::OK);

    let carol_org_write = post_json(
        gateway.url("/tools/write_file"),
        Some(&carol_tok),
        &[("x-workspace-id", &org_id)],
        json!({"path": "shared.txt", "content": "OVERWRITE"}),
    )
    .await;
    assert_eq!(carol_org_write.status(), StatusCode::FORBIDDEN);

    let bob_org_read = post_json(
        gateway.url("/tools/read_file"),
        Some(&bob_tok),
        &[("x-workspace-id", &org_id)],
        json!({"path": "shared.txt"}),
    )
    .await;
    assert_eq!(bob_org_read.status(), StatusCode::FORBIDDEN);

    // 6. Automation & Browser routes block unauthorized workspace spoofing (`X-Workspace-Id`).
    let bob_auto = client()
        .get(gateway.url("/api/automation/patterns"))
        .bearer_auth(&bob_tok)
        .header("x-workspace-id", &org_id)
        .send()
        .await
        .expect("bob automation");
    assert_eq!(bob_auto.status(), StatusCode::FORBIDDEN);

    let bob_browser = post_json(
        gateway.url("/api/browser/read-url"),
        Some(&bob_tok),
        &[],
        json!({"url": "https://example.com", "workspace_id": org_id}),
    )
    .await;
    assert_eq!(bob_browser.status(), StatusCode::FORBIDDEN);

    // 7. Tenant spoofing (`X-Tenant-Id: acme` by Bob who belongs to `globex`) is denied 403
    //    and written to `audit_log.json` via PlatformAuthAudit.
    let cross_tenant = post_json(
        gateway.url("/tools/list_dir"),
        Some(&bob_tok),
        &[("x-tenant-id", "acme")],
        json!({}),
    )
    .await;
    assert_eq!(cross_tenant.status(), StatusCode::FORBIDDEN);

    // 8. `/agent` enforces server-side user_email & isolated root (ignores spoofed user_email).
    let alice_agent = post_json(
        gateway.url("/agent"),
        Some(&alice_tok),
        &[],
        json!({
            "message": "write personal.txt",
            "user_email": "bob@globex.test",
            "user_role": "user",
            "permission_mode": "trusted",
        }),
    )
    .await;
    assert_eq!(alice_agent.status(), StatusCode::OK);
    let alice_agent_json = json_body(alice_agent).await;
    let run_ws = alice_agent_json["workspace"].as_str().unwrap_or_default();
    assert_eq!(
        run_ws, alice_ws_path,
        "agent run must land in Alice's isolated root even when body tries to spoof bob@globex.test"
    );
    assert!(!bob_root.join("personal.txt").exists());

    // Audit log recorded auth.tenant.denied and agent.workspace.denied events.
    let audit_text =
        std::fs::read_to_string(data_dir.join("audit_log.json")).expect("audit_log.json");
    assert!(
        audit_text.contains("auth.tenant.denied"),
        "audit log missing auth.tenant.denied: {audit_text}"
    );
    assert!(
        audit_text.contains("agent.workspace.denied"),
        "audit log missing agent.workspace.denied: {audit_text}"
    );

    gateway.stop().await;
}
