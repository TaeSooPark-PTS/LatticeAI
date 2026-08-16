//! Dual-mode cloud credentials: an API key, or a locally-authenticated CLI.
//!
//! Resolution order is the product contract:
//!
//! 1. `<data_dir>/cloud_provider.json`
//! 2. `LATTICEAI_CLOUD_API_KEY` (+ optional base URL / model) → `api_key`
//! 3. CLI autodetect: `agy` then `grok`, on `PATH` and `~/.local/bin`
//! 4. none
//!
//! `api_key` talks to an OpenAI-compatible HTTP endpoint. `cli_oauth` spawns
//! a local CLI whose OAuth subscription is already on the machine — it never
//! sees an API key and never dials `api.openai.com` / `api.x.ai` /
//! `generativelanguage.googleapis.com` itself.

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;

use serde_json::{json, Map, Value};
use tokio::io::AsyncReadExt;
use tokio::process::Command;

use super::adapter::OpenAiCompatibleAdapter;
use super::{
    CLOUD_API_KEY_ENV, CLOUD_BASE_URL_ENV, CLOUD_MODEL_ENV, DEFAULT_CLOUD_BASE_URL,
    DEFAULT_CLOUD_MODEL,
};

/// File name under the data dir.
pub const CLOUD_PROVIDER_FILE: &str = "cloud_provider.json";

/// How long a CLI cloud turn may run before it is killed.
pub const CLI_TIMEOUT: Duration = Duration::from_secs(120);

/// How long `{bin} --version` is allowed to take for the status chip.
const VERSION_TIMEOUT: Duration = Duration::from_secs(2);

/// Default Gemini model for the Antigravity CLI.
pub const AGY_DEFAULT_MODEL: &str = "gemini-3.7-flash";
/// Default xAI model for the `grok` CLI.
pub const GROK_DEFAULT_MODEL: &str = "grok-4.6";

/// Wire / status value for the credential mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CloudMode {
    /// OpenAI-compatible HTTP, key from env or the provider file.
    ApiKey,
    /// Locally-authenticated CLI (`agy`, `grok`, or a configured binary).
    CliOauth,
    /// Nothing configured.
    None,
}

impl CloudMode {
    /// The SPA / status-route token.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::ApiKey => "api_key",
            Self::CliOauth => "cli_oauth",
            Self::None => "none",
        }
    }

    fn parse(value: &str) -> Option<Self> {
        match value.trim().to_lowercase().as_str() {
            "api_key" | "apikey" | "key" => Some(Self::ApiKey),
            "cli_oauth" | "cli" | "oauth" => Some(Self::CliOauth),
            "none" | "" => Some(Self::None),
            _ => None,
        }
    }
}

/// What `GET /api/cloud/status` answers.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CloudStatus {
    /// Whether a turn could actually be sent.
    pub configured: bool,
    /// `api_key` | `cli_oauth` | `none`.
    pub mode: CloudMode,
    /// `gemini` / `xai` / `openai_compatible` / …
    pub provider: Option<String>,
    /// The model a turn would name.
    pub model: Option<String>,
    /// Extra, cheap detail (CLI name + version when obtainable).
    pub detail: Option<String>,
}

impl CloudStatus {
    /// The UI contract object.
    pub fn to_value(&self) -> Value {
        json!({
            "configured": self.configured,
            "mode": self.mode.as_str(),
            "provider": self.provider,
            "model": self.model,
            "detail": self.detail,
        })
    }

    /// Nothing is wired.
    pub fn none() -> Self {
        Self {
            configured: false,
            mode: CloudMode::None,
            provider: None,
            model: None,
            detail: None,
        }
    }
}

/// Inputs the resolver reads. Tests pass a snapshot so they never touch the
/// process environment (and never race other tests that do).
#[derive(Debug, Clone, Default)]
pub struct ResolveInput {
    /// `LATTICEAI_CLOUD_API_KEY`.
    pub api_key: Option<String>,
    /// `LATTICEAI_CLOUD_BASE_URL`.
    pub base_url: Option<String>,
    /// `LATTICEAI_CLOUD_MODEL`.
    pub model: Option<String>,
    /// Directories to search for `agy` / `grok`, in order. Empty → real PATH.
    pub path_dirs: Vec<PathBuf>,
}

