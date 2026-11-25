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

pub const CLOUD_PROVIDER_FILE: &str = "cloud_provider.json";

pub const CLI_TIMEOUT: Duration = Duration::from_secs(120);

const VERSION_TIMEOUT: Duration = Duration::from_secs(2);

pub const AGY_DEFAULT_MODEL: &str = "gemini-3.7-flash";
pub const GROK_DEFAULT_MODEL: &str = "grok-4.6";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CloudMode {
    ApiKey,
    CliOauth,
    None,
}

impl CloudMode {
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CloudStatus {
    pub configured: bool,
    pub mode: CloudMode,
    pub provider: Option<String>,
    pub model: Option<String>,
    pub detail: Option<String>,
    pub verified: Option<bool>,
}

impl CloudStatus {
    pub fn to_value(&self) -> Value {
        json!({
            "configured": self.configured,
            "mode": self.mode.as_str(),
            "provider": self.provider,
            "model": self.model,
            "detail": self.detail,
            "verified": self.verified,
        })
    }

    pub fn none() -> Self {
        Self {
            configured: false,
            mode: CloudMode::None,
            provider: None,
            model: None,
            detail: None,
            verified: None,
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct ResolveInput {
    pub api_key: Option<String>,
    pub base_url: Option<String>,
    pub model: Option<String>,
    pub path_dirs: Vec<PathBuf>,
}

impl ResolveInput {
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

#[derive(Debug, Clone)]
struct CliSpec {
    bin: PathBuf,
    args: Vec<String>,
    effort: String,
    timeout: Duration,
}

impl CloudProvider {
    pub fn name(&self) -> &str {
        &self.name
    }

    pub fn model(&self) -> &str {
        &self.model
    }

    pub fn mode(&self) -> CloudMode {
        self.mode
    }

    pub fn configured(&self) -> bool {
        match &self.backend {
            CloudBackend::Http(adapter) => adapter.configured(),
            CloudBackend::Cli(spec) => !spec.bin.as_os_str().is_empty(),
        }
    }

    pub async fn status(&self) -> CloudStatus {
        match &self.backend {
            CloudBackend::Http(adapter) => match adapter.probe_models().await {
                Ok(ids) => CloudStatus {
                    configured: true,
                    mode: self.mode,
                    provider: Some(self.name.clone()),
                    model: Some(self.model.clone()),
                    detail: Some(format!("{} models", ids.len())),
                    verified: Some(true),
                },
                Err(error) => CloudStatus {
                    configured: false,
                    mode: self.mode,
                    provider: Some(self.name.clone()),
                    model: Some(self.model.clone()),
                    detail: Some(error),
                    verified: Some(false),
                },
            },
            CloudBackend::Cli(spec) => CloudStatus {
                configured: self.configured(),
                mode: self.mode,
                provider: Some(self.name.clone()),
                model: Some(self.model.clone()),
                detail: cli_version_line(&spec.bin).await,
                verified: None,
            },
        }
    }

    pub fn resolve(data_dir: &Path) -> Option<Self> {
        Self::resolve_with(data_dir, &ResolveInput::from_env())
    }

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

    pub fn api_key(adapter: OpenAiCompatibleAdapter, provider: impl Into<String>) -> Self {
        let model = adapter.default_model().to_string();
        Self {
            mode: CloudMode::ApiKey,
            name: provider.into(),
            model,
            backend: CloudBackend::Http(adapter),
        }
    }

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

async fn spawn_retrying_etxtbsy(cmd: &mut Command) -> std::io::Result<tokio::process::Child> {
    for attempt in 0..10 {
        match cmd.spawn() {
            Ok(child) => return Ok(child),
            Err(err) if err.raw_os_error() == Some(26) && attempt < 9 => {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
            Err(err) => return Err(err),
        }
    }
    cmd.spawn()
}

async fn run_cli(spec: &CliSpec, prompt: &str, model: &str) -> Result<String, String> {
    let cwd = tempfile::tempdir().map_err(|error| format!("cloud CLI temp dir: {error}"))?;
    let args: Vec<String> = spec
        .args
        .iter()
        .map(|arg| substitute(arg, prompt, model, &spec.effort))
        .collect();
    let mut cmd = Command::new(&spec.bin);
    cmd.args(&args)
        .current_dir(cwd.path())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let mut child = spawn_retrying_etxtbsy(&mut cmd)
        .await
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
    let mut cmd = Command::new(bin);
    cmd.arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let mut child = spawn_retrying_etxtbsy(&mut cmd).await.ok()?;
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
#[path = "provider_tests.rs"]
mod tests;
