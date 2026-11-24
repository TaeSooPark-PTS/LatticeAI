use std::collections::HashMap;

use lattice_agent::tools::sandbox::Workspace;
use lattice_auth::{AuthConfig, AuthState, Clock};

use super::hybrid::search_notes_with_options;
use super::save_note;
use crate::toolsurface::tools::ToolsState;

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
