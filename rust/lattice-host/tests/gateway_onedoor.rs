//! The One Door itself: boot the whole product router and prove two things.
//!
//! 1. **Every mounted family answers here.** One representative request per
//!    family — the first happy record in that family's committed HTTP fixture —
//!    reaches a native handler rather than falling through to the 404 the
//!    gateway answers for a path nobody mounted. Bodies are not compared: each
//!    family already replays its own fixtures byte-for-byte in its own crate.
//!    What *this* suite owns is the question those suites cannot ask, because
//!    they mount one router each: **is it all mounted at once, on the same
//!    paths, without a collision?**
//!
//! 2. **Nothing leaks to the worker.** The fake worker records every request it
//!    receives, and the assertion is that each one is on the committed
//!    allowlist — the product routes are answered here now, and the only things
//!    that may cross the hop are the worker's own surface plus the compute
//!    seams a request legitimately triggers (`/worker/embed` while a turn is
//!    indexed, `/worker/render/*` while a document is built, and so on).
//!
//! 3. **Nothing *names* a path the worker no longer serves.** (1) and (2) are
//!    dynamic: they fire one representative request per fixture family, so a
//!    stranded seam call on a branch no fixture reaches is invisible to them —
//!    which is exactly how two live `POST /knowledge-graph/ingest` clients
//!    survived v11.6.0. [`no_source_file_names_a_stranded_worker_path`] is the
//!    static half: it reads every `rust/*/src/**/*.rs` and refuses any
//!    worker-request path literal that is neither on the committed allowlist
//!    nor on an explicit, reasoned exemption list.
//!
//! The store, the agent workspace and the knowledge vault are all temporary.
//! That is not hygiene, it is a precondition: `GraphWriter::open` bootstraps a
//! schema and several of these routes write, so a suite that took the ambient
//! configuration would be editing the Brain of whoever ran it.

mod common;

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use common::{client, FakeWorker, FixedProvider, TestGateway};
use lattice_core::db::RuntimeConfig;
use lattice_host::gateway::allowlist::Allowlist;
use lattice_host::gateway::onedoor::OneDoorState;
use lattice_host::gateway::posture::Posture;
use lattice_host::gateway::product;
use lattice_host::gateway::GatewayState;
use serde_json::Value;

/// Calls that still cross to the worker on a path the worker no longer serves.
///
/// Empty: the three W3b leftovers now write through `GraphWriter`.
/// `intents::clear` → `ingest_event`; `garden_api::process` and
/// `browser_api::ingest_current_tab` → native ingest plus the W5 extract/embed
/// chain. A new entry is a regression.
const KNOWN_STRANDED_SEAM_CALLS: [&str; 0] = [];

/// Fixture-recorded routes that no longer exist anywhere (v11.8.0).
///
/// `(method, path, why)`. The HTTP fixtures are a frozen record of what the
/// Python surface answered while it existed, so they still carry these; the
/// product does not. Probing them below asserts the front door's **own** 404
/// rather than skipping, so "deleted" stays a checked fact: if one were
/// re-mounted, or the proxy allowlist regrew it, this fails.
const DELETED_ROUTES: &[(&str, &str, &str)] = &[(
    "GET",
    "/api/capture/voice/status",
    "the ASR capability probe. No surface ever called it, and POST /worker/asr \
     reports per call whether this machine heard anything",
)];

/// The committed HTTP fixtures, one file per work package.
const FIXTURE_FILES: [&str; 9] = [
    "workspace.json",
    "admin.json",
    "review_proposals.json",
    "mcp_ecosystem.json",
    "platform_misc.json",
    "tools_misc.json",
    "chat.json",
    "knowledge_search.json",
    "memory_brain.json",
];

/// One request to try, taken from a fixture record.
#[derive(Debug, Clone)]
struct Probe {
    family: String,
    name: String,
    method: String,
    path: String,
    query: Vec<(String, String)>,
    body: Option<Value>,
}

fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("rust/ workspace root")
        .join("fixtures")
        .join("http")
}

