//! Voice family: KEEP_WORKER routes must not be claimed (WP-R9).

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#[path = "portability_harness.rs"]
mod harness;

use harness::openapi_fragment;
use lattice_platform::voice::{KEEP, MOUNTED};

#[test]
fn voice_router_claims_capture_natively_status_stays_keep() {
    // W3b: POST /api/capture/voice is product-native. Spec stays in
    // worker_keep.json (byte-composition of fragments must not change).
    assert_eq!(MOUNTED, &[("POST", "/api/capture/voice")]);
    assert_eq!(
        KEEP,
        &[
            ("GET", "/api/capture/voice/status"),
            ("POST", "/api/capture/voice"),
        ]
    );
    let worker = openapi_fragment("worker_keep.json");
    let mut ops: Vec<&str> = worker["operation_order"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|v| v.as_str())
        .filter(|op| op.contains("/api/capture/voice"))
        .collect();
    ops.sort();
    assert_eq!(
        ops,
        ["GET /api/capture/voice/status", "POST /api/capture/voice"]
    );
}

#[test]
fn voice_keep_routes_are_not_in_the_r9_fragment() {
    let spec = openapi_fragment("portability_network.json");
    for op in spec["operation_order"].as_array().unwrap() {
        let op = op.as_str().unwrap();
        assert!(
            !op.contains("/api/capture/voice"),
            "KEEP voice route leaked into the R9 fragment: {op}"
        );
    }
}
