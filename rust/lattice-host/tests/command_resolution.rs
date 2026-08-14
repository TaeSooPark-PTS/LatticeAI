//! Worker command resolution: the three-rule chain and its priority order.
//!
//! Lives outside `src/supervisor/command.rs` so that file stays under the
//! 500-line target; every symbol used here is public API.

use std::path::PathBuf;

use lattice_host::supervisor::command::{dedup_preserving_order, find_in_path, python_candidates};
use lattice_host::supervisor::{resolve_worker_command, CommandOrigin, ResolveError, StaticProbe};

fn probe_with_python_everywhere() -> StaticProbe {
    StaticProbe::new()
        .with_env("LTCAI_PYTHON", "/zzz/venv/bin/python")
        .with_path_dir("/aaa/bin")
        .with_file("/aaa/bin/python3")
        .with_file("/usr/bin/python3")
        .with_file("/zzz/venv/bin/python")
}

#[test]
fn python_candidates_keep_declaration_order() {
    let probe = probe_with_python_everywhere();
    let candidates = python_candidates(&probe);
    // The desktop shell sorted this list, which put "/aaa/bin/python3"
    // ahead of the explicitly configured interpreter. Order is priority.
    assert_eq!(candidates[0], "/zzz/venv/bin/python");
    assert_eq!(candidates[1], "/aaa/bin/python3");
    assert!(candidates.iter().any(|c| c == "/usr/bin/python3"));
    let sorted = {
        let mut copy = candidates.clone();
        copy.sort();
        copy
    };
    assert_ne!(
        candidates, sorted,
        "test fixture must be able to tell sorted from unsorted"
    );
}

#[test]
fn python_candidates_dedup_without_reordering() {
    // /usr/bin is on PATH *and* in the absolute fallback list, so
    // /usr/bin/python3 is produced twice; it must survive once, at the
    // earlier (PATH-derived) position.
    let probe = StaticProbe::new()
        .with_path_dir("/usr/bin")
        .with_file("/usr/bin/python3");
    let candidates = python_candidates(&probe);
    assert_eq!(candidates[0], "/usr/bin/python3");
    assert_eq!(
        candidates
            .iter()
            .filter(|c| *c == "/usr/bin/python3")
            .count(),
        1
    );
    assert_eq!(candidates[1], "/opt/homebrew/bin/python3");
}

#[test]
fn dedup_preserving_order_is_stable() {
    let input = vec!["c", "a", "c", "b", "a"];
    assert_eq!(dedup_preserving_order(input), vec!["c", "a", "b"]);
}

#[test]
fn rule_one_env_override_wins_over_everything() {
    let probe = probe_with_python_everywhere()
        .with_env("LATTICEAI_DESKTOP_BACKEND_CMD", "/opt/ltcai serve --fast")
        .with_env("LATTICEAI_DESKTOP_BACKEND_CWD", "/opt/work")
        .with_file("/aaa/bin/ltcai")
        .with_importable("/zzz/venv/bin/python");
    let cmd = resolve_worker_command(&probe, 4825).expect("resolves");
    assert_eq!(cmd.origin, CommandOrigin::EnvOverride);
    assert_eq!(cmd.program, "/opt/ltcai");
    assert_eq!(cmd.args, vec!["serve", "--fast"]);
    assert_eq!(cmd.cwd, Some(PathBuf::from("/opt/work")));
    assert_eq!(cmd.display(), "/opt/ltcai serve --fast");
}

#[test]
fn rule_one_blank_command_falls_through() {
    let probe = StaticProbe::new()
        .with_path_dir("/aaa/bin")
        .with_env("LATTICEAI_DESKTOP_BACKEND_CMD", "   ")
        .with_file("/aaa/bin/python3")
        .with_importable("/aaa/bin/python3");
    let cmd = resolve_worker_command(&probe, 4825).expect("resolves");
    assert_eq!(cmd.origin, CommandOrigin::PythonModule);
}

/// v11.6.0 §3: `ltcai` starts the **host**, so a host that spawned it as its
/// worker would spawn another host, and that one another. The rule is gone;
/// an `ltcai` on PATH is now simply not a worker.
#[test]
fn an_ltcai_on_path_is_no_longer_a_worker() {
    let probe = StaticProbe::new()
        .with_path_dir("/aaa/bin")
        .with_file("/aaa/bin/LTCAI")
        .with_file("/aaa/bin/ltcai");
    assert_eq!(
        resolve_worker_command(&probe, 4899).expect_err("no worker"),
        ResolveError::NotFound,
        "an ltcai on PATH must not be mistaken for a worker — it is this binary"
    );
    // …and with a python beside it, the python wins rather than tying.
    let probe = probe
        .with_file("/aaa/bin/python3")
        .with_importable("/aaa/bin/python3");
    let cmd = resolve_worker_command(&probe, 4899).expect("resolves");
    assert_eq!(cmd.origin, CommandOrigin::PythonModule);
    assert_eq!(cmd.program, "/aaa/bin/python3");
}