/// A path a probe can actually be issued for.
///
/// Fixture paths carry masking tokens (`@id`, `@ts`) where the capture had a
/// live identifier. Those records describe a *body*, not a route, and issuing
/// them would test the tokeniser rather than the mount map.
fn is_literal(path: &str) -> bool {
    path.starts_with('/') && !path.contains('@') && !path.contains('{')
}

/// The first happy record of every family in one fixture file.
fn probes_from(file: &str) -> Vec<Probe> {
    let raw = std::fs::read_to_string(fixtures_dir().join(file))
        .unwrap_or_else(|err| panic!("{file}: {err}"));
    let document: Value = serde_json::from_str(&raw).unwrap_or_else(|err| panic!("{file}: {err}"));
    let records = document
        .get("fixtures")
        .or_else(|| document.get("cases"))
        .and_then(Value::as_array)
        .unwrap_or_else(|| panic!("{file} has neither `fixtures` nor `cases`"));

    let mut first: BTreeMap<String, Probe> = BTreeMap::new();
    for record in records {
        let family = record["family"].as_str().unwrap_or("unknown").to_string();
        if first.contains_key(&family) {
            continue;
        }
        // The platform captures label the branch; the retrieval ones do not, and
        // there "happy" is the 2xx/3xx answer.
        let happy = match record.get("branch").and_then(Value::as_str) {
            Some(branch) => branch == "happy",
            None => (200..400).contains(&record["status"].as_u64().unwrap_or(0)),
        };
        let path = record["path"].as_str().unwrap_or_default();
        if !happy || !is_literal(path) {
            continue;
        }
        first.insert(
            family.clone(),
            Probe {
                family,
                name: record["name"].as_str().unwrap_or("unnamed").to_string(),
                method: record["method"].as_str().unwrap_or("GET").to_string(),
                path: path.to_string(),
                query: record["query"]
                    .as_object()
                    .map(|map| {
                        map.iter()
                            .map(|(key, value)| {
                                (
                                    key.clone(),
                                    value.as_str().map(str::to_string).unwrap_or_else(|| {
                                        value.to_string().trim_matches('"').to_string()
                                    }),
                                )
                            })
                            .collect()
                    })
                    .unwrap_or_default(),
                body: record
                    .get("request_body")
                    .filter(|body| !body.is_null())
                    .cloned(),
            },
        );
    }
    first.into_values().collect()
}

/// A gateway with the whole product mounted, over throwaway state.
async fn one_door(worker: &FakeWorker, name: &str) -> (TestGateway, PathBuf) {
    let scratch = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join("onedoor")
        .join(name);
    let _ = std::fs::remove_dir_all(&scratch);
    let data_dir = scratch.join("data");
    let agent_root = scratch.join("agent_workspace");
    let brain_dir = scratch.join("brain");
    for dir in [&data_dir, &agent_root, &brain_dir] {
        std::fs::create_dir_all(dir).expect("scratch");
    }
    // `default_brain_dir()` reads the environment, and its fallback is
    // `~/.ltcai-brain` — the user's real knowledge vault.
    std::env::set_var("LATTICEAI_BRAIN_DIR", &brain_dir);

    let config = RuntimeConfig::resolve(
        Some(&data_dir.to_string_lossy()),
        None,
        Some(&worker.origin()),
        None,
    );
    let loop_config = lattice_agent::LoopConfig {
        worker_origin: worker.origin(),
        runs_dir: scratch.join("rust_agent_runs"),
        client: Some(client()),
        // Scratch for the same reason `LATTICEAI_BRAIN_DIR` is: the loop's
        // default proposal store is `$HOME/.ltcai/workspace_os.json`, which is
        // the Review Center of whoever runs this suite.
        proposals: Some(std::sync::Arc::new(
            lattice_agent::proposals::JsonProposalStore::new(scratch.join("proposals")),
        )),
        // `OneDoorState` binds the sink over the registry it opens under
        // `data_dir`, which is scratch here for the same reason.
        hooks: None,
    };
    let product = OneDoorState::open_with_config(
        config,
        &worker.origin(),
        client(),
        &agent_root,
        loop_config,
    )
    .expect("the product state must assemble over a scratch directory");
    // v11.7.0 F-BC: the product binds the hooks registry into the loop, so a
    // user `pre_tool` hook fires for a native tool. Unbound, every hook in
    // `hooks.json` would be silently inert for everything the loop writes.
    assert!(
        product.loop_config.hooks.is_some(),
        "the agent loop must reach the hooks registry the product mounted"
    );

    let state = GatewayState::new(Arc::new(FixedProvider::new(worker.origin(), worker.port())))
        .expect("gateway state")
        .with_db_path(data_dir.join("knowledge_graph.sqlite"))
        .with_agent_root(&agent_root)
        .with_agent_runs_dir(scratch.join("rust_agent_runs"))
        .with_pinned_posture(Posture::Open)
        .with_product(Arc::new(product));
    (TestGateway::start_with_state(state).await, scratch)
}

