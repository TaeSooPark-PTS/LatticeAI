pub mod brain;

use std::collections::BTreeSet;
use std::path::PathBuf;
use std::sync::OnceLock;

use lattice_core::open_read_only;
use rusqlite::Connection;
use serde_json::Value;

pub fn fixtures() -> PathBuf {
    [env!("CARGO_MANIFEST_DIR"), "..", "fixtures"]
        .iter()
        .collect()
}

pub fn read_json(path: PathBuf) -> Value {
    let raw = std::fs::read_to_string(&path)
        .unwrap_or_else(|err| panic!("missing fixture {}: {err}", path.display()));
    serde_json::from_str(&raw).expect("fixture must be valid JSON")
}

pub fn golden(name: &str) -> Value {
    read_json(fixtures().join("golden").join(format!("{name}.json")))
}

pub fn manifest() -> &'static Value {
    static MANIFEST: OnceLock<Value> = OnceLock::new();
    MANIFEST.get_or_init(|| read_json(fixtures().join("golden").join("manifest.json")))
}

pub fn pin_environment() {
    static PINNED: OnceLock<()> = OnceLock::new();
    PINNED.get_or_init(|| {
        for (key, value) in manifest()["pinned_env"].as_object().unwrap() {
            std::env::set_var(key, value.as_str().unwrap());
        }
    });
}

pub fn open_store(dir: &std::path::Path) -> Connection {
    let source = fixtures().join(manifest()["store"].as_str().unwrap());
    let target = dir.join("parity_store.sqlite");
    std::fs::copy(&source, &target).expect("fixture database must exist");
    open_read_only(&target).expect("the fixture must open read-only")
}

pub fn allowed_set(value: Option<&Value>) -> Option<BTreeSet<String>> {
    value?.as_array().map(|items| {
        items
            .iter()
            .map(|item| item.as_str().unwrap_or_default().to_string())
            .collect()
    })
}

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
