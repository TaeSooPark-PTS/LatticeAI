use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use lattice_host::gateway::allowlist::Allowlist;

const NOT_WORKER_CALLS: &[(&str, &str)] = &[
    (
        "/agent/resume",
        "lattice-platform mounts it natively (agents::AGENT_LOOP_MOUNTED)",
    ),
    (
        "/agent/approvals",
        "lattice-platform mounts it natively (agents::AGENT_LOOP_MOUNTED)",
    ),
    (
        "/agent/eval",
        "lattice-platform mounts it natively (agents::AGENT_LOOP_MOUNTED)",
    ),
    (
        "/auth/sso/callback",
        "lattice-auth's own OIDC redirect target, served by this process",
    ),
    (
        "/rust/ingest/plan",
        "lattice-ingest's native dry-run route (api::PLAN_PATH)",
    ),
    (
        "/rust/ingest/chunk",
        "lattice-ingest's native dry-run route (api::CHUNK_PATH)",
    ),
];

const NOT_WORKER_CALLS_IN: &[(&str, &str, &str)] = &[
    (
        "/worker/graph/mutate",
        "lattice-host/src/gateway/allowlist.rs",
        "the retired graph-write seam, named only by the proxy allowlist's own \
         unit test, which asserts this path is NOT forwarded. Keeping one \
         negative assertion is what stops it being quietly re-allowlisted; no \
         other file in rust/*/src names it (§F-G)",
    ),
    (
        "/worker/multimodal/describe",
        "lattice-host/src/gateway/allowlist.rs",
        "the image-describe compute seam, deleted in v11.8.0 for having no \
         caller. Named only by the proxy allowlist's own unit test, which \
         asserts it is NOT forwarded — same reason as the line above: an \
         unregenerated fixture would keep proxying it, and the browser would \
         then get the *worker's* 404 instead of the front door's",
    ),
];

const KNOWN_STRANDED_SEAM_PATHS: &[(&str, usize, &str)] = &[];

const SEAM_CALLS: [&str; 3] = ["post_json(", "get_json(", "stream_sse("];

fn workspace_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("rust/ workspace root")
        .to_path_buf()
}

fn rust_sources() -> Vec<PathBuf> {
    let mut out = Vec::new();
    let entries = std::fs::read_dir(workspace_root()).expect("rust/ is readable");
    for entry in entries.flatten() {
        let src = entry.path().join("src");
        if src.is_dir() {
            collect_rs(&src, &mut out);
        }
    }
    out.sort();
    assert!(
        out.len() > 100,
        "the scanner found only {} source files, which is a broken walk rather \
         than a clean workspace",
        out.len()
    );
    out
}

fn collect_rs(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_rs(&path, out);
        } else if path.extension().and_then(|ext| ext.to_str()) == Some("rs") {
            out.push(path);
        }
    }
}

