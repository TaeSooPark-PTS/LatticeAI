//! `computer_*` — the OS actuators.
//!
//! Two of the eight actually do something: `computer_open_app` and
//! `computer_open_url` shell out to the platform opener, and that is ported
//! here verbatim (same argv, same ten-second ceiling, same refusal text).
//!
//! The other six — click, type, key, scroll, move, drag — are `pyautogui`
//! calls, and `pyautogui` is **not a declared dependency of this product**:
//! it appears in no `pyproject.toml`, no requirements file, and the committed
//! HTTP fixtures record `computer_status` answering
//! `{"available": false, "reason": "pyautogui not installed"}`. Every one of
//! those six therefore raises the same `ToolError` on the shipped worker today,
//! and that exact refusal is what they answer here. It is a **capability gap
//! stated as one** (see the W4 wiring note): a user who pip-installs pyautogui
//! into the worker venv gets working pointer control today and will not after
//! the handlers are deleted. Closing it means a native actuator, not a
//! resurrected Python path.

use std::net::IpAddr;
use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::tools::args;
use crate::tools::sandbox::ToolError;

/// `subprocess.run(..., timeout=10)`.
const OPEN_TIMEOUT: Duration = Duration::from_secs(10);

/// Short timeout for UI inspection and bridge actuation calls.
const INSPECT_TIMEOUT: Duration = Duration::from_secs(5);

/// The six pointer/keyboard tools' refusal when `_CU_AVAILABLE` is false.
pub const NO_POINTER_CONTROL: &str = "pyautogui를 사용할 수 없습니다.";

/// The six names that need `pyautogui`, sorted.
pub const POINTER_TOOLS: [&str; 6] = [
    "computer_click",
    "computer_drag",
    "computer_key",
    "computer_move",
    "computer_scroll",
    "computer_type",
];

/// Read and validate `LATTICEAI_COMPUTER_BRIDGE_URL`, enforcing a strict
/// loopback-only policy (`127.0.0.1`, `localhost`, `[::1]`).
pub fn bridge_url() -> Result<Option<String>, ToolError> {
    let raw = match std::env::var("LATTICEAI_COMPUTER_BRIDGE_URL") {
        Ok(val) => val,
        Err(_) => return Ok(None),
    };
    let trimmed = raw.trim().trim_end_matches('/');
    if trimmed.is_empty() {
        return Ok(None);
    }
    let Some((scheme_raw, after_scheme)) = trimmed.split_once("://") else {
        return Err(ToolError::tool(
            "LATTICEAI_COMPUTER_BRIDGE_URL must use http:// or https:// on loopback.",
        ));
    };
    let scheme = scheme_raw.to_ascii_lowercase();
    if scheme != "http" && scheme != "https" {
        return Err(ToolError::tool(format!(
            "LATTICEAI_COMPUTER_BRIDGE_URL scheme '{scheme}' is not allowed."
        )));
    }
    let authority_end = after_scheme
        .find(['/', '?', '#'])
        .unwrap_or(after_scheme.len());
    let authority = &after_scheme[..authority_end];
    if authority.is_empty() || authority.contains('@') {
        return Err(ToolError::tool(
            "LATTICEAI_COMPUTER_BRIDGE_URL contains an invalid authority.",
        ));
    }

    let host = if authority.starts_with('[') {
        let Some(close_idx) = authority.find(']') else {
            return Err(ToolError::tool(
                "LATTICEAI_COMPUTER_BRIDGE_URL has a malformed IPv6 host.",
            ));
        };
        let rest = &authority[close_idx + 1..];
        if !rest.is_empty() {
            let Some(port_str) = rest.strip_prefix(':') else {
                return Err(ToolError::tool(
                    "LATTICEAI_COMPUTER_BRIDGE_URL has an invalid authority after IPv6 host.",
                ));
            };
            let port = port_str.parse::<u16>().map_err(|_| {
                ToolError::tool("LATTICEAI_COMPUTER_BRIDGE_URL has an invalid port.")
            })?;
            if port == 0 {
                return Err(ToolError::tool(
                    "LATTICEAI_COMPUTER_BRIDGE_URL port cannot be 0.",
                ));
            }
        }
        authority[1..close_idx].to_ascii_lowercase()
    } else if let Some((h, port_str)) = authority.rsplit_once(':') {
        if h.contains(':') || h.is_empty() {
            return Err(ToolError::tool(
                "LATTICEAI_COMPUTER_BRIDGE_URL has an invalid host.",
            ));
        }
        let port = port_str
            .parse::<u16>()
            .map_err(|_| ToolError::tool("LATTICEAI_COMPUTER_BRIDGE_URL has an invalid port."))?;
        if port == 0 {
            return Err(ToolError::tool(
                "LATTICEAI_COMPUTER_BRIDGE_URL port cannot be 0.",
            ));
        }
        h.to_ascii_lowercase()
    } else {
        authority.to_ascii_lowercase()
    };

    let clean_host = host.trim_end_matches('.');
    let is_loopback = clean_host == "localhost"
        || clean_host
            .parse::<IpAddr>()
            .map(|ip| ip.is_loopback())
            .unwrap_or(false);
    if !is_loopback {
        return Err(ToolError::tool(format!(
            "LATTICEAI_COMPUTER_BRIDGE_URL must target a loopback host (127.0.0.1, localhost, or [::1]), got '{clean_host}'."
        )));
    }
    Ok(Some(trimmed.to_string()))
}

