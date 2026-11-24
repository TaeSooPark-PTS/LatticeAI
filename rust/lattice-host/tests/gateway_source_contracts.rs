//! Static source-level contracts for worker seam paths and review event vocabulary.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use lattice_host::gateway::allowlist::Allowlist;

// ── the static gate: no source file may name a stranded worker path ─────────
//
// The dynamic leak assertion above can only see the calls a probe happens to
// reach. This half needs no request at all: it reads the source. A path literal
// that *looks* like a worker request must be on the committed allowlist, on
// [`NOT_WORKER_CALLS`] (with a reason), or on [`KNOWN_STRANDED_SEAM_PATHS`]
// (named debt, capped so it can shrink but never grow).
//
// **The debt register is empty as of v11.7.0** and the gate is at its strongest
// in that state: with nothing exempt, any new stranded path fails the build.

/// Path literals that look like worker requests and are not.
///
/// `(literal, why it never crosses the hop)`. Everything here is a route this
/// process mounts, a table describing one, or an assertion that a path is
/// *refused*: the front door serving `/agent/eval` is the opposite of the front
/// door asking a worker for it, and a test proving `/worker/graph/mutate` is
/// never forwarded is the opposite of a call to it.
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

/// Exemptions that are true of **one file**, as `(path, file suffix, why)`.
///
/// [`NOT_WORKER_CALLS`] licenses a literal everywhere, which is right for a
/// route this process mounts — any file may name `/agent/eval`. It is wrong for
/// a path that is dead: licensing `/worker/graph/mutate` globally would let the
/// next call site walk straight back in behind an exemption written for a test
/// assertion. So this list carries the file too, and the file has to match.
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

/// Seam calls that still name a path the worker stopped serving.
///
/// `(path, occurrence ceiling in rust/*/src, what is stranded)`. This is a debt
/// register, not an exemption: the ceiling only ever moves **down**, so a fix
/// passes and a new call site fails.
///
/// **It is empty.** v11.6.0 left two entries, both closed in v11.7.0 (§F-G):
///
/// * `/worker/graph/mutate` ×9 — the WP-I6 graph-write seam, retired with the
///   Python write door in v11.6.0. Five call sites in `lattice-platform`, two
///   in `lattice-retrieval` and one in `lattice-ingest` were the `else` arm of
///   a native `GraphWriter` branch, so a wired install never took them; the
///   ninth, `memory_api::shared`, was the *only* path for the Self-Model's four
///   writes and the contradiction stamps, which therefore answered 404 on every
///   live install. All are native now (`graph_native::dispatch` and
///   `memory_api::self_model_write`), and the fallbacks are deleted rather than
///   left as plausible-looking options.
/// * `/tools/create_xlsx` ×1 — `security_dashboard::export` posted the
///   spreadsheet build to a **product** route this process mounts itself, so
///   the xlsx export 502'd. It goes through `/worker/render/xlsx` now, which is
///   on `rust/fixtures/worker_allowlist.json`.
const KNOWN_STRANDED_SEAM_PATHS: &[(&str, usize, &str)] = &[];

/// The call helpers whose first argument is a worker path.
const SEAM_CALLS: [&str; 3] = ["post_json(", "get_json(", "stream_sse("];

/// `rust/` — the workspace root both this crate and every scanned crate sit in.
fn workspace_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("rust/ workspace root")
        .to_path_buf()
}

/// Every `rust/*/src/**/*.rs`, sorted so a failure reads the same twice.
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

/// Every double-quoted literal on one line, escapes unescaped.
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

/// The path literals one file is answerable for, as `(line, literal)`.
///
/// Three rules, because one is not enough:
///
/// 1. **Prefix.** Anything starting `/worker/` or `/agent/` is the worker's
///    namespace by construction, wherever it is written.
/// 2. **Named constant.** `const X: &str = "/…";` is how a seam path is spelled
///    when the call site is far from the declaration — which is precisely how
///    `INGEST_PATH` hid a dead route in two crates.
/// 3. **Call argument.** The first literal handed to `post_json` / `get_json` /
///    `stream_sse`, matched across newlines so rustfmt's wrapping cannot hide it.
///
/// Whole-line comments are skipped: prose naming a path is documentation, and
/// this module's own header would otherwise fail its own gate.
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
                    // The literal's own line, not the call's: rustfmt wraps a
                    // long call, and two reports for one call would double-count
                    // it against the debt ceiling.
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

/// Whether a literal could be an HTTP path at all.
///
/// A leading `/` is not enough: `"// braces in comments { … }"` is a fixture
/// for a code sanitiser, not a route. A path has one leading slash and no
/// whitespace in it.
fn is_pathish(literal: &str) -> bool {
    literal.len() > 1
        && literal.starts_with('/')
        && !literal.starts_with("//")
        && !literal.contains(char::is_whitespace)
}

/// A parameter segment in either FastAPI's spelling or axum's.
fn is_placeholder(segment: &str) -> bool {
    segment.starts_with('{') || segment.starts_with(':') || segment.starts_with('*')
}

/// Whether a literal names an allowlisted route, template parameters included.
///
/// `"/worker/render/{kind}"` is the same route as the fixture's
/// `/worker/render/docx`; a segment that is a parameter on either side matches.
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

/// The review-event vocabulary is one vocabulary, spelled in two crates.
///
/// `lattice-retrieval` writes review items too (synthesis and Self-Model
/// proposals) and cannot name `lattice_platform::governance::review_queue` — the
/// dependency runs the other way — so it declares the three constants again.
/// This host is the one crate that can see both spellings, which makes it the
/// only place the claim "they are the same" can be checked rather than
/// asserted in a comment.
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
