//! Feature switchboard — native (v11.6.0, WP-R2).
//!
//! Port of `latticeai/api/features.py` + `latticeai/services/feature_toggles.py`.
//! Persistence is `<data_dir>/feature_toggles.json` (same filename Python
//! uses). The file is not in I1's `state_files` map; the service has always
//! owned that path.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use axum::extract::{Path as AxumPath, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::body::{optional, parse_model, required};
use lattice_auth::{AuthState, OrderedMap};
use lattice_core::messages;
use serde_json::{json, Map, Value};

use crate::adminops::admin;
use crate::adminops::admin::{
    append_audit_event, audit_log_path, json_ok, language_from, message_error,
};

pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/api/features"),
    ("POST", "/api/features/:feature_id"),
];

pub const STORE_FILENAME: &str = "feature_toggles.json";
pub const STORE_VERSION: u32 = 1;

const TRUTHY: &[&str] = &["1", "true", "yes", "on"];
const FALSY: &[&str] = &["0", "false", "no", "off"];

#[derive(Clone, Copy)]
enum Kind {
    Toggle,
    Choice,
}

struct Choice {
    id: &'static str,
    label_key: &'static str,
    probe: Option<&'static str>,
}

struct Definition {
    id: &'static str,
    kind: Kind,
    env_var: &'static str,
    default: Value,
    caution: bool,
    parent: Option<&'static str>,
    choices: &'static [Choice],
    live: bool,
}

const VECTOR_CHOICES: &[Choice] = &[
    Choice {
        id: "brute",
        label_key: "features.vector_backend.choice.brute",
        probe: None,
    },
    Choice {
        id: "quantized",
        label_key: "features.vector_backend.choice.quantized",
        probe: None,
    },
    Choice {
        id: "hnsw",
        label_key: "features.vector_backend.choice.hnsw",
        probe: Some("hnsw"),
    },
];

const CATALOG: &[Definition] = &[
    Definition {
        id: "allow_multimodal",
        kind: Kind::Toggle,
        env_var: "LATTICEAI_ALLOW_MULTIMODAL",
        default: Value::Bool(false),
        caution: false,
        parent: None,
        choices: &[],
        live: true,
    },
    Definition {
        id: "video_ingest",
        kind: Kind::Toggle,
        env_var: "LATTICEAI_ALLOW_VIDEO",
        default: Value::Bool(true),
        caution: false,
        parent: Some("allow_multimodal"),
        choices: &[],
        live: true,
    },
    Definition {
        id: "vault_watch",
        kind: Kind::Toggle,
        env_var: "LATTICEAI_VAULT_WATCH",
        default: Value::Bool(false),
        caution: false,
        parent: None,
        choices: &[],
        live: true,
    },
    Definition {
        id: "brain_network",
        kind: Kind::Toggle,
        env_var: "LATTICEAI_BRAIN_NETWORK",
        default: Value::Bool(false),
        caution: true,
        parent: None,
        choices: &[],
        live: true,
    },
    Definition {
        id: "synthesis",
        kind: Kind::Toggle,
        env_var: "LATTICEAI_SYNTHESIS",
        default: Value::Bool(true),
        caution: false,
        parent: None,
        choices: &[],
        live: true,
    },
    Definition {
        id: "auto_vector_index",
        kind: Kind::Toggle,
        env_var: "LATTICEAI_AUTO_VECTOR_INDEX",
        default: Value::Bool(true),
        caution: false,
        parent: None,
        choices: &[],
        live: true,
    },
    Definition {
        id: "auto_late_fusion",
        kind: Kind::Toggle,
        env_var: "LATTICEAI_TEXT_IMAGE_FUSION",
        default: Value::Bool(false),
        caution: false,
        parent: None,
        choices: &[],
        live: true,
    },
    Definition {
        id: "fusion_rrf",
        kind: Kind::Toggle,
        env_var: "LATTICEAI_FUSION_RRF",
        default: Value::Bool(false),
        caution: false,
        parent: None,
        choices: &[],
        live: true,
    },
    Definition {
        id: "graph_expansion",
        kind: Kind::Toggle,
        env_var: "LATTICEAI_GRAPH_EXPANSION",
        default: Value::Bool(false),
        caution: false,
        parent: None,
        choices: &[],
        live: true,
    },
    Definition {
        id: "vector_backend",
        kind: Kind::Choice,
        env_var: "LATTICEAI_VECTOR_INDEX",
        default: Value::String(String::new()), // overwritten to "brute" in resolve()
        caution: false,
        parent: None,
        choices: VECTOR_CHOICES,
        live: true,
    },
];