fn encode_query_component(input: &str) -> String {
    let mut out = String::with_capacity(input.len());
    for b in input.as_bytes() {
        match *b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(*b as char)
            }
            b' ' => out.push_str("%20"),
            other => out.push_str(&format!("%{other:02X}")),
        }
    }
    out
}

async fn bridge_post_act(base: &str, payload: Value) -> Result<Value, ToolError> {
    let client = reqwest::Client::builder()
        .timeout(INSPECT_TIMEOUT)
        .build()
        .map_err(|err| ToolError::tool(format!("failed to build bridge client: {err}")))?;
    let body_bytes =
        serde_json::to_vec(&payload).map_err(|err| ToolError::tool(err.to_string()))?;
    let response = client
        .post(format!("{base}/ui/act"))
        .header("content-type", "application/json")
        .body(body_bytes)
        .send()
        .await
        .map_err(|err| ToolError::tool(format!("computer bridge /ui/act failed: {err}")))?;
    let status = response.status();
    let bytes = response
        .bytes()
        .await
        .map_err(|err| ToolError::tool(format!("computer bridge response unreadable: {err}")))?;
    if !status.is_success() {
        let text = String::from_utf8_lossy(&bytes);
        return Err(ToolError::tool(format!(
            "computer bridge /ui/act returned {}: {}",
            status.as_u16(),
            text.trim()
        )));
    }
    let parsed: Value = serde_json::from_slice(&bytes).unwrap_or_else(|_| json!({}));
    let mut merged = match payload {
        Value::Object(map) => map,
        _ => Map::new(),
    };
    if let Value::Object(resp_map) = parsed {
        for (k, v) in resp_map {
            merged.insert(k, v);
        }
    }
    merged.insert("mode".into(), json!("bridge"));
    Ok(Value::Object(merged))
}

async fn query_macos_frontmost_app() -> Option<String> {
    let mut child = tokio::process::Command::new("osascript");
    child
        .args([
            "-e",
            "tell application \"System Events\" to get name of first application process whose frontmost is true",
        ])
        .kill_on_drop(true);
    let output = tokio::time::timeout(INSPECT_TIMEOUT, child.output())
        .await
        .ok()?
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let name = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if name.is_empty() {
        None
    } else {
        Some(name)
    }
}