impl ResolveInput {
    /// Read the live process environment and `PATH` / `~/.local/bin`.
    pub fn from_env() -> Self {
        let read = |name: &str| {
            std::env::var(name)
                .ok()
                .map(|value| value.trim().to_string())
                .filter(|value| !value.is_empty())
        };
        Self {
            api_key: read(CLOUD_API_KEY_ENV),
            base_url: read(CLOUD_BASE_URL_ENV),
            model: read(CLOUD_MODEL_ENV),
            path_dirs: default_search_dirs(),
        }
    }
}

/// One configured cloud backend.
#[derive(Debug, Clone)]
pub struct CloudProvider {
    mode: CloudMode,
    name: String,
    model: String,
    backend: CloudBackend,
}

#[derive(Debug, Clone)]
enum CloudBackend {
    Http(OpenAiCompatibleAdapter),
    Cli(CliSpec),
}

/// How to spawn one CLI turn.
#[derive(Debug, Clone)]
struct CliSpec {
    bin: PathBuf,
    /// Template args; `{prompt}`, `{model}`, `{effort}` are substituted.
    args: Vec<String>,
    effort: String,
    timeout: Duration,
}

impl CloudProvider {
    /// The provider name the SPA chip and the egress record carry.
    pub fn name(&self) -> &str {
        &self.name
    }

    /// The model a turn will send.
    pub fn model(&self) -> &str {
        &self.model
    }

    /// `api_key` / `cli_oauth`.
    pub fn mode(&self) -> CloudMode {
        self.mode
    }

    /// Whether a turn can actually be sent.
    pub fn configured(&self) -> bool {
        match &self.backend {
            CloudBackend::Http(adapter) => adapter.configured(),
            CloudBackend::Cli(spec) => !spec.bin.as_os_str().is_empty(),
        }
    }

    /// Status payload, with a cheap `{bin} --version` for CLI mode.
    pub async fn status(&self) -> CloudStatus {
        let mut detail = None;
        if let CloudBackend::Cli(spec) = &self.backend {
            detail = cli_version_line(&spec.bin).await;
        }
        CloudStatus {
            configured: self.configured(),
            mode: self.mode,
            provider: Some(self.name.clone()),
            model: Some(self.model.clone()),
            detail,
        }
    }

    /// Resolve from `<data_dir>/cloud_provider.json`, then env, then CLIs.
    pub fn resolve(data_dir: &Path) -> Option<Self> {
        Self::resolve_with(data_dir, &ResolveInput::from_env())
    }

    /// [`Self::resolve`] over an explicit snapshot.
    pub fn resolve_with(data_dir: &Path, input: &ResolveInput) -> Option<Self> {
        match from_file(data_dir, input) {
            FileResolution::Configured(provider) => return Some(provider),
            FileResolution::ExplicitNone => return None,
            FileResolution::Absent => {}
        }
        if let Some(from_env) = from_env_key(input) {
            return Some(from_env);
        }
        detect_cli(&input.path_dirs)
    }

    /// Point an HTTP adapter at a specific endpoint (tests).
    pub fn api_key(adapter: OpenAiCompatibleAdapter, provider: impl Into<String>) -> Self {
        let model = adapter.default_model().to_string();
        Self {
            mode: CloudMode::ApiKey,
            name: provider.into(),
            model,
            backend: CloudBackend::Http(adapter),
        }
    }

    /// A CLI runner, for tests that hand a fake executable.
    pub fn cli_oauth(
        bin: impl Into<PathBuf>,
        args: Vec<String>,
        provider: impl Into<String>,
        model: impl Into<String>,
        timeout: Duration,
    ) -> Self {
        Self {
            mode: CloudMode::CliOauth,
            name: provider.into(),
            model: model.into(),
            backend: CloudBackend::Cli(CliSpec {
                bin: bin.into(),
                args,
                effort: "low".into(),
                timeout,
            }),
        }
    }