pub type Probe = Arc<dyn Fn() -> (bool, String) + Send + Sync>;

#[derive(Clone)]
pub struct FeatureService {
    data_dir: PathBuf,
    probes: HashMap<String, Probe>,
    lock: Arc<Mutex<()>>,
}

impl FeatureService {
    pub fn new(data_dir: impl Into<PathBuf>) -> Self {
        Self {
            data_dir: data_dir.into(),
            probes: HashMap::new(),
            lock: Arc::new(Mutex::new(())),
        }
    }

    pub fn with_probe(mut self, name: impl Into<String>, probe: Probe) -> Self {
        self.probes.insert(name.into(), probe);
        self
    }

    fn path(&self) -> PathBuf {
        self.data_dir.join(STORE_FILENAME)
    }

    fn read(&self) -> Map<String, Value> {
        let Ok(text) = std::fs::read_to_string(self.path()) else {
            return Map::new();
        };
        let Ok(data) = serde_json::from_str::<Value>(&text) else {
            return Map::new();
        };
        data.get("features")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default()
    }

    fn write(&self, features: &Map<String, Value>) {
        let mut root = OrderedMap::new();
        root.insert("version", json!(STORE_VERSION));
        root.insert("features", Value::Object(features.clone()));
        if let Ok(text) = lattice_auth::pyjson::dumps_indent2(&root) {
            lattice_auth::atomic::write_text(&self.path(), &text);
        }
    }