/// `computer_ui_inspect(app=None)` — observe active window and accessibility UI elements.
pub async fn computer_ui_inspect(args: &Map<String, Value>) -> Result<Value, ToolError> {
    let app_filter = args
        .get("app")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());

    if let Some(base) = bridge_url()? {
        let url = match app_filter {
            Some(app) => format!("{base}/ui/inspect?app={}", encode_query_component(app)),
            None => format!("{base}/ui/inspect"),
        };
        let client = reqwest::Client::builder()
            .timeout(INSPECT_TIMEOUT)
            .build()
            .map_err(|err| ToolError::tool(format!("failed to build bridge client: {err}")))?;
        let response =
            client.get(&url).send().await.map_err(|err| {
                ToolError::tool(format!("computer bridge /ui/inspect failed: {err}"))
            })?;
        let status = response.status();
        let bytes = response.bytes().await.map_err(|err| {
            ToolError::tool(format!(
                "computer bridge /ui/inspect response unreadable: {err}"
            ))
        })?;
        if !status.is_success() {
            let text = String::from_utf8_lossy(&bytes);
            return Err(ToolError::tool(format!(
                "computer bridge /ui/inspect returned {}: {}",
                status.as_u16(),
                text.trim()
            )));
        }
        let parsed: Value = serde_json::from_slice(&bytes).map_err(|err| {
            ToolError::tool(format!(
                "computer bridge /ui/inspect returned invalid JSON: {err}"
            ))
        })?;
        let mut obj = parsed.as_object().cloned().unwrap_or_default();
        obj.insert("available".into(), json!(true));
        obj.insert("mode".into(), json!("bridge"));
        obj.entry("frontmost_app".to_string())
            .or_insert_with(|| json!(app_filter.unwrap_or("")));
        obj.entry("window_title".to_string())
            .or_insert_with(|| json!(""));
        obj.entry("screen_size".to_string())
            .or_insert_with(|| json!({"width": 0, "height": 0}));
        if !obj.get("elements").is_some_and(Value::is_array) {
            obj.insert("elements".into(), json!([]));
        }
        return Ok(Value::Object(obj));
    }

    if std::env::consts::OS == "macos" {
        let frontmost = query_macos_frontmost_app()
            .await
            .or_else(|| app_filter.map(str::to_string))
            .unwrap_or_default();
        return Ok(json!({
            "available": false,
            "mode": "osascript_limited",
            "frontmost_app": frontmost,
            "window_title": "",
            "screen_size": {"width": 0, "height": 0},
            "elements": [],
            "note": "Accessibility element tree requires LATTICEAI_COMPUTER_BRIDGE_URL or macOS Accessibility permissions.",
        }));
    }

    Ok(json!({
        "available": false,
        "mode": "unsupported",
        "frontmost_app": app_filter.unwrap_or(""),
        "window_title": "",
        "screen_size": {"width": 0, "height": 0},
        "elements": [],
        "note": "UI accessibility inspection is not supported on this platform without LATTICEAI_COMPUTER_BRIDGE_URL.",
    }))
}

/// `platform.system()`'s three branches, as this build sees them.
fn opener(target: &str, app: Option<&str>) -> Vec<String> {
    let owned = |values: &[&str]| values.iter().map(|value| (*value).to_string()).collect();
    match std::env::consts::OS {
        "macos" => match app {
            Some(app) if !app.is_empty() => vec![
                "open".into(),
                "-a".into(),
                app.to_string(),
                target.to_string(),
            ],
            // `computer_open_app` always passes `-a`; `computer_open_url` drops
            // it when no app was named, which is `open <url>`.
            Some(_) | None => vec!["open".into(), target.to_string()],
        },
        "windows" => {
            let mut command: Vec<String> = owned(&["cmd", "/c", "start", ""]);
            command.push(target.to_string());
            command
        }
        _ => vec!["xdg-open".into(), target.to_string()],
    }
}

/// Run the platform opener, mapping a non-zero exit onto Python's message.
async fn open_with(command: Vec<String>, failure: &str, fallback: &str) -> Result<(), ToolError> {
    let (program, rest) = command.split_first().expect("opener is never empty");
    let mut child = tokio::process::Command::new(program);
    child.args(rest).kill_on_drop(true);
    let output = match tokio::time::timeout(OPEN_TIMEOUT, child.output()).await {
        Ok(Ok(output)) => output,
        // Python raises `FileNotFoundError` / `TimeoutExpired` here, neither of
        // which the seam catches — a 500 rather than a step error. Named as a
        // deviation: the step fails with the reason instead.
        Ok(Err(error)) => return Err(ToolError::tool(format!("{failure}: {program} ({error})"))),
        Err(_) => {
            return Err(ToolError::tool(format!(
                "{failure}: {} seconds",
                OPEN_TIMEOUT.as_secs()
            )))
        }
    };
    if output.status.success() {
        return Ok(());
    }
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    let detail = if stderr.is_empty() { fallback } else { &stderr };
    Err(ToolError::tool(format!("{failure}: {detail}")))
}

