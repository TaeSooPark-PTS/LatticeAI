//! Capture-replay parity for the static/UI family (WP-I4).
//!
//! Every case in `rust/fixtures/http/static_ui.json` was recorded against the
//! live Python router; every case is replayed here against the Rust one, over
//! real HTTP, with the same static tree and the same invite posture. A
//! difference in status, in any pinned header, or in one byte of the body fails.
//!
//! The suite is split by install rather than by route, because the install *is*
//! the branch: "the build output is missing" and "the gate is armed" are not
//! variations on a request, they are different machines.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod static_ui_harness;

use lattice_platform::static_ui::{
    asset_content_type, sign_invite_cookie, verify_invite_cookie, INVITE_DENIED_HTML,
    PRODUCTION_CSP,
};
use serde_json::Value;
use static_ui_harness::{cases_for, fixture, sha256, Install};

#[tokio::test]
async fn the_default_install_answers_as_python_answered() {
    let install = Install::start("gate_off").await;
    install.replay_all("gate_off").await;
}

#[tokio::test]
async fn an_install_without_build_output_answers_as_python_answered() {
    let install = Install::start("shell_missing").await;
    install.replay_all("shell_missing").await;
}

#[tokio::test]
async fn an_install_without_icons_answers_as_python_answered() {
    let install = Install::start("iconless").await;
    install.replay_all("iconless").await;
}

#[tokio::test]
async fn an_invitation_only_install_answers_as_python_answered() {
    let install = Install::start("gate_on").await;
    install.replay_all("gate_on").await;
}

#[tokio::test]
async fn a_reachable_invitation_only_install_marks_its_cookie_secure() {
    let install = Install::start("gate_on_secure").await;
    install.replay_all("gate_on_secure").await;
}

/// The wall is a literal in two languages' source files; only a digest can say
/// they are the same wall.
#[test]
fn the_invitation_wall_is_byte_identical() {
    let recorded = cases_for("gate_on")
        .into_iter()
        .find(|case| case["name"] == "gate_root_denied")
        .expect("the denied case");
    assert_eq!(
        sha256(INVITE_DENIED_HTML.as_bytes()),
        recorded["body_sha256"].as_str().expect("digest"),
        "the invitation wall drifted from the Python literal"
    );
    assert_eq!(
        INVITE_DENIED_HTML.len(),
        recorded["body_bytes"].as_u64().expect("length") as usize
    );
}

/// The CSP is one long string that nobody reads and every browser enforces.
#[test]
fn the_production_csp_is_the_recorded_one() {
    let recorded = cases_for("gate_off")
        .into_iter()
        .find(|case| case["name"] == "app_shell")
        .expect("the shell case");
    assert_eq!(
        recorded["headers"]["content-security-policy"]
            .as_str()
            .expect("csp"),
        PRODUCTION_CSP
    );
}

/// A cookie Python signed, verified by Rust — the point of the exercise. If the
/// two HMACs ever disagree, every browser holding a live invitation is locked
/// out at the moment of the cutover, which is not something to discover in
/// production.
#[test]
fn python_signed_cookies_verify_here_and_vice_versa() {
    let invite = &fixture()["invite"];
    let secret = invite["secret"].as_str().expect("secret");
    let signed = invite["signed_cookie"].as_str().expect("cookie");
    let frozen = invite["frozen_now"].as_i64().expect("now");
    let nonce = invite["frozen_nonce"].as_str().expect("nonce");
    let ttl = invite["ttl_seconds"].as_i64().expect("ttl");

    assert!(verify_invite_cookie(Some(signed), secret, frozen));
    assert_eq!(
        sign_invite_cookie(secret, frozen + ttl, nonce),
        signed,
        "the Rust signature is not the Python one"
    );
}

/// Every accept/reject the Python verifier makes, made again here.
#[test]
fn the_invite_verifier_agrees_branch_for_branch() {
    let invite = &fixture()["invite"];
    let default_secret = invite["secret"].as_str().expect("secret");
    for vector in invite["vectors"].as_array().expect("vectors") {
        let value = vector["value"].as_str().expect("value");
        let secret = vector
            .get("secret")
            .and_then(Value::as_str)
            .unwrap_or(default_secret);
        let now = vector["now"].as_i64().expect("now");
        assert_eq!(
            verify_invite_cookie(Some(value), secret, now),
            vector["valid"].as_bool().expect("verdict"),
            "vector: {}",
            vector["why"]
        );
    }
    assert!(
        !verify_invite_cookie(None, default_secret, 0),
        "no cookie at all"
    );
}

/// The content-type table, against what Starlette actually served.
///
/// Two extensions are machine-dependent in Python — CPython's `mimetypes` also
/// reads the system's `mime.types` — so they are asserted against the choice the
/// port documents rather than against the recording.
#[test]
fn the_content_type_table_matches_the_recording() {
    let mut machine_dependent = 0;
    for row in fixture()["mimetypes"]["rows"].as_array().expect("rows") {
        let extension = row["extension"].as_str().expect("extension");
        let served = row["served"].as_str().expect("served");
        let ours = asset_content_type(&format!("probe{extension}"));
        let live = row["live_guess"].as_str();
        let builtin = row["builtin_guess"].as_str();
        if live != builtin {
            machine_dependent += 1;
            assert_eq!(
                ours, served,
                "{extension}: the port follows this machine's answer"
            );
            continue;
        }
        assert_eq!(ours, served, "{extension}");
    }
    assert_eq!(
        machine_dependent, 2,
        "the recording knows of exactly two machine-dependent extensions (.ico, .xml)"
    );
}

/// The tree the goldens were recorded against is the tree they are replayed
/// against — a fixture that carries its own inputs cannot drift from them.
#[test]
fn the_recorded_tree_carries_its_own_digests() {
    use base64::engine::general_purpose::STANDARD as BASE64;
    use base64::Engine;

    let tree = fixture()["tree"].as_object().expect("tree");
    assert!(
        tree.contains_key("app/index.html"),
        "the SPA shell is the point"
    );
    for (path, entry) in tree {
        let bytes = BASE64
            .decode(entry["b64"].as_str().expect("b64"))
            .expect("base64");
        assert_eq!(
            sha256(&bytes),
            entry["sha256"].as_str().expect("digest"),
            "{path}"
        );
        assert_eq!(
            bytes.len(),
            entry["bytes"].as_u64().expect("length") as usize,
            "{path}"
        );
    }
}

/// Nothing in the recording is left unreplayed: a case added to the fixture and
/// not to a suite would otherwise pass by being ignored.
#[test]
fn every_recorded_case_belongs_to_a_replayed_install() {
    let replayed = [
        "gate_off",
        "shell_missing",
        "iconless",
        "gate_on",
        "gate_on_secure",
    ];
    let configs = fixture()["configs"].as_object().expect("configs");
    for config in configs.keys() {
        assert!(replayed.contains(&config.as_str()), "{config} has no suite");
    }
    for case in fixture()["cases"].as_array().expect("cases") {
        let config = case["config"].as_str().expect("config");
        assert!(
            configs.contains_key(config),
            "{} names an install the fixture does not describe",
            case["name"]
        );
    }
    assert_eq!(
        fixture()["cases"].as_array().expect("cases").len(),
        replayed
            .iter()
            .map(|config| cases_for(config).len())
            .sum::<usize>()
    );
}
