//! Shared plumbing for the two parity suites.
//!
//! Both `parity.rs` (the Phase-1 search engines) and `suites.rs` (the Phase-2/3
//! ports) read the same committed store, the same manifest and the same golden
//! directory, and both compare **exactly**: `serde_json::Value` equality over
//! the whole response, so a drifting float, a missing honesty field, a reordered
//! tie and a renamed key all fail the same way.
//!
//! Regenerate with `.venv/bin/python scripts/generate_rust_parity_fixtures.py`.

use std::collections::BTreeSet;
use std::path::PathBuf;
use std::sync::OnceLock;

use lattice_core::open_read_only;
use rusqlite::Connection;
use serde_json::Value;

/// The committed fixture directory.
pub fn fixtures() -> PathBuf {
    [env!("CARGO_MANIFEST_DIR"), "..", "fixtures"]
        .iter()
        .collect()
}

/// Read one JSON file, failing with its path rather than a bare parse error.
pub fn read_json(path: PathBuf) -> Value {
    let raw = std::fs::read_to_string(&path)
        .unwrap_or_else(|err| panic!("missing fixture {}: {err}", path.display()));
    serde_json::from_str(&raw).expect("fixture must be valid JSON")
}

/// One golden by name.
pub fn golden(name: &str) -> Value {
    read_json(fixtures().join("golden").join(format!("{name}.json")))
}

/// The manifest the generator wrote alongside the goldens.
pub fn manifest() -> &'static Value {
    static MANIFEST: OnceLock<Value> = OnceLock::new();
    MANIFEST.get_or_init(|| read_json(fixtures().join("golden").join("manifest.json")))
}

/// Pin every environment knob to the configuration the goldens were built with.
///
/// A developer shell that already exports `LATTICEAI_VECTOR_INDEX=hnsw` would
/// otherwise fail these suites for a reason unrelated to the port.
pub fn pin_environment() {
    static PINNED: OnceLock<()> = OnceLock::new();
    PINNED.get_or_init(|| {
        for (key, value) in manifest()["pinned_env"].as_object().unwrap() {
            std::env::set_var(key, value.as_str().unwrap());
        }
    });
}

/// Open the committed store read-only — from a copy, so a stray `-wal`/`-shm`
/// sidecar can never land next to a checked-in fixture.
pub fn open_store(dir: &std::path::Path) -> Connection {
    let source = fixtures().join(manifest()["store"].as_str().unwrap());
    let target = dir.join("parity_store.sqlite");
    std::fs::copy(&source, &target).expect("fixture database must exist");
    open_read_only(&target).expect("the fixture must open read-only")
}

/// `allowed_workspaces` as the engines want it: absent/null is "no scoping",
/// a list (even an empty one) is a membership filter.
pub fn allowed_set(value: Option<&Value>) -> Option<BTreeSet<String>> {
    value?.as_array().map(|items| {
        items
            .iter()
            .map(|item| item.as_str().unwrap_or_default().to_string())
            .collect()
    })
}

/// The first differing path, so a failure names the field instead of dumping
/// two thousand lines of JSON.
pub fn diff(label: &str, expected: &Value, got: &Value) -> String {
    let mut trail = Vec::new();
    walk(expected, got, &mut String::new(), &mut trail);
    format!(
        "  {label}: {}",
        trail.first().cloned().unwrap_or_else(|| "differs".into())
    )
}

fn walk(expected: &Value, got: &Value, path: &mut String, out: &mut Vec<String>) {
    if out.len() >= 3 || expected == got {
        return;
    }
    match (expected, got) {
        (Value::Object(a), Value::Object(b)) => {
            let keys: BTreeSet<&String> = a.keys().chain(b.keys()).collect();
            for key in keys {
                let mut next = format!("{path}.{key}");
                match (a.get(key), b.get(key)) {
                    (Some(x), Some(y)) => walk(x, y, &mut next, out),
                    (Some(_), None) => out.push(format!("{next} missing in rust")),
                    (None, Some(_)) => out.push(format!("{next} extra in rust")),
                    (None, None) => {}
                }
            }
        }
        (Value::Array(a), Value::Array(b)) if a.len() == b.len() => {
            for (index, (x, y)) in a.iter().zip(b.iter()).enumerate() {
                let mut next = format!("{path}[{index}]");
                walk(x, y, &mut next, out);
            }
        }
        _ => out.push(format!("{path}: python={expected} rust={got}")),
    }
}