/// The body the gateway answers for a path nothing mounted.
async fn is_unmounted(response: reqwest::Response) -> bool {
    if response.status() != reqwest::StatusCode::NOT_FOUND {
        return false;
    }
    let text = response.text().await.unwrap_or_default();
    serde_json::from_str::<Value>(&text)
        .map(|body| body == serde_json::json!({"detail": "Not Found"}))
        .unwrap_or(false)
}

#[tokio::test]
async fn every_mounted_family_answers_and_nothing_leaks_to_the_worker() {
    let worker = FakeWorker::start().await;
    let (gateway, _scratch) = one_door(&worker, "families").await;
    let http = client();

    let mut probed = 0usize;
    let mut families: BTreeSet<String> = BTreeSet::new();
    for file in FIXTURE_FILES {
        for probe in probes_from(file) {
            let method = reqwest::Method::from_bytes(probe.method.as_bytes())
                .unwrap_or(reqwest::Method::GET);
            let mut request = http.request(method, gateway.url(&probe.path));
            if !probe.query.is_empty() {
                request = request.query(&probe.query);
            }
            if let Some(body) = &probe.body {
                request = request
                    .header("content-type", "application/json")
                    .body(serde_json::to_vec(body).expect("json"));
            }
            let response = request.send().await.unwrap_or_else(|err| {
                panic!(
                    "{file} {} {} {}: {err}",
                    probe.family, probe.method, probe.path
                )
            });
            let status = response.status();
            if let Some((_, _, why)) = DELETED_ROUTES
                .iter()
                .find(|(method, path, _)| *method == probe.method && *path == probe.path)
            {
                assert!(
                    is_unmounted(response).await,
                    "{} {} was deleted ({why}) — the front door must answer its \
                     own 404, not mount it and not forward it",
                    probe.method,
                    probe.path,
                );
                continue;
            }
            assert!(
                !is_unmounted(response).await,
                "{file} · {} · {} — {} {} is not mounted on the One Door router \
                 (the gateway answered its own 404). Either the family's router \
                 was never merged, or the path moved.",
                probe.family,
                probe.name,
                probe.method,
                probe.path,
            );
            // A refusal is fine — an empty scratch Brain answers plenty of them.
            // What must not happen is "nobody is home".
            assert_ne!(
                status,
                reqwest::StatusCode::METHOD_NOT_ALLOWED,
                "{} {} is mounted for another method only",
                probe.method,
                probe.path
            );
            families.insert(format!("{file}:{}", probe.family));
            probed += 1;
        }
    }

    assert!(
        probed >= 20,
        "only {probed} families were probed; the fixture reader found almost \
         nothing, which is a broken test rather than a passing one"
    );
    assert_eq!(families.len(), probed);

    let allowlist = Allowlist::shared();
    let leaked: BTreeSet<String> = worker
        .requests()
        .iter()
        .filter(|request| {
            let method = reqwest::Method::from_bytes(request.method.as_bytes())
                .unwrap_or(reqwest::Method::GET);
            !allowlist.allows(&method, request.path())
        })
        .map(|request| format!("{} {}", request.method, request.path()))
        .collect();
    assert_eq!(
        leaked,
        KNOWN_STRANDED_SEAM_CALLS
            .iter()
            .map(|call| call.to_string())
            .collect::<BTreeSet<String>>(),
        "the set of requests that cross to the worker on a path it no longer \
         serves must be exactly the documented list — a new entry is a \
         regression, and a missing one means a fix landed and this list should \
         shrink with it"
    );

    gateway.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn the_front_door_serves_the_shell_and_refuses_what_nobody_owns() {
    let worker = FakeWorker::start().await;
    let (gateway, _scratch) = one_door(&worker, "shell").await;
    let http = common::client_no_redirect();

    // The SPA bookmark redirects are the shell's, and they carry the query.
    let redirect = http
        .get(gateway.url("/chat?ref=bookmark"))
        .send()
        .await
        .expect("chat redirect");
    assert_eq!(redirect.status(), 308);
    assert_eq!(
        redirect
            .headers()
            .get("location")
            .and_then(|value| value.to_str().ok()),
        Some("/app#/chat?ref=bookmark"),
    );

    // `GET /agents` is one path two crates could have claimed; exactly one does.
    let agents = http
        .get(gateway.url("/agents"))
        .send()
        .await
        .expect("agents redirect");
    assert_eq!(agents.status(), 308);
    assert_eq!(
        agents
            .headers()
            .get("location")
            .and_then(|value| value.to_str().ok()),
        Some("/app#/agents"),
    );

    // …and `GET /plugins/sdk`, the one page redirect the contract gives to a
    // feature family, is served by that family.
    let sdk = http
        .get(gateway.url("/plugins/sdk"))
        .send()
        .await
        .expect("plugins sdk");
    assert_eq!(sdk.status(), 308);

    // A path neither the gateway nor the worker owns.
    let nowhere = http
        .get(gateway.url("/definitely-not-a-route"))
        .send()
        .await
        .expect("unknown");
    assert!(is_unmounted(nowhere).await);
    assert_eq!(
        worker
            .requests()
            .iter()
            .filter(|request| request.path() == "/definitely-not-a-route")
            .count(),
        0,
        "an off-allowlist path must never reach the worker"
    );

    gateway.stop().await;
    worker.shutdown();
}

#[test]
fn the_mount_table_declares_every_family_exactly_once() {
    // The same property `product::mount_table`'s own unit test asserts, restated
    // here because this is the suite a reader opens when a route goes missing.
    let mut seen: BTreeSet<(&str, &str)> = BTreeSet::new();
    for (_, rows) in product::mount_table() {
        for (method, path) in rows {
            assert!(seen.insert((method, path)), "{method} {path} twice");
        }
    }
    assert_eq!(seen.len(), product::mounted_route_count());
}

#[test]
fn the_fixture_reader_finds_a_family_in_every_committed_file() {
    for file in FIXTURE_FILES {
        let probes = probes_from(file);
        assert!(!probes.is_empty(), "{file} produced no probe at all");
        for probe in &probes {
            assert!(Path::new(&probe.path).is_absolute(), "{:?}", probe.path);
        }
    }
}

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
/// proposals) and cannot name `lattice_platform::review_queue` — the
/// dependency runs the other way — so it declares the three constants again.
/// This host is the one crate that can see both spellings, which makes it the
/// only place the claim "they are the same" can be checked rather than
/// asserted in a comment.
#[test]
fn the_review_event_vocabulary_is_one_vocabulary() {
    use lattice_retrieval::memory_api::wsos;
    assert_eq!(
        wsos::REVIEW_TIMELINE_AREA,
        lattice_platform::review_queue::REVIEW_TIMELINE_AREA
    );
    assert_eq!(
        wsos::REVIEW_ITEM_CREATED_EVENT,
        lattice_platform::review_queue::REVIEW_ITEM_CREATED_EVENT
    );
    assert_eq!(
        wsos::REVIEW_ITEM_UPDATED_EVENT,
        lattice_platform::review_queue::REVIEW_ITEM_UPDATED_EVENT
    );
}
