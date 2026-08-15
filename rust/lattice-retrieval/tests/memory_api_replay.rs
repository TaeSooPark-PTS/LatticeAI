//! Replay `memory` records from `memory_brain.json`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod common;

#[tokio::test]
async fn memory_replays_the_python_oracle() {
    let install = common::brain::Install::start().await;
    install.replay_family("memory").await;
    // Every Self-Model write and the vector rebuild are native since v11.7.0.
    // Until then they posted to `POST /worker/graph/mutate`, which the Python
    // worker stopped serving in v11.6.0 — so on a live install the four
    // Self-Model routes answered 404 while this replay stayed green against a
    // stand-in that still mounted it. The stand-in is now a tripwire.
    assert_eq!(
        common::brain::seed::GRAPH_MUTATE_CALLS.load(std::sync::atomic::Ordering::SeqCst),
        0,
        "a memory route still delegated a graph write to the retired seam"
    );
}
