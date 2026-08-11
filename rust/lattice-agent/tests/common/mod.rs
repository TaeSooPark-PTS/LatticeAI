//! Shared fixture plumbing for the agent parity suites.
//!
//! Both suites read the same committed goldens and both build the same
//! throwaway workspace from the manifest's `tree` spec — the point being that
//! the Rust side never invents a fixture the Python side did not describe.
#![allow(dead_code)] // each test binary uses a different half of this module.

use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use lattice_agent::sandbox::Workspace;
use serde_json::Value;

/// `rust/fixtures/agent`.
pub fn fixtures() -> PathBuf {
    [env!("CARGO_MANIFEST_DIR"), "..", "fixtures", "agent"]
        .iter()
        .collect()
}

pub fn read_golden(name: &str) -> Value {
    let path = fixtures().join("golden").join(name);
    let raw = std::fs::read_to_string(&path).unwrap_or_else(|err| {
        panic!(
            "missing golden {} ({err}) — run scripts/generate_agent_parity_fixtures.py",
            path.display()
        )
    });
    serde_json::from_str(&raw).expect("goldens must be valid JSON")
}

pub fn manifest() -> &'static Value {
    static MANIFEST: OnceLock<Value> = OnceLock::new();
    MANIFEST.get_or_init(|| read_golden("manifest.json"))
}

/// The cases of a grid file, as a slice.
pub fn cases(golden: &Value, key: &str) -> Vec<Value> {
    golden[key]
        .as_array()
        .unwrap_or_else(|| panic!("golden has no {key} array"))
        .clone()
}

/// Build the workspace the command fixtures were generated in.
///
/// The spec is the manifest's, so a tree change on the Python side reaches this
/// suite as a different tree rather than as a mysterious mismatch.
pub fn build_tree(dir: &Path) -> Workspace {
    let root = dir.join("agent_workspace");
    std::fs::create_dir_all(&root).expect("root");
    for node in manifest()["tree"].as_array().expect("tree") {
        let kind = node["kind"].as_str().expect("kind");
        let relative = node["path"].as_str().expect("path");
        let target = root.join(relative);
        match kind {
            "outside" => std::fs::write(dir.join(relative), content(node)).expect("outside file"),
            "dir" => std::fs::create_dir_all(&target).expect("dir"),
            "file" => std::fs::write(&target, content(node)).expect("file"),
            "lines" => {
                let count = node["count"].as_u64().expect("count");
                let body: String = (0..count).map(|index| format!("{index:07}\n")).collect();
                std::fs::write(&target, body).expect("lines");
            }
            "symlink" => {
                let link_target = node["target"].as_str().expect("target");
                #[cfg(unix)]
                std::os::unix::fs::symlink(link_target, &target).expect("symlink");
                #[cfg(not(unix))]
                panic!("the fixture tree needs symlinks: {link_target}");
            }
            other => panic!("unknown tree node kind {other}"),
        }
    }
    Workspace::new(&root).expect("workspace")
}

fn content(node: &Value) -> String {
    node["content"].as_str().unwrap_or_default().to_string()
}

/// Substitute the placeholder the generator writes for the absolute root.
pub fn with_root(text: &str, workspace: &Workspace) -> String {
    text.replace("<AGENT_ROOT>", &workspace.root().display().to_string())
}

/// Report every mismatch at once: a parity failure that names one case out of a
/// thousand and hides the rest is a parity failure you fix three times.
pub fn assert_no_failures(checked: usize, failures: Vec<String>, what: &str) {
    assert!(
        failures.is_empty(),
        "{} of {checked} {what} mismatched:\n{}",
        failures.len(),
        failures
            .iter()
            .take(25)
            .cloned()
            .collect::<Vec<_>>()
            .join("\n")
    );
    assert!(checked > 0, "no {what} were checked at all");
}