#[test]
fn rule_two_picks_the_first_importable_candidate_not_the_first_sorted() {
    // Both interpreters can import the module. Priority order must pick
    // LTCAI_PYTHON even though "/aaa/bin/python3" sorts first.
    let probe = probe_with_python_everywhere()
        .with_importable("/zzz/venv/bin/python")
        .with_importable("/aaa/bin/python3");
    let cmd = resolve_worker_command(&probe, 4825).expect("resolves");
    assert_eq!(cmd.origin, CommandOrigin::PythonModule);
    assert_eq!(cmd.program, "/zzz/venv/bin/python");
    assert_eq!(
        cmd.display(),
        "/zzz/venv/bin/python -m uvicorn latticeai.worker_app:create_worker_app --factory --host 127.0.0.1 --port 4825",
        "the worker profile is served through uvicorn: `python -m latticeai.worker_app` has no __main__ guard and would exit without binding"
    );
}

#[test]
fn rule_two_skips_candidates_that_cannot_import() {
    let probe = probe_with_python_everywhere().with_importable("/usr/bin/python3");
    let cmd = resolve_worker_command(&probe, 4825).expect("resolves");
    assert_eq!(cmd.program, "/usr/bin/python3");
}

#[test]
fn rule_three_uses_the_bundled_tree_with_an_existing_interpreter() {
    let probe = StaticProbe::new()
        .with_resource_dir("/App/Contents/Resources")
        .with_file("/App/Contents/Resources/_up_/latticeai/worker_app.py")
        .with_file("/usr/bin/python3");
    let cmd = resolve_worker_command(&probe, 4825).expect("resolves");
    assert_eq!(cmd.origin, CommandOrigin::BundledTree);
    assert_eq!(cmd.program, "/usr/bin/python3");
    assert_eq!(cmd.cwd, Some(PathBuf::from("/App/Contents/Resources/_up_")));
    assert_eq!(cmd.python_root, cmd.cwd);
}

#[test]
fn rule_three_accepts_resources_root_without_up_dir() {
    let probe = StaticProbe::new()
        .with_resource_dir("/App/Contents/Resources")
        .with_file("/App/Contents/Resources/latticeai/worker_app.py")
        .with_file("/usr/bin/python3");
    let cmd = resolve_worker_command(&probe, 4825).expect("resolves");
    assert_eq!(cmd.cwd, Some(PathBuf::from("/App/Contents/Resources")));
}

/// A bundle built before the flip still boots (WP-P2 §checklist 3).
#[test]
fn rule_three_still_accepts_a_pre_v11_6_bundle() {
    let probe = StaticProbe::new()
        .with_resource_dir("/App/Contents/Resources")
        .with_file("/App/Contents/Resources/latticeai/cli/entrypoint.py")
        .with_file("/usr/bin/python3");
    let cmd = resolve_worker_command(&probe, 4825).expect("resolves");
    assert_eq!(cmd.origin, CommandOrigin::BundledTree);
}

#[test]
fn nothing_found_is_an_error_not_a_bogus_command() {
    let err = resolve_worker_command(&StaticProbe::new(), 4825).expect_err("no worker");
    assert_eq!(err, ResolveError::NotFound);
    assert!(err.to_string().contains("latticeai.worker_app"));
}

#[test]
fn command_origin_names_are_stable() {
    assert_eq!(CommandOrigin::EnvOverride.to_string(), "env_override");
    assert_eq!(CommandOrigin::LtcaiOnPath.as_str(), "ltcai_on_path");
    assert_eq!(CommandOrigin::PythonModule.as_str(), "python_module");
    assert_eq!(CommandOrigin::BundledTree.as_str(), "bundled_tree");
}

#[test]
fn find_in_path_appends_gui_fallback_dirs() {
    let probe = StaticProbe::new().with_file("/opt/homebrew/bin/ltcai");
    assert_eq!(
        find_in_path(&probe, "ltcai").as_deref(),
        Some("/opt/homebrew/bin/ltcai")
    );
}