fn string_literals(line: &str) -> Vec<String> {
    let chars: Vec<char> = line.chars().collect();
    let mut out = Vec::new();
    let mut index = 0;
    while index < chars.len() {
        if chars[index] != '"' {
            index += 1;
            continue;
        }
        let mut cursor = index + 1;
        let mut value = String::new();
        let mut escaped = false;
        while cursor < chars.len() {
            let ch = chars[cursor];
            if escaped {
                value.push(ch);
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == '"' {
                break;
            } else {
                value.push(ch);
            }
            cursor += 1;
        }
        out.push(value);
        index = cursor + 1;
    }
    out
}

fn path_literals(text: &str) -> BTreeSet<(usize, String)> {
    let mut out = BTreeSet::new();
    for (offset, line) in text.lines().enumerate() {
        let trimmed = line.trim_start();
        if trimmed.starts_with("//") {
            continue;
        }
        let number = offset + 1;
        let literals = string_literals(line);
        for literal in &literals {
            if literal.starts_with("/worker/") || literal.starts_with("/agent/") {
                out.insert((number, literal.clone()));
            }
        }
        let declares_const = trimmed.starts_with("const ") || trimmed.starts_with("pub const ");
        let names_str = line.contains(": &str = \"") || line.contains(": &'static str = \"");
        if declares_const && names_str {
            if let Some(literal) = literals.iter().find(|value| is_pathish(value)) {
                out.insert((number, literal.clone()));
            }
        }
    }
    for call in SEAM_CALLS {
        let mut from = 0usize;
        while let Some(found) = text[from..].find(call) {
            let after = from + found + call.len();
            let rest = &text[after..];
            let skipped = rest.trim_start_matches(|ch: char| ch.is_whitespace() || ch == '&');
            if let Some(inner) = skipped.strip_prefix('"') {
                if let Some(end) = inner.find('"') {
                    let literal = &inner[..end];
                    let start = after + (rest.len() - skipped.len()) + 1;
                    if is_pathish(literal) {
                        out.insert((text[..start].lines().count(), literal.to_string()));
                    }
                }
            }
            from = after;
        }
    }
    out
}

fn is_pathish(literal: &str) -> bool {
    literal.len() > 1
        && literal.starts_with('/')
        && !literal.starts_with("//")
        && !literal.contains(char::is_whitespace)
}

fn is_placeholder(segment: &str) -> bool {
    segment.starts_with('{') || segment.starts_with(':') || segment.starts_with('*')
}

fn path_matches(literal: &str, allowed: &str) -> bool {
    if literal == allowed {
        return true;
    }
    let left: Vec<&str> = literal.split('/').collect();
    let right: Vec<&str> = allowed.split('/').collect();
    left.len() == right.len()
        && left
            .iter()
            .zip(right.iter())
            .all(|(one, other)| one == other || is_placeholder(one) || is_placeholder(other))
}

#[test]
fn no_source_file_names_a_stranded_worker_path() {
    let allowlist = Allowlist::shared();
    let allowed: Vec<String> = allowlist
        .routes()
        .iter()
        .flat_map(|route| [route.path.clone(), route.axum.clone()])
        .collect();
    assert!(!allowed.is_empty(), "the compiled allowlist is empty");
    let exempt: BTreeSet<&str> = NOT_WORKER_CALLS.iter().map(|(path, _)| *path).collect();

    let root = workspace_root();
    let mut stranded: Vec<String> = Vec::new();
    let mut debt: BTreeMap<&str, usize> = BTreeMap::new();
    for file in rust_sources() {
        let Ok(text) = std::fs::read_to_string(&file) else {
            continue;
        };
        let shown = file
            .strip_prefix(&root)
            .unwrap_or(&file)
            .display()
            .to_string();
        for (line, literal) in path_literals(&text) {
            if allowed.iter().any(|route| path_matches(&literal, route)) {
                continue;
            }
            if exempt.contains(literal.as_str()) {
                continue;
            }
            if NOT_WORKER_CALLS_IN
                .iter()
                .any(|(path, file, _)| *path == literal && shown.ends_with(file))
            {
                continue;
            }
            if let Some((known, _, _)) = KNOWN_STRANDED_SEAM_PATHS
                .iter()
                .find(|(path, _, _)| *path == literal)
            {
                *debt.entry(known).or_default() += 1;
                continue;
            }
            stranded.push(format!("  rust/{shown}:{line} names {literal:?}"));
        }
    }

    assert!(
        stranded.is_empty(),
        "these source files name a worker-request path the worker does not \
         serve (it is absent from rust/fixtures/worker_allowlist.json), so the \
         call 404s in production while every fixture that never exercises the \
         branch stays green:\n{}\n\nFix the call — write through the native \
         engine — or, if the literal is a route this process mounts rather than \
         one it requests, add it to NOT_WORKER_CALLS with the reason (or to \
         NOT_WORKER_CALLS_IN, when it is only true of the one file).",
        stranded.join("\n"),
    );

    for (path, ceiling, reason) in KNOWN_STRANDED_SEAM_PATHS {
        let seen = debt.get(path).copied().unwrap_or(0);
        assert!(
            seen <= *ceiling,
            "{path} is now named {seen} times in rust/*/src, above the recorded \
             ceiling of {ceiling}. This register only shrinks: nativize the new \
             call site instead of raising the number.\nDebt: {reason}",
        );
    }
}

#[test]
fn the_review_event_vocabulary_is_one_vocabulary() {
    use lattice_retrieval::memory_api::wsos;
    assert_eq!(
        wsos::REVIEW_TIMELINE_AREA,
        lattice_platform::governance::review_queue::REVIEW_TIMELINE_AREA
    );
    assert_eq!(
        wsos::REVIEW_ITEM_CREATED_EVENT,
        lattice_platform::governance::review_queue::REVIEW_ITEM_CREATED_EVENT
    );
    assert_eq!(
        wsos::REVIEW_ITEM_UPDATED_EVENT,
        lattice_platform::governance::review_queue::REVIEW_ITEM_UPDATED_EVENT
    );
}