/// `computer_open_app(app="Google Chrome")`.
pub async fn computer_open_app(args: &Map<String, Value>) -> Result<Value, ToolError> {
    let app = args::coerced_str(args, "app", "Google Chrome")
        .trim()
        .to_string();
    if app.is_empty() {
        return Err(ToolError::tool("앱 이름이 필요합니다."));
    }
    if let Some(base) = bridge_url()? {
        return bridge_post_act(&base, json!({"action": "open_app", "app": app})).await;
    }
    // macOS opens an *app*, so the app is the target and there is no `-a`.
    let command = match std::env::consts::OS {
        "macos" => vec!["open".to_string(), "-a".to_string(), app.clone()],
        _ => opener(&app, None),
    };
    open_with(command, "앱 열기 실패", &app).await?;
    Ok(json!({"action": "open_app", "app": app}))
}

/// `computer_open_url(url, app="Google Chrome")`.
pub async fn computer_open_url(args: &Map<String, Value>) -> Result<Value, ToolError> {
    let raw = args::required(args, "url")?;
    let url = crate::parse::pystr::py_str(raw).trim().to_string();
    // `str(app or "").strip()` — unlike `computer_open_app`, an explicitly
    // falsy app is **no app** here, and the opener drops its `-a` for it.
    let app = args::defaulted_str(args, "app", "Google Chrome")
        .trim()
        .to_string();
    if url.is_empty() {
        return Err(ToolError::tool("URL이 필요합니다."));
    }
    let url =
        if !url.contains("://") && !url.starts_with("localhost") && !url.starts_with("127.0.0.1") {
            format!("https://{url}")
        } else {
            url
        };
    let resolved_app = if app.is_empty() {
        "default".to_string()
    } else {
        app.clone()
    };
    if let Some(base) = bridge_url()? {
        return bridge_post_act(
            &base,
            json!({
                "action": "open_url",
                "app": resolved_app,
                "url": url,
            }),
        )
        .await;
    }
    open_with(opener(&url, Some(&app)), "URL 열기 실패", &url).await?;
    Ok(json!({
        "action": "open_url",
        "app": resolved_app,
        "url": url,
    }))
}

