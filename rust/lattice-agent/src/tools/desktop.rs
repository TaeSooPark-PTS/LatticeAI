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

use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::sandbox::ToolError;
use crate::tools::args;

/// `subprocess.run(..., timeout=10)`.
const OPEN_TIMEOUT: Duration = Duration::from_secs(10);

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
    let url = crate::pystr::py_str(raw).trim().to_string();
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
    open_with(opener(&url, Some(&app)), "URL 열기 실패", &url).await?;
    Ok(json!({
        "action": "open_url",
        "app": if app.is_empty() { "default".to_string() } else { app },
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
}
