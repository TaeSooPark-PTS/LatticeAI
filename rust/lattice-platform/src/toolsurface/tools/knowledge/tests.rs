use std::collections::HashMap;

use axum::http::HeaderMap;
use lattice_agent::tools::sandbox::Workspace;
use lattice_auth::{AuthConfig, AuthState, Clock, Identity};
use serde_json::{json, Value};

use super::hybrid::search_notes_with_options;
use super::{run_knowledge_search, save_note};
use crate::toolsurface::tools::{ToolExecError, ToolsState};

#[test]
fn hybrid_note_search_surfaces_bm25f_and_wikilink_graph_subgraph() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let mut config = AuthConfig::from_map(&HashMap::new(), None);
    config.data_dir = tmp.path().to_path_buf();
    let auth = AuthState::with_clock(config, Clock::frozen(1_700_000_000.0));
    let workspace = Workspace::new(tmp.path()).expect("workspace");
    let state = ToolsState::new(auth, workspace, tmp.path());
    save_note(
        &state,
        "personal",
        "user@example.com",
        "# 하이브리드 검색 설계\n한국어 BM25F 및 그래프 확장을 지원합니다. [[GraphRAG 서브그래프 노트]] 참고 #graphrag #bm25f",
        Some("하이브리드 검색 설계"),
        false,
    )
    .expect("save seed note");
    save_note(
        &state,
        "personal",
        "user@example.com",
        "# GraphRAG 서브그래프 노트\n1홉 및 2홉 위키링크 이웃 문서를 RRF로 결합합니다. #graphrag",
        Some("GraphRAG 서브그래프 노트"),
        false,
    )
    .expect("save neighbor note");

    let report = search_notes_with_options(
        &state,
        "personal",
        "user@example.com",
        "하이브리드에서 BM25F",
        5,
        false,
        Some("hybrid"),
        Some(2),
        true,
        None,
    )
    .expect("hybrid search");

    assert_eq!(report["mode"], "hybrid");
    let results = report["results"].as_array().expect("results array");
    assert!(
        results.len() >= 2,
        "Both seed note and 1-hop wikilink/tag neighbor should be returned: {results:?}"
    );
    assert!(results[0]["bm25_score"].as_f64().unwrap_or(0.0) > 0.0);
    assert!(
        results.iter().any(|r| {
            r["relative_path"]
                .as_str()
                .unwrap_or("")
                .contains("GraphRAG")
                && r["graph_score"].as_f64().unwrap_or(0.0) > 0.0
        }),
        "1-hop neighbor should receive positive graph_score: {results:?}"
    );
    let subgraph = &report["subgraph"];
    assert_eq!(subgraph["hops"], 2);
    assert!(!subgraph["nodes"].as_array().unwrap().is_empty());
    assert!(!subgraph["edges"].as_array().unwrap().is_empty());
}

fn age(path: &str, days: u64) {
    let when = std::time::SystemTime::now() - std::time::Duration::from_secs(days * 86_400);
    std::fs::File::options()
        .write(true)
        .open(path)
        .expect("open note")
        .set_modified(when)
        .expect("mtime");
}

#[test]
fn mcp_knowledge_search_ranks_notes_by_recency_and_sort_by() {
    let tmp = tempfile::tempdir().expect("tempdir");
    let mut config = AuthConfig::from_map(&HashMap::new(), None);
    config.data_dir = tmp.path().to_path_buf();
    let auth = AuthState::with_clock(config, Clock::frozen(1_700_000_000.0));
    let workspace = Workspace::new(tmp.path()).expect("workspace");
    let mut state = ToolsState::new(auth, workspace, tmp.path());
    state.require_auth = false;
    for (title, content, days) in [
        (
            "alpha",
            "zebrafish rollout zebrafish rollout zebrafish rollout",
            200,
        ),
        ("beta", "zebrafish tank cleaned, nothing about plans", 1),
    ] {
        let saved = save_note(&state, "", "", content, Some(title), false).expect("save");
        age(saved["path"].as_str().expect("path"), days);
    }
    let identity = Identity::local_owner();
    let search = |args: Value| run_knowledge_search(&state, &identity, &HeaderMap::new(), &args);
    let order = |report: &Value| -> Vec<String> {
        report["results"]
            .as_array()
            .expect("results")
            .iter()
            .map(|item| item["relative_path"].as_str().expect("path").to_string())
            .collect()
    };

    let relevance = search(json!({
        "query": "zebrafish rollout", "mode": "hybrid", "sort_by": "relevance"
    }))
    .expect("relevance");
    assert_eq!(relevance["sort_by"], "relevance");
    assert_eq!(order(&relevance)[0], "00_Raw/alpha.md");

    let newest = search(json!({
        "query": "zebrafish rollout", "mode": "hybrid", "sort_by": "newest"
    }))
    .expect("newest");
    assert_eq!(newest["sort_by"], "newest");
    assert_eq!(order(&newest)[0], "00_Raw/beta.md");
    assert!(newest["results"][0]["updated_at"].is_string());
    assert_eq!(newest["results"][0]["status"], "active");
    assert_eq!(newest["subgraph"]["nodes"][0]["id"], "00_Raw/beta.md");

    let plain = search(json!({"query": "zebrafish", "max_results": 1})).expect("plain");
    assert_eq!(order(&plain), vec!["00_Raw/beta.md".to_string()]);

    let refused = search(json!({"query": "zebrafish", "sort_by": "oldest"}));
    assert!(matches!(
        refused,
        Err(ToolExecError::Message(message)) if message.starts_with("sort_by must be one of")
    ));
    assert!(search(json!({"query": "zebrafish", "sort_by": 3})).is_err());

    let schema = crate::toolsurface::mcp::NATIVE_TOOLS
        .iter()
        .find(|tool| tool.name == "knowledge_search")
        .expect("tool")
        .input_schema_value();
    assert_eq!(
        schema["properties"]["sort_by"]["enum"],
        json!(["relevance", "newest", "auto"])
    );
}
