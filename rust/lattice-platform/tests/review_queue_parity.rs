#[allow(dead_code)]
mod review_queue_harness;

use lattice_platform::governance::automation;
use lattice_platform::governance::change_proposals;
use lattice_platform::governance::hooks;
use lattice_platform::governance::review_queue;
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