    /// Stream one turn. Each non-empty piece is handed to `on_piece`.
    ///
    /// HTTP mode yields tokens as they arrive. CLI mode yields the whole
    /// stdout as one piece. `on_piece` returning `false` means the client
    /// hung up.
    pub async fn stream(
        &self,
        system: &str,
        user: &str,
        context: &str,
        on_piece: &mut (dyn FnMut(&str) -> bool + Send),
    ) -> Result<(), String> {
        match &self.backend {
            CloudBackend::Http(adapter) => {
                adapter.stream(system, user, context, None, on_piece).await
            }
            CloudBackend::Cli(spec) => {
                let prompt = compose_prompt(system, user, context);
                let answer = run_cli(spec, &prompt, &self.model).await?;
                if !answer.is_empty() {
                    let _ = on_piece(&answer);
                }
                Ok(())
            }
        }
    }
}

/// HYBRID system prompt + the minimal context slice + the question.
pub fn compose_prompt(system: &str, user: &str, context: &str) -> String {
    let mut parts = vec![system.trim().to_string()];
    if !context.trim().is_empty() {
        parts.push(format!(
            "Local Knowledge Graph context (minimal related nodes only):\n{}",
            context.trim()
        ));
    }
    parts.push(user.trim().to_string());
    parts.join("\n\n")
}

/// Directories `agy` / `grok` are looked up in.
pub fn default_search_dirs() -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    if let Ok(path) = std::env::var("PATH") {
        for entry in std::env::split_paths(&path) {
            if !entry.as_os_str().is_empty() {
                dirs.push(entry);
            }
        }
    }
    if let Some(home) = std::env::var_os("HOME") {
        let local = PathBuf::from(home).join(".local").join("bin");
        if !dirs.iter().any(|dir| dir == &local) {
            dirs.push(local);
        }
    }
    dirs
}

enum FileResolution {
    Configured(CloudProvider),
    ExplicitNone,
    Absent,
}

fn from_file(data_dir: &Path, input: &ResolveInput) -> FileResolution {
    let Ok(raw) = std::fs::read_to_string(data_dir.join(CLOUD_PROVIDER_FILE)) else {
        return FileResolution::Absent;
    };
    let Ok(value) = serde_json::from_str::<Value>(&raw) else {
        return FileResolution::Absent;
    };
    let Some(object) = value.as_object() else {
        return FileResolution::Absent;
    };
    let mode = object
        .get("mode")
        .and_then(Value::as_str)
        .and_then(CloudMode::parse)
        .unwrap_or(CloudMode::None);
    if mode == CloudMode::None {
        return FileResolution::ExplicitNone;
    }
    let provider = object
        .get("provider")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| default_provider_name(mode, object))
        .to_string();
    let model = object
        .get("model")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| default_model_for(&provider));
    match mode {
        CloudMode::ApiKey => {
            let api_key = object
                .get("api_key")
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_string)
                .or_else(|| input.api_key.clone())
                .unwrap_or_default();
            let base_url = object
                .get("base_url")
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_string)
                .or_else(|| input.base_url.clone())
                .unwrap_or_else(|| DEFAULT_CLOUD_BASE_URL.to_string());
            let adapter = OpenAiCompatibleAdapter::from_parts(api_key, base_url, model.clone());
            FileResolution::Configured(CloudProvider {
                mode: CloudMode::ApiKey,
                name: provider,
                model,
                backend: CloudBackend::Http(adapter),
            })
        }
        CloudMode::CliOauth => {
            let Some((bin, args)) = cli_from_object(object, &provider, &input.path_dirs) else {
                return FileResolution::Absent;
            };
            let effort = object
                .get("effort")
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .unwrap_or("low")
                .to_string();
            FileResolution::Configured(CloudProvider {
                mode: CloudMode::CliOauth,
                name: provider,
                model,
                backend: CloudBackend::Cli(CliSpec {
                    bin,
                    args,
                    effort,
                    timeout: CLI_TIMEOUT,
                }),
            })
        }
        CloudMode::None => FileResolution::ExplicitNone,
    }
}

