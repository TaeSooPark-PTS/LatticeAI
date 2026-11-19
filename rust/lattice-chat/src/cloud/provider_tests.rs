use super::*;

fn write_json(dir: &Path, name: &str, value: Value) {
    std::fs::write(dir.join(name), value.to_string()).unwrap();
}

fn empty_input() -> ResolveInput {
    ResolveInput {
        api_key: None,
        base_url: None,
        model: None,
        path_dirs: vec![PathBuf::from("/no/such/bin/dir")],
    }
}

#[test]
fn file_api_key_wins_over_env_and_cli() {
    let dir = tempfile::tempdir().unwrap();
    write_json(
        dir.path(),
        CLOUD_PROVIDER_FILE,
        json!({
            "mode": "api_key",
            "provider": "openai_compatible",
            "model": "file-model",
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": "from-file",
        }),
    );
    let input = ResolveInput {
        api_key: Some("from-env".into()),
        path_dirs: vec![PathBuf::from("/no/such")],
        ..ResolveInput::default()
    };
    let provider = CloudProvider::resolve_with(dir.path(), &input).unwrap();
    assert_eq!(provider.mode(), CloudMode::ApiKey);
    assert_eq!(provider.model(), "file-model");
    assert_eq!(provider.name(), "openai_compatible");
    assert!(provider.configured());
}

#[test]
fn env_key_wins_over_cli_when_no_file() {
    let dir = tempfile::tempdir().unwrap();
    let input = ResolveInput {
        api_key: Some("env-key".into()),
        model: Some("env-model".into()),
        path_dirs: vec![PathBuf::from("/no/such")],
        ..ResolveInput::default()
    };
    let provider = CloudProvider::resolve_with(dir.path(), &input).unwrap();
    assert_eq!(provider.mode(), CloudMode::ApiKey);
    assert_eq!(provider.model(), "env-model");
    assert_eq!(provider.name(), "openai_compatible");
}

#[test]
fn agy_on_path_is_gemini_cli_oauth() {
    let dir = tempfile::tempdir().unwrap();
    let bin_dir = dir.path().join("bin");
    std::fs::create_dir_all(&bin_dir).unwrap();
    let agy = bin_dir.join("agy");
    std::fs::write(&agy, "#!/bin/sh\necho agy\n").unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&agy, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    let provider = CloudProvider::resolve_with(
        dir.path(),
        &ResolveInput {
            path_dirs: vec![bin_dir],
            ..ResolveInput::default()
        },
    )
    .unwrap();
    assert_eq!(provider.mode(), CloudMode::CliOauth);
    assert_eq!(provider.name(), "gemini");
    assert_eq!(provider.model(), AGY_DEFAULT_MODEL);
}

#[test]
fn grok_is_chosen_when_agy_is_absent() {
    let dir = tempfile::tempdir().unwrap();
    let bin_dir = dir.path().join("bin");
    std::fs::create_dir_all(&bin_dir).unwrap();
    let grok = bin_dir.join("grok");
    std::fs::write(&grok, "#!/bin/sh\necho grok\n").unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&grok, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    let provider = CloudProvider::resolve_with(
        dir.path(),
        &ResolveInput {
            path_dirs: vec![bin_dir],
            ..ResolveInput::default()
        },
    )
    .unwrap();
    assert_eq!(provider.name(), "xai");
    assert_eq!(provider.model(), GROK_DEFAULT_MODEL);
    assert_eq!(provider.mode(), CloudMode::CliOauth);
}

#[test]
fn nothing_configured_is_none() {
    let dir = tempfile::tempdir().unwrap();
    assert!(CloudProvider::resolve_with(dir.path(), &empty_input()).is_none());
}

#[test]
fn compose_prompt_joins_system_context_and_question() {
    let text = compose_prompt(
        crate::cloud::HYBRID_SYSTEM_PROMPT,
        "수도는?",
        "- [Note] 서울",
    );
    assert!(text.starts_with("You are assisting"));
    assert!(text.contains("Local Knowledge Graph context"));
    assert!(text.contains("서울"));
    assert!(text.ends_with("수도는?"));
    let bare = compose_prompt("sys", "q", "");
    assert_eq!(bare, "sys\n\nq");
}

#[tokio::test]
async fn a_fake_cli_yields_its_stdout() {
    let dir = tempfile::tempdir().unwrap();
    let script = dir.path().join("fake-cli");
    std::fs::write(&script, "#!/bin/sh\necho canned-from-cli\n").unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    let provider = CloudProvider::cli_oauth(
        &script,
        vec!["-p".into(), "{prompt}".into()],
        "gemini",
        AGY_DEFAULT_MODEL,
        Duration::from_secs(5),
    );
    let mut pieces = Vec::new();
    provider
        .stream("sys", "q", "", &mut |piece| {
            pieces.push(piece.to_string());
            true
        })
        .await
        .unwrap();
    assert_eq!(pieces, ["canned-from-cli"]);
}

#[tokio::test]
async fn a_hanging_cli_is_killed_and_reported() {
    let dir = tempfile::tempdir().unwrap();
    let script = dir.path().join("hang");
    std::fs::write(&script, "#!/bin/sh\nsleep 30\n").unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    let provider = CloudProvider::cli_oauth(
        &script,
        vec!["-p".into(), "{prompt}".into()],
        "gemini",
        AGY_DEFAULT_MODEL,
        Duration::from_millis(200),
    );
    let error = provider
        .stream("s", "u", "", &mut |_| true)
        .await
        .unwrap_err();
    assert!(error.contains("timed out"), "{error}");
}

#[tokio::test]
async fn a_nonzero_cli_exit_is_an_honest_error() {
    let dir = tempfile::tempdir().unwrap();
    let script = dir.path().join("fail");
    std::fs::write(&script, "#!/bin/sh\necho boom >&2\nexit 3\n").unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    let provider = CloudProvider::cli_oauth(
        &script,
        vec!["-p".into(), "{prompt}".into()],
        "xai",
        GROK_DEFAULT_MODEL,
        Duration::from_secs(5),
    );
    let error = provider
        .stream("s", "u", "", &mut |_| true)
        .await
        .unwrap_err();
    assert!(error.contains("exited 3"), "{error}");
    assert!(error.contains("boom"), "{error}");
}

#[test]
fn status_none_matches_the_ui_contract() {
    let value = CloudStatus::none().to_value();
    assert_eq!(value["configured"], false);
    assert_eq!(value["mode"], "none");
    assert!(value["provider"].is_null());
    assert!(value["model"].is_null());
    assert!(value["detail"].is_null());
}