/// The six `pyautogui` tools: the argument the handler reads first, then the
/// unavailability every shipped worker reports.
pub fn pointer_tool(tool: &str, args: &Map<String, Value>) -> Result<Value, ToolError> {
    match tool {
        // `a["text"]` / `a["key"]` are evaluated building the call, so a missing
        // key is a KeyError *before* the tool refuses.
        "computer_type" => args::required(args, "text").map(|_| ())?,
        "computer_key" => args::required(args, "key").map(|_| ())?,
        _ => (),
    }
    Err(ToolError::tool(NO_POINTER_CONTROL))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(value: Value) -> Map<String, Value> {
        value.as_object().expect("object").clone()
    }

    #[test]
    fn every_pointer_tool_reports_the_python_unavailability() {
        for tool in POINTER_TOOLS {
            let filled = args(json!({"text": "hi", "key": "return", "x": 1, "y": 2}));
            assert_eq!(
                pointer_tool(tool, &filled)
                    .expect_err("unavailable")
                    .message,
                NO_POINTER_CONTROL,
                "{tool}"
            );
        }
    }

    #[test]
    fn the_two_typed_pointer_tools_read_their_argument_first() {
        assert_eq!(
            pointer_tool("computer_type", &Map::new())
                .expect_err("missing")
                .message,
            "'text'"
        );
        assert_eq!(
            pointer_tool("computer_key", &Map::new())
                .expect_err("missing")
                .message,
            "'key'"
        );
        // The ones with defaults go straight to the refusal.
        assert_eq!(
            pointer_tool("computer_click", &Map::new())
                .expect_err("unavailable")
                .message,
            NO_POINTER_CONTROL
        );
    }

    #[tokio::test]
    async fn an_empty_app_name_is_refused_before_anything_is_spawned() {
        assert_eq!(
            computer_open_app(&args(json!({"app": "   "})))
                .await
                .expect_err("empty")
                .message,
            "앱 이름이 필요합니다."
        );
    }

    #[tokio::test]
    async fn a_missing_url_is_the_key_error_and_an_empty_one_the_refusal() {
        assert_eq!(
            computer_open_url(&Map::new())
                .await
                .expect_err("missing")
                .message,
            "'url'"
        );
        assert_eq!(
            computer_open_url(&args(json!({"url": "  "})))
                .await
                .expect_err("empty")
                .message,
            "URL이 필요합니다."
        );
    }

    #[test]
    fn the_two_openers_default_their_app_differently() {
        // `computer_open_app`: `str(app or "Google Chrome")` — falsy is Chrome.
        assert_eq!(
            args::coerced_str(&args(json!({"app": ""})), "app", "Google Chrome"),
            "Google Chrome"
        );
        // `computer_open_url`: `str(app or "")` — falsy is *no app*, and only
        // an absent key takes the handler's Chrome.
        assert_eq!(
            args::defaulted_str(&args(json!({"app": ""})), "app", "Google Chrome"),
            ""
        );
        assert_eq!(
            args::defaulted_str(&Map::new(), "app", "Google Chrome"),
            "Google Chrome"
        );
    }

    #[test]
    fn a_bare_host_gets_https_and_loopback_does_not() {
        // The scheme rule is `"://" not in url and not startswith(localhost|127.0.0.1)`.
        let scheme = |url: &str| {
            !url.contains("://") && !url.starts_with("localhost") && !url.starts_with("127.0.0.1")
        };
        assert!(scheme("example.com"));
        assert!(!scheme("https://example.com"));
        assert!(!scheme("localhost:4825/app"));
        assert!(!scheme("127.0.0.1:4825"));
    }

    #[test]
    fn the_opener_is_the_platform_argv() {
        let command = opener("https://example.com", Some("Safari"));
        match std::env::consts::OS {
            "macos" => assert_eq!(command, ["open", "-a", "Safari", "https://example.com"]),
            "windows" => assert_eq!(command, ["cmd", "/c", "start", "", "https://example.com"]),
            _ => assert_eq!(command, ["xdg-open", "https://example.com"]),
        }
        let no_app = opener("https://example.com", Some(""));
        match std::env::consts::OS {
            "macos" => assert_eq!(no_app, ["open", "https://example.com"]),
            "windows" => assert_eq!(no_app, ["cmd", "/c", "start", "", "https://example.com"]),
            _ => assert_eq!(no_app, ["xdg-open", "https://example.com"]),
        }
    }

    #[tokio::test]
    async fn a_failing_opener_reports_the_python_prefix() {
        let error = open_with(
            vec!["definitely-not-a-real-opener".into(), "x".into()],
            "앱 열기 실패",
            "Some App",
        )
        .await
        .expect_err("no such binary");
        assert!(
            error.message.starts_with("앱 열기 실패: "),
            "{}",
            error.message
        );
    }

    static BRIDGE_ENV_LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

    struct BridgeEnvGuard(Option<String>);

    impl BridgeEnvGuard {
        fn set(val: Option<&str>) -> Self {
            let prev = std::env::var("LATTICEAI_COMPUTER_BRIDGE_URL").ok();
            match val {
                Some(v) => std::env::set_var("LATTICEAI_COMPUTER_BRIDGE_URL", v),
                None => std::env::remove_var("LATTICEAI_COMPUTER_BRIDGE_URL"),
            }
            Self(prev)
        }
    }

    impl Drop for BridgeEnvGuard {
        fn drop(&mut self) {
            match &self.0 {
                Some(v) => std::env::set_var("LATTICEAI_COMPUTER_BRIDGE_URL", v),
                None => std::env::remove_var("LATTICEAI_COMPUTER_BRIDGE_URL"),
            }
        }
    }

    #[test]
    fn bridge_url_accepts_loopback_and_rejects_remote_hosts_fail_closed() {
        let _lock = BRIDGE_ENV_LOCK.blocking_lock();
        let _g = BridgeEnvGuard::set(None);
        assert_eq!(bridge_url().expect("none"), None);

        for valid in [
            "http://127.0.0.1:9911/",
            "http://localhost:9911",
            "http://[::1]:9911/",
        ] {
            let _g = BridgeEnvGuard::set(Some(valid));
            let resolved = bridge_url().expect("valid loopback").expect("some");
            assert!(!resolved.ends_with('/'));
        }

        for invalid in [
            "http://192.168.1.50:9911",
            "https://evil.example.com/bridge",
            "http://169.254.169.254",
            "ftp://127.0.0.1:9911",
        ] {
            let _g = BridgeEnvGuard::set(Some(invalid));
            assert!(
                bridge_url().is_err(),
                "expected non-loopback bridge URL '{invalid}' to be rejected"
            );
        }
    }

    #[tokio::test]
    async fn computer_ui_inspect_and_openers_route_through_loopback_bridge() {
        let app = axum::Router::new()
            .route(
                "/ui/inspect",
                axum::routing::get(|| async {
                    axum::Json(json!({
                        "frontmost_app": "Safari",
                        "window_title": "LatticeAI Docs",
                        "screen_size": {"width": 1440, "height": 900},
                        "elements": [
                            {"id": "btn-1", "role": "AXButton", "label": "Run", "x": 120, "y": 240}
                        ]
                    }))
                }),
            )
            .route(
                "/ui/act",
                axum::routing::post(|axum::Json(payload): axum::Json<Value>| async move {
                    axum::Json(json!({
                        "status": "ok",
                        "verified": true,
                        "echo": payload
                    }))
                }),
            );

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let origin = format!("http://{}", listener.local_addr().expect("addr"));
        tokio::spawn(async move {
            let _ = axum::serve(listener, app).await;
        });

        let _lock = BRIDGE_ENV_LOCK.lock().await;
        let _g = BridgeEnvGuard::set(Some(&origin));

        let inspected = computer_ui_inspect(&args(json!({"app": "Safari"})))
            .await
            .expect("inspect via bridge");
        assert_eq!(inspected["available"], true);
        assert_eq!(inspected["mode"], "bridge");
        assert_eq!(inspected["frontmost_app"], "Safari");
        assert_eq!(inspected["window_title"], "LatticeAI Docs");
        assert_eq!(inspected["elements"].as_array().unwrap().len(), 1);

        let opened_app = computer_open_app(&args(json!({"app": "Safari"})))
            .await
            .expect("open_app via bridge");
        assert_eq!(opened_app["action"], "open_app");
        assert_eq!(opened_app["app"], "Safari");
        assert_eq!(opened_app["mode"], "bridge");
        assert_eq!(opened_app["verified"], true);

        let opened_url = computer_open_url(&args(json!({"url": "example.com", "app": "Safari"})))
            .await
            .expect("open_url via bridge");
        assert_eq!(opened_url["action"], "open_url");
        assert_eq!(opened_url["url"], "https://example.com");
        assert_eq!(opened_url["mode"], "bridge");
    }

    #[tokio::test]
    async fn computer_ui_inspect_gracefully_falls_back_without_bridge() {
        let _lock = BRIDGE_ENV_LOCK.lock().await;
        let _g = BridgeEnvGuard::set(None);

        let inspected = computer_ui_inspect(&Map::new())
            .await
            .expect("fallback inspect");
        assert_eq!(inspected["available"], false);
        assert!(matches!(
            inspected["mode"].as_str(),
            Some("osascript_limited" | "unsupported")
        ));
        assert_eq!(inspected["elements"], json!([]));
    }
}