    fn definition(id: &str) -> Option<&'static Definition> {
        CATALOG.iter().find(|d| d.id == id)
    }

    fn coerce(def: &Definition, value: &Value) -> Option<Value> {
        match def.kind {
            Kind::Choice => {
                let text = match value {
                    Value::String(s) => s.trim().to_ascii_lowercase(),
                    other => other.to_string().trim().to_ascii_lowercase(),
                };
                if def.choices.iter().any(|c| c.id == text) {
                    Some(json!(text))
                } else {
                    None
                }
            }
            Kind::Toggle => match value {
                Value::Bool(b) => Some(json!(*b)),
                other => {
                    let text = match other {
                        Value::String(s) => s.trim().to_ascii_lowercase(),
                        v => v.to_string().trim().to_ascii_lowercase(),
                    };
                    if TRUTHY.contains(&text.as_str()) {
                        Some(json!(true))
                    } else if FALSY.contains(&text.as_str()) {
                        Some(json!(false))
                    } else {
                        None
                    }
                }
            },
        }
    }

    fn env_value(def: &Definition) -> Option<Value> {
        let raw = std::env::var(def.env_var).ok()?.trim().to_string();
        if raw.is_empty() {
            return None;
        }
        Self::coerce(def, &json!(raw))
    }

    fn resolve(def: &Definition, stored: &Map<String, Value>) -> (Value, &'static str) {
        if let Some(raw) = stored.get(def.id) {
            if let Some(user) = Self::coerce(def, raw) {
                return (user, "user");
            }
        }
        if let Some(seeded) = Self::env_value(def) {
            return (seeded, "env");
        }
        (
            if def.id == "vector_backend" {
                json!("brute")
            } else {
                def.default.clone()
            },
            "default",
        )
    }

    fn availability(&self, choice: &Choice) -> (bool, String) {
        let Some(name) = choice.probe else {
            return (true, String::new());
        };
        let Some(probe) = self.probes.get(name) else {
            return (true, String::new());
        };
        probe()
    }

    fn render_choices(&self, def: &Definition, language: &str) -> Vec<Value> {
        def.choices
            .iter()
            .map(|choice| {
                let (available, reason) = self.availability(choice);
                let mut row = OrderedMap::new();
                row.insert("id", json!(choice.id));
                row.insert(
                    "label",
                    json!(messages::text(choice.label_key, language, &[])),
                );
                row.insert("available", json!(available));
                row.insert(
                    "detail",
                    if available {
                        Value::Null
                    } else {
                        json!(messages::text(
                            "features.choice.install_required",
                            language,
                            &[("reason", reason.as_str())],
                        ))
                    },
                );
                admin::json_from_ordered(&row)
            })
            .collect()
    }

    fn render(&self, def: &Definition, stored: &Map<String, Value>, language: &str) -> OrderedMap {
        let (current, source) = Self::resolve(def, stored);
        let mut out = OrderedMap::new();
        out.insert("id", json!(def.id));
        out.insert(
            "kind",
            json!(match def.kind {
                Kind::Toggle => "toggle",
                Kind::Choice => "choice",
            }),
        );
        out.insert(
            "label",
            json!(messages::text(
                &format!("features.{}.label", def.id),
                language,
                &[]
            )),
        );
        out.insert(
            "summary",
            json!(messages::text(
                &format!("features.{}.summary", def.id),
                language,
                &[]
            )),
        );
        out.insert(
            "default",
            if def.id == "vector_backend" {
                json!("brute")
            } else {
                def.default.clone()
            },
        );
        out.insert("current", current);
        out.insert("source", json!(source));
        out.insert("env_var", json!(def.env_var));
        out.insert("live", json!(def.live));
        out.insert("restart_required", json!(!def.live));
        out.insert(
            "caution",
            if def.caution {
                json!(messages::text(
                    &format!("features.{}.caution", def.id),
                    language,
                    &[],
                ))
            } else {
                Value::Null
            },
        );
        out.insert(
            "parent",
            def.parent.map(|p| json!(p)).unwrap_or(Value::Null),
        );
        out.insert("choices", json!(self.render_choices(def, language)));
        out
    }

    pub fn catalog(&self, language: &str) -> OrderedMap {
        let _g = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        let stored = self.read();
        drop(_g);
        let features: Vec<Value> = CATALOG
            .iter()
            .map(|d| admin::json_from_ordered(&self.render(d, &stored, language)))
            .collect();
        let mut out = OrderedMap::new();
        out.insert("features", json!(features));
        out.insert(
            "note",
            json!(messages::text("features.note", language, &[])),
        );
        out
    }

    pub fn set(
        &self,
        feature_id: &str,
        value: &Value,
        language: &str,
        user_email: Option<&str>,
        data_dir: &Path,
    ) -> Result<OrderedMap, FeatureError> {
        let def = Self::definition(feature_id).ok_or(FeatureError::Unknown)?;
        let coerced = Self::coerce(def, value).ok_or_else(|| {
            FeatureError::Invalid(messages::text(
                "features.invalid_value",
                language,
                &[("value", &value_as_display(value))],
            ))
        })?;
        if matches!(def.kind, Kind::Choice) {
            let id = coerced.as_str().unwrap_or("");
            let choice = def.choices.iter().find(|c| c.id == id).unwrap();
            let (available, reason) = self.availability(choice);
            if !available {
                return Err(FeatureError::Invalid(messages::text(
                    "features.choice.install_required",
                    language,
                    &[("reason", reason.as_str())],
                )));
            }
        }
        let _g = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        let mut stored = self.read();
        let previous = Self::resolve(def, &stored).0;
        stored.insert(def.id.to_string(), coerced.clone());
        self.write(&stored);
        drop(_g);
        let rendered = self.render(def, &stored, language);
        let mut payload = Map::new();
        payload.insert("feature".into(), json!(def.id));
        payload.insert("previous".into(), previous);
        payload.insert("value".into(), coerced);
        if let Some(email) = user_email {
            payload.insert("user_email".into(), json!(email));
        }
        append_audit_event(&audit_log_path(data_dir), "feature_toggle_changed", payload);
        Ok(rendered)
    }
}

