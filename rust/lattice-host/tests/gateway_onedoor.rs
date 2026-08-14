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
    };
    let product = OneDoorState::open_with_config(
        config,
        &worker.origin(),
        client(),
        &agent_root,
        loop_config,
    )
    .expect("the product state must assemble over a scratch directory");

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
