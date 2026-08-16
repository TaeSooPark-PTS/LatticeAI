//! The review-proposals surface: fixture replay for the four Python modules
//! `review_proposals.json` captured, plus the OpenAPI contract they compose.
//!
//! These were five test binaries — four of them a single `replay_family` call
//! each — all recompiling the same 25kB harness. They are one binary now, with
//! every test function kept as it was.

// The shared harness is written for every suite that includes it, so a
// helper this one does not call still reads as dead in this binary.
#[allow(dead_code)]
mod review_queue_harness;

use lattice_platform::automation;
use lattice_platform::change_proposals;
use lattice_platform::hooks;
use lattice_platform::review_queue;
use review_queue_harness::{fragment, to_openapi, Install};

#[tokio::test]
async fn review_queue_replays_the_python_oracle() {
    let install = Install::start().await;
    install.replay_family("review_queue.py").await;
}

#[tokio::test]
async fn change_proposals_replays_the_python_oracle() {
    let install = Install::start().await;
    install.replay_family("change_proposals.py").await;
}

#[tokio::test]
async fn automation_replays_the_python_oracle() {
    let install = Install::start().await;
    install.replay_family("automation_intelligence.py").await;
}

#[tokio::test]
async fn hooks_replays_the_python_oracle() {
    let install = Install::start().await;
    install.replay_family("hooks.py").await;
}

#[test]
fn mounted_routes_match_the_committed_contract() {
    let spec = fragment();
    let mut expected: Vec<String> = spec["operation_order"]
        .as_array()
        .expect("operation_order")
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let mut actual: Vec<String> = review_queue::MOUNTED
        .iter()
        .chain(change_proposals::MOUNTED)
        .chain(automation::MOUNTED)
        .chain(hooks::MOUNTED)
        .map(|(method, path)| format!("{method} {}", to_openapi(path)))
        .collect();
    expected.sort();
    actual.sort();
    assert_eq!(
        actual, expected,
        "router and rust/fixtures/openapi/review_proposals.json disagree"
    );

    for (key, param) in spec["greedy_path_params"].as_object().expect("greedy") {
        let path = key.split_once(' ').unwrap().1;
        let param = param.as_str().unwrap();
        assert!(
            hooks::MOUNTED
                .iter()
                .any(|(_, p)| { to_openapi(p) == path && p.contains(&format!("*{param}")) }),
            "{key} matches slashes in Python; mount it as /*{param}"
        );
    }
}