fn default_provider_name(mode: CloudMode, object: &Map<String, Value>) -> &'static str {
    match mode {
        CloudMode::ApiKey => OpenAiCompatibleAdapter::PROVIDER_NAME,
        CloudMode::CliOauth => {
            let bin = object
                .get("cli")
                .and_then(|cli| cli.get("bin"))
                .and_then(Value::as_str)
                .unwrap_or("");
            if bin_looks_like(bin, "grok") {
                "xai"
            } else {
                "gemini"
            }
        }
        CloudMode::None => "none",
    }
}

fn default_model_for(provider: &str) -> String {
    match provider {
        "gemini" => AGY_DEFAULT_MODEL.to_string(),
        "xai" => GROK_DEFAULT_MODEL.to_string(),
        _ => DEFAULT_CLOUD_MODEL.to_string(),
    }
}

fn cli_from_object(
    object: &Map<String, Value>,
    provider: &str,
    path_dirs: &[PathBuf],
) -> Option<(PathBuf, Vec<String>)> {
    let cli = object.get("cli").and_then(Value::as_object);
    let named = cli
        .and_then(|cli| cli.get("bin"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty());
    let bin = if let Some(named) = named {
        let as_path = PathBuf::from(named);
        if as_path.is_absolute() || named.contains('/') || named.contains('\\') {
            as_path
        } else {
            find_bin(named, path_dirs).unwrap_or(as_path)
        }
    } else {
        default_bin_for(provider, path_dirs)?
    };
    let args = cli
        .and_then(|cli| cli.get("args"))
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect::<Vec<_>>()
        })
        .filter(|args| !args.is_empty())
        .unwrap_or_else(|| default_args_for(provider));
    Some((bin, args))
}

fn default_bin_for(provider: &str, path_dirs: &[PathBuf]) -> Option<PathBuf> {
    match provider {
        "xai" => find_bin("grok", path_dirs),
        _ => find_bin("agy", path_dirs).or_else(|| find_bin("grok", path_dirs)),
    }
}

fn default_args_for(provider: &str) -> Vec<String> {
    match provider {
        "xai" => vec![
            "-p".into(),
            "{prompt}".into(),
            "--model".into(),
            "{model}".into(),
            "--reasoning-effort".into(),
            "{effort}".into(),
        ],
        _ => vec![
            "-p".into(),
            "{prompt}".into(),
            "--model".into(),
            "{model}".into(),
            "--effort".into(),
            "{effort}".into(),
            "--dangerously-skip-permissions".into(),
        ],
    }
}

fn from_env_key(input: &ResolveInput) -> Option<CloudProvider> {
    let api_key = input.api_key.as_deref()?.trim();
    if api_key.is_empty() {
        return None;
    }
    let base_url = input
        .base_url
        .clone()
        .unwrap_or_else(|| DEFAULT_CLOUD_BASE_URL.to_string());
    let model = input
        .model
        .clone()
        .unwrap_or_else(|| DEFAULT_CLOUD_MODEL.to_string());
    let adapter = OpenAiCompatibleAdapter::from_parts(api_key, base_url, model.clone());
    Some(CloudProvider {
        mode: CloudMode::ApiKey,
        name: OpenAiCompatibleAdapter::PROVIDER_NAME.to_string(),
        model,
        backend: CloudBackend::Http(adapter),
    })
}

fn detect_cli(path_dirs: &[PathBuf]) -> Option<CloudProvider> {
    if let Some(bin) = find_bin("agy", path_dirs) {
        return Some(CloudProvider {
            mode: CloudMode::CliOauth,
            name: "gemini".into(),
            model: AGY_DEFAULT_MODEL.into(),
            backend: CloudBackend::Cli(CliSpec {
                bin,
                args: default_args_for("gemini"),
                effort: "low".into(),
                timeout: CLI_TIMEOUT,
            }),
        });
    }
    let bin = find_bin("grok", path_dirs)?;
    Some(CloudProvider {
        mode: CloudMode::CliOauth,
        name: "xai".into(),
        model: GROK_DEFAULT_MODEL.into(),
        backend: CloudBackend::Cli(CliSpec {
            bin,
            args: default_args_for("xai"),
            effort: "low".into(),
            timeout: CLI_TIMEOUT,
        }),
    })
}