fn value_as_display(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Bool(b) => {
            if *b {
                "true".into()
            } else {
                "false".into()
            }
        }
        other => other.to_string(),
    }
}

pub enum FeatureError {
    Unknown,
    Invalid(String),
}

#[derive(Clone)]
pub struct FeaturesState {
    pub auth: Arc<AuthState>,
    pub service: FeatureService,
    pub data_dir: PathBuf,
}

impl FeaturesState {
    pub fn new(auth: Arc<AuthState>, data_dir: impl Into<PathBuf>) -> Self {
        let data_dir = data_dir.into();
        Self {
            auth,
            service: FeatureService::new(&data_dir),
            data_dir,
        }
    }
}

impl axum::extract::FromRef<FeaturesState> for Arc<AuthState> {
    fn from_ref(s: &FeaturesState) -> Self {
        Arc::clone(&s.auth)
    }
}

pub fn router(state: FeaturesState) -> Router {
    Router::new()
        .route("/api/features", get(list_features))
        .route("/api/features/:feature_id", post(set_feature))
        .with_state(state)
}

async fn list_features(
    State(state): State<FeaturesState>,
    headers: HeaderMap,
) -> Result<Response, Response> {
    state.auth.require_user(&headers)?;
    Ok(json_ok(&state.service.catalog(language_from(&headers))))
}

async fn set_feature(
    State(state): State<FeaturesState>,
    AxumPath(feature_id): AxumPath<String>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Response, Response> {
    let user = state.auth.require_user(&headers)?;
    let language = language_from(&headers);
    let value = parse_feature_value(&body)?;
    match state.service.set(
        &feature_id,
        &value,
        language,
        Some(user.email.as_str()).filter(|s| !s.is_empty()),
        &state.data_dir,
    ) {
        Ok(rendered) => Ok(json_ok(&rendered)),
        Err(FeatureError::Unknown) => Err(message_error(
            400,
            "features.unknown",
            language,
            &[("feature", feature_id.as_str())],
        )),
        Err(FeatureError::Invalid(detail)) => {
            let mut body = OrderedMap::new();
            body.insert("detail", json!(detail));
            Err(admin::json_status(StatusCode::BAD_REQUEST, &body))
        }
    }
}

fn parse_feature_value(bytes: &[u8]) -> Result<Value, Response> {
    // Required `value: Union[bool, str]`. `parse_model` only accepts strings,
    // so we do the union ourselves and reuse its 422 shape for missing/type.
    match serde_json::from_slice::<Value>(bytes) {
        Ok(Value::Object(map)) => match map.get("value") {
            None => {
                // Field required — same envelope as FastAPI.
                let _ = parse_model(b"{}", &[required("value")]);
                Err(parse_model(b"{}", &[required("value")]).unwrap_err())
            }
            Some(Value::Bool(b)) => Ok(json!(*b)),
            Some(Value::String(s)) => Ok(json!(s)),
            Some(other) => {
                // Treat a number/null as a string so coerce can refuse it
                // with the 400 invalid-value message (fixture: "not-a-bool").
                if other.is_null() {
                    Err(parse_model(
                        &serde_json::to_vec(&json!({"value": other})).unwrap_or_default(),
                        &[optional("value")],
                    )
                    .err()
                    .unwrap_or_else(|| {
                        let mut body = OrderedMap::new();
                        body.insert("detail", json!("Field required"));
                        admin::json_status(StatusCode::UNPROCESSABLE_ENTITY, &body)
                    }))
                } else {
                    Ok(other.clone())
                }
            }
        },
        Ok(other) => Err(parse_model(
            &serde_json::to_vec(&other).unwrap_or_default(),
            &[required("value")],
        )
        .unwrap_err()),
        Err(_) => Err(parse_model(bytes, &[required("value")]).unwrap_err()),
    }
}
