//! Per-user agent root & workspace isolation tests (Review §7.2).
//!
//! Verifies that authenticated users receive separate server-derived
//! `AGENT_ROOT` directories (`users/<sanitized_user_id>`), authorized shared
//! workspaces map to `workspaces/<sanitized_workspace_id>` while unauthorized
//! shared workspaces fail closed, and User A cannot read, write, search,
//! traverse (`..` or symlinks), or shell-execute into User B's files.

use std::sync::Arc;

use lattice_agent::policy::{default_blocked_write_prefixes, ToolPolicy};
use lattice_agent::tools::command::validate;
use lattice_agent::tools::{CallScope, NativeCall, NativeTools, ToolConfig, ToolHost};
use lattice_agent::worker::{ToolOutcome, WorkerClient};
use lattice_agent::{
    loop_router_isolated, IsolatedRootSpec, LoopConfig, ScopeAuthorizer, Workspace,
};
use serde_json::{json, Value};

async fn start_dummy_worker() -> String {
    let app = axum::Router::new().route(
        "/agent/llm",
        axum::routing::post(|axum::Json(body): axum::Json<Value>| async move {
            let prompt = body.to_string();
            let text = if prompt.contains("plan") {
                json!({
                    "action": "plan",
                    "goal": "write personal note",
                    "steps": [{"action": "write_file", "args": {"path": "note.txt"}}],
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
    format!("http://{addr}")
}

#[test]
fn personal_roots_never_collide_and_shared_requires_authorization() {
    let dir = tempfile::tempdir().expect("tempdir");
    let base = dir.path().join("agent_base");

    let alice = Workspace::for_isolated_scope(&IsolatedRootSpec {
        base_root: base.clone(),
        user_id: Some("a.b@example.com".into()),
        tenant_id: None,
        workspace_id: Some("personal".into()),
        shared_authorized: false,
        require_user_isolation: true,
    })
    .expect("alice workspace");

    let alice_plus = Workspace::for_isolated_scope(&IsolatedRootSpec {
        base_root: base.clone(),
        user_id: Some("a+b@example.com".into()),
        tenant_id: None,
        workspace_id: None,
        shared_authorized: false,
        require_user_isolation: true,
    })
    .expect("alice+ workspace");

    assert_ne!(
        alice.root(),
        alice_plus.root(),
        "hash suffix must keep normalized-punctuation variants distinct"
    );
    assert!(alice.is_confined());

    // Unauthenticated access fails closed when isolation is required.
    let anon = Workspace::for_isolated_scope(&IsolatedRootSpec {
        base_root: base.clone(),
        user_id: None,
        tenant_id: None,
        workspace_id: None,
        shared_authorized: false,
        require_user_isolation: true,
    });
    assert!(anon.is_err());

    // Spoofing a shared workspace without authorization fails closed.
    let spoofed = Workspace::for_isolated_scope(&IsolatedRootSpec {
        base_root: base.clone(),
        user_id: Some("a.b@example.com".into()),
        tenant_id: None,
        workspace_id: Some("org-secret".into()),
        shared_authorized: false,
        require_user_isolation: true,
    });
    assert!(spoofed.is_err());

    // Authorized shared workspace lands under workspaces/..., distinct from users/...
    let shared = Workspace::for_isolated_scope(&IsolatedRootSpec {
        base_root: base,
        user_id: Some("a.b@example.com".into()),
        tenant_id: Some("acme".into()),
        workspace_id: Some("org-eng".into()),
        shared_authorized: true,
        require_user_isolation: true,
    })
    .expect("shared workspace");
    let shared_str = shared.root().to_string_lossy();
    assert!(shared_str.contains("tenants"), "{shared_str}");
    assert!(shared_str.contains("workspaces"), "{shared_str}");
}

#[tokio::test]
async fn user_a_cannot_read_write_traverse_symlink_or_exec_user_b_files() {
    let dir = tempfile::tempdir().expect("tempdir");
    let base = dir.path().join("agent_base");
    let brain = dir.path().join("brain");

    let ws_a = Workspace::user_personal(&base, "alice@example.com").expect("alice");
    let ws_b = Workspace::user_personal(&base, "bob@example.com").expect("bob");

    std::fs::write(ws_b.root().join("secret.txt"), "BOB_TOP_SECRET\n").expect("bob secret");

    // 1. Direct path / `..` traversal from User A's root to User B's root is blocked.
    let rel_to_b = format!(
        "../../users/{}/secret.txt",
        ws_b.root().file_name().unwrap().to_string_lossy()
    );
    assert!(ws_a.resolve(&rel_to_b).is_err());
    assert!(ws_a
        .resolve(&ws_b.root().join("secret.txt").to_string_lossy())
        .is_err());

    // 2. Unix symlink inside User A's root pointing at User B's secret is blocked.
    #[cfg(unix)]
    {
        let link = ws_a.root().join("steal_bob");
        std::os::unix::fs::symlink(ws_b.root().join("secret.txt"), &link).expect("symlink");
        assert!(ws_a.resolve("steal_bob").is_err());
        assert!(validate(&ws_a, "cat steal_bob", None).is_err());
    }

    // 3. Shell execution validation blocks reading/searching User B's directory.
    assert!(validate(
        &ws_a,
        &format!("cat {}", ws_b.root().join("secret.txt").display()),
        None
    )
    .is_err());
    assert!(validate(&ws_a, &format!("rg BOB {}", ws_b.root().display()), None).is_err());
    assert!(validate(
        &ws_a,
        &format!("find {} -type f", ws_b.root().display()),
        None
    )
    .is_err());

    // 4. NativeTools confined mode blocks `local_write` into User B's directory or `..`.
    let cfg = ToolConfig {
        brain_dir: brain.clone(),
        role: "owner".into(),
        blocked_write_prefixes: default_blocked_write_prefixes(),
    };
    let tools_a = NativeTools::new(ws_a.clone(), cfg, WorkerClient::new("http://127.0.0.1:1"));

    let mut args = serde_json::Map::new();
    args.insert(
        "path".into(),
        json!(ws_b.root().join("pwned.txt").to_string_lossy()),
    );
    args.insert("content".into(), json!("hacked"));
    let policy = ToolPolicy {
        risk: "write".into(),
        sandbox: "workspace".into(),
        ..ToolPolicy::default()
    };
    let scope_a = CallScope {
        user_email: Some("alice@example.com".into()),
        workspace_id: Some("personal".into()),
    };
    let outcome = tools_a
        .execute(NativeCall {
            tool: "local_write",
            args: &args,
            policy: &policy,
            scope: &scope_a,
        })
        .await;
    match outcome {
        ToolOutcome::Error(ref msg) => assert!(msg.contains("escapes"), "{msg}"),
        other => panic!("confined local_write must reject cross-user path, got {other:?}"),
    }
    assert!(!ws_b.root().join("pwned.txt").exists());

    // 5. Vault write (`knowledge_save`) uses CallScope even if args spoof `bob@example.com`.
    let mut vault_args = serde_json::Map::new();
    vault_args.insert("content".into(), json!("alice private note"));
    vault_args.insert("user_email".into(), json!("bob@example.com"));
    vault_args.insert("workspace_id".into(), json!("org-other"));
    let saved = match tools_a
        .execute(NativeCall {
            tool: "knowledge_save",
            args: &vault_args,
            policy: &policy,
            scope: &scope_a,
        })
        .await
    {
        ToolOutcome::Result(v) => v,
        other => panic!("vault save failed: {other:?}"),
    };
    let saved_path = saved["path"].as_str().unwrap_or_default();
    let expected_alice_vault =
        lattice_agent::tools::vault::scope_root(&brain, "personal", "alice@example.com");
    assert!(
        saved_path.starts_with(expected_alice_vault.to_string_lossy().as_ref()),
        "vault write landed in {saved_path}, expected under {}",
        expected_alice_vault.display()
    );
}

#[tokio::test]
async fn isolated_loop_router_enforces_per_user_roots_and_shared_workspace_acl() {
    let dir = tempfile::tempdir().expect("tempdir");
    let base = Workspace::new(dir.path().join("agent_base")).expect("base");
    let runs_dir = dir.path().join("runs");
    let origin = start_dummy_worker().await;

    let authorizer: ScopeAuthorizer = Arc::new(|user, ws| {
        Ok(matches!(
            (user, ws),
            (Some("alice@example.com"), Some("org-eng"))
        ))
    });
    let app = loop_router_isolated(
        base.clone(),
        LoopConfig {
            worker_origin: origin,
            runs_dir,
            client: None,
            proposals: None,
            hooks: None,
            external: None,
        },
        Some(authorizer),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let app_origin = format!("http://{}", listener.local_addr().expect("addr"));
    tokio::spawn(async move {
        let _ = axum::serve(listener, app).await;
    });
    let client = reqwest::Client::new();

    // Bob is denied access to org-eng.
    let resp = client
        .post(format!("{app_origin}/rust/agent/run"))
        .header("content-type", "application/json")
        .body(
            serde_json::to_vec(&json!({
                "message": "list workspace",
                "user_email": "bob@example.com",
                "workspace_id": "org-eng",
                "permission_mode": "trusted",
            }))
            .unwrap(),
        )
        .send()
        .await
        .expect("resp");
    assert_eq!(resp.status(), reqwest::StatusCode::FORBIDDEN);

    // Unauthenticated call without user_email is denied.
    let resp_anon = client
        .post(format!("{app_origin}/rust/agent/run"))
        .header("content-type", "application/json")
        .body(
            serde_json::to_vec(&json!({
                "message": "list workspace",
                "permission_mode": "trusted",
            }))
            .unwrap(),
        )
        .send()
        .await
        .expect("resp");
    assert_eq!(resp_anon.status(), reqwest::StatusCode::FORBIDDEN);

    // Empty ?user on /rust/agent/approvals returns empty list in isolated mode.
    let resp_approvals = client
        .get(format!("{app_origin}/rust/agent/approvals"))
        .send()
        .await
        .expect("approvals");
    assert_eq!(resp_approvals.status(), reqwest::StatusCode::OK);
    let bytes = resp_approvals.bytes().await.unwrap();
    let payload: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(payload["pending"], json!([]));
}