/// Look up `name` in `dirs`. The first executable wins.
pub fn find_bin(name: &str, dirs: &[PathBuf]) -> Option<PathBuf> {
    for dir in dirs {
        let candidate = dir.join(name);
        if is_executable(&candidate) {
            return Some(candidate);
        }
    }
    None
}

fn is_executable(path: &Path) -> bool {
    if !path.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        path.metadata()
            .map(|meta| meta.permissions().mode() & 0o111 != 0)
            .unwrap_or(false)
    }
    #[cfg(not(unix))]
    {
        true
    }
}

fn bin_looks_like(bin: &str, needle: &str) -> bool {
    Path::new(bin)
        .file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name == needle || name.starts_with(&format!("{needle}.")))
}

fn substitute(template: &str, prompt: &str, model: &str, effort: &str) -> String {
    template
        .replace("{prompt}", prompt)
        .replace("{model}", model)
        .replace("{effort}", effort)
}

async fn run_cli(spec: &CliSpec, prompt: &str, model: &str) -> Result<String, String> {
    let cwd = tempfile::tempdir().map_err(|error| format!("cloud CLI temp dir: {error}"))?;
    let args: Vec<String> = spec
        .args
        .iter()
        .map(|arg| substitute(arg, prompt, model, &spec.effort))
        .collect();
    let mut child = Command::new(&spec.bin)
        .args(&args)
        .current_dir(cwd.path())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .map_err(|error| format!("cloud CLI could not start {}: {error}", spec.bin.display()))?;
    let mut stdout = child.stdout.take();
    let mut stderr = child.stderr.take();
    let stdout_task = tokio::spawn(async move {
        let mut buf = Vec::new();
        if let Some(mut pipe) = stdout.take() {
            let _ = pipe.read_to_end(&mut buf).await;
        }
        buf
    });
    let stderr_task = tokio::spawn(async move {
        let mut buf = Vec::new();
        if let Some(mut pipe) = stderr.take() {
            let _ = pipe.read_to_end(&mut buf).await;
        }
        buf
    });
    let status = match tokio::time::timeout(spec.timeout, child.wait()).await {
        Ok(Ok(status)) => status,
        Ok(Err(error)) => {
            return Err(format!("cloud CLI failed: {error}"));
        }
        Err(_) => {
            let _ = child.kill().await;
            let _ = child.wait().await;
            return Err(format!(
                "cloud CLI timed out after {}s",
                spec.timeout.as_secs()
            ));
        }
    };
    let stdout = String::from_utf8_lossy(&stdout_task.await.unwrap_or_default()).into_owned();
    let stderr = String::from_utf8_lossy(&stderr_task.await.unwrap_or_default()).into_owned();
    if !status.success() {
        let code = status
            .code()
            .map(|code| code.to_string())
            .unwrap_or_else(|| "signal".into());
        let err = stderr.trim();
        return Err(if err.is_empty() {
            format!("cloud CLI exited {code}")
        } else {
            format!(
                "cloud CLI exited {code}: {}",
                err.chars().take(400).collect::<String>()
            )
        });
    }
    let answer = stdout.trim().to_string();
    if answer.is_empty() {
        return Err("cloud CLI produced no output".into());
    }
    Ok(answer)
}

async fn cli_version_line(bin: &Path) -> Option<String> {
    let name = bin.file_name()?.to_string_lossy().into_owned();
    let mut child = Command::new(bin)
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .ok()?;
    let mut stdout = child.stdout.take();
    let read = tokio::spawn(async move {
        let mut buf = Vec::new();
        if let Some(mut pipe) = stdout.take() {
            let _ = pipe.read_to_end(&mut buf).await;
        }
        buf
    });
    let ok = match tokio::time::timeout(VERSION_TIMEOUT, child.wait()).await {
        Ok(Ok(status)) if status.success() => true,
        Ok(_) => false,
        Err(_) => {
            let _ = child.kill().await;
            false
        }
    };
    if !ok {
        return Some(name);
    }
    let text = String::from_utf8_lossy(&read.await.ok()?)
        .trim()
        .to_string();
    if text.is_empty() {
        return Some(name);
    }
    let first = text.lines().next().unwrap_or(&text).trim();
    Some(format!("{name} {first}"))
}

#[cfg(test)]
mod tests {
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
}
