//! Setup wizard install-item execution (SSE frames, no fake "complete").

use serde_json::{json, Map, Value};

use super::SetupState;

pub(crate) struct InstallOutcome {
    pub(crate) status: String,
    pub(crate) frames: Vec<Vec<(&'static str, Value)>>,
}

pub(crate) async fn execute_install_item(state: &SetupState, item: &Value) -> InstallOutcome {
    let item_id = item.get("id").and_then(Value::as_str).unwrap_or("unknown");
    let name = item.get("name").and_then(Value::as_str).unwrap_or(item_id);
    let action = item.get("action").and_then(Value::as_object);
    let atype = action
        .and_then(|a| a.get("type"))
        .and_then(Value::as_str)
        .unwrap_or("");
    let classified = classify_install_action(atype, action, item);
    match classified {
        InstallKind::Ready => InstallOutcome {
            status: "skipped".into(),
            frames: vec![vec![
                ("id", json!(item_id)),
                ("status", json!("skipped")),
                ("msg", json!(format!("{name} — 이미 준비됨"))),
            ]],
        },
        InstallKind::Auth { url } => InstallOutcome {
            status: "auth".into(),
            frames: {
                (state.opener)(&url);
                vec![
                    vec![
                        ("id", json!(item_id)),
                        ("status", json!("auth")),
                        ("msg", json!("브라우저에서 인증 페이지를 엽니다...")),
                        ("auth_url", json!(url)),
                    ],
                    vec![
                        ("id", json!(item_id)),
                        ("status", json!("waiting")),
                        ("msg", json!("브라우저에서 인증 완료 후 계속하세요")),
                    ],
                ]
            },
        },
        InstallKind::Manual { command } => InstallOutcome {
            status: "manual".into(),
            frames: vec![vec![
                ("id", json!(item_id)),
                ("status", json!("manual")),
                ("msg", json!(command.clone())),
                ("command", json!(command)),
            ]],
        },
        InstallKind::PrepareModel { model, engine } => {
            prepare_model_item(state, item_id, name, &model, &engine).await
        }
        InstallKind::Unknown { detail } => InstallOutcome {
            status: "error".into(),
            frames: vec![
                vec![
                    ("id", json!(item_id)),
                    ("status", json!("starting")),
                    ("msg", json!(format!("{name} 준비 중..."))),
                ],
                vec![
                    ("id", json!(item_id)),
                    ("status", json!("error")),
                    ("msg", json!(detail)),
                ],
            ],
        },
    }
}

enum InstallKind {
    Ready,
    Auth { url: String },
    Manual { command: String },
    PrepareModel { model: String, engine: String },
    Unknown { detail: String },
}

fn classify_install_action(
    atype: &str,
    action: Option<&Map<String, Value>>,
    item: &Value,
) -> InstallKind {
    let model = action
        .and_then(|a| a.get("model").or_else(|| a.get("model_id")))
        .or_else(|| item.get("model_id"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let engine = action
        .and_then(|a| a.get("engine"))
        .and_then(Value::as_str)
        .unwrap_or("local_mlx")
        .to_string();
    let command = action
        .and_then(|a| a.get("command").or_else(|| a.get("cmd")))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    match atype {
        "" | "ready" | "noop" => {
            if !model.is_empty() {
                InstallKind::PrepareModel { model, engine }
            } else {
                InstallKind::Ready
            }
        }
        "auth" | "url" => InstallKind::Auth {
            url: action
                .and_then(|a| a.get("url"))
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
        },
        "brew" | "pip" | "apt" | "dnf" | "pacman" | "host" | "install_package" => {
            let fallback = if command.is_empty() {
                match atype {
                    "brew" => format!(
                        "brew install {}",
                        item.get("id").and_then(Value::as_str).unwrap_or("")
                    ),
                    "pip" => format!(
                        "pip3 install {}",
                        item.get("id").and_then(Value::as_str).unwrap_or("")
                    ),
                    "apt" => format!(
                        "sudo apt-get install {}",
                        item.get("id").and_then(Value::as_str).unwrap_or("")
                    ),
                    other => format!(
                        "{other} install {}",
                        item.get("id").and_then(Value::as_str).unwrap_or("")
                    ),
                }
            } else {
                command
            };
            InstallKind::Manual { command: fallback }
        }
        "download" | "prepare_model" | "prepare-model" | "model" | "pull" => {
            if model.is_empty() {
                InstallKind::Unknown {
                    detail: "prepare-model action is missing a model id".into(),
                }
            } else {
                InstallKind::PrepareModel { model, engine }
            }
        }
        other => InstallKind::Unknown {
            detail: format!("알 수 없는 액션: {other}"),
        },
    }
}

async fn prepare_model_item(
    state: &SetupState,
    item_id: &str,
    name: &str,
    model: &str,
    engine: &str,
) -> InstallOutcome {
    let mut frames = vec![vec![
        ("id", json!(item_id)),
        ("status", json!("starting")),
        ("msg", json!(format!("{name} 준비 중..."))),
    ]];
    let Some(worker) = state.worker.as_ref() else {
        frames.push(vec![
            ("id", json!(item_id)),
            ("status", json!("error")),
            (
                "msg",
                json!("worker is not configured; cannot run /engines/prepare-model"),
            ),
        ]);
        return InstallOutcome {
            status: "error".into(),
            frames,
        };
    };
    let body = json!({
        "model": model,
        "engine": engine,
        "allow_download": true,
    });
    match worker
        .stream_sse("/engines/prepare-model/stream", &body)
        .await
    {
        Ok(upstream) => {
            let mut response = upstream.into_response();
            let mut saw_error = false;
            while let Ok(Some(chunk)) = response.chunk().await {
                let text = String::from_utf8_lossy(&chunk);
                if text.contains("\"error\"") || text.contains("\"status\":\"error\"") {
                    saw_error = true;
                }
                frames.push(vec![
                    ("id", json!(item_id)),
                    ("status", json!("progress")),
                    ("msg", json!(text.trim())),
                ]);
            }
            if saw_error {
                frames.push(vec![
                    ("id", json!(item_id)),
                    ("status", json!("error")),
                    ("msg", json!(format!("{name} 준비 실패"))),
                ]);
                InstallOutcome {
                    status: "error".into(),
                    frames,
                }
            } else {
                frames.push(vec![
                    ("id", json!(item_id)),
                    ("status", json!("done")),
                    ("msg", json!(format!("{name} 준비 완료"))),
                ]);
                InstallOutcome {
                    status: "done".into(),
                    frames,
                }
            }
        }
        Err(err) => {
            frames.push(vec![
                ("id", json!(item_id)),
                ("status", json!("error")),
                ("msg", json!(err.to_string())),
            ]);
            InstallOutcome {
                status: "error".into(),
                frames,
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn brew_and_pip_are_manual_with_the_exact_command() {
        let item = json!({"id": "mlx", "name": "MLX"});
        match classify_install_action("brew", None, &item) {
            InstallKind::Manual { command } => assert_eq!(command, "brew install mlx"),
            _ => panic!("expected manual, got a different kind"),
        }
        let action = serde_json::Map::from_iter([("command".into(), json!("pip3 install mlx"))]);
        match classify_install_action("pip", Some(&action), &item) {
            InstallKind::Manual { command } => assert_eq!(command, "pip3 install mlx"),
            _ => panic!("expected manual pip"),
        }
    }

    #[test]
    fn prepare_model_is_recognized_and_empty_is_ready() {
        let item = json!({"id": "model", "model_id": "mlx-community/x"});
        match classify_install_action("prepare_model", None, &item) {
            InstallKind::PrepareModel { model, .. } => {
                assert_eq!(model, "mlx-community/x");
            }
            _ => panic!("expected prepare_model"),
        }
        match classify_install_action("", None, &json!({"id": "done"})) {
            InstallKind::Ready => {}
            _ => panic!("expected ready"),
        }
    }
}
