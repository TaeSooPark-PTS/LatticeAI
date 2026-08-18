//! Realtime presence / feed / SSE — native port of `latticeai/api/realtime.py`.
//!
//! `/activity` is a STATIC page shell owned by `ui_redirects` (I4) and is
//! deliberately not remounted here.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};

use axum::body::{Body, Bytes};
use axum::extract::{Query, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::{AuthState, OrderedMap};
use serde_json::{json, Value};

use crate::workspaceos::project_sessions::{detail, json_ok, now_iso_utc, parse_json_object};

/// Native mounts. `/activity` lives with the UI redirects.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/realtime/stream"),
    ("GET", "/realtime/feed"),
    ("GET", "/realtime/presence"),
    ("POST", "/realtime/presence/join"),
    ("POST", "/realtime/presence/leave"),
];

const REALTIME_VERSION: &str = "2.2.0";
const FEED_LIMIT: usize = 200;

/// Router state.
#[derive(Clone)]
pub struct RealtimeState {
    pub auth: Arc<AuthState>,
    pub bus: Arc<RealtimeBus>,
}

impl RealtimeState {
    pub fn new(auth: Arc<AuthState>) -> Self {
        Self {
            auth,
            bus: Arc::new(RealtimeBus::new()),
        }
    }
}

/// Build the realtime router (no `/activity`).
pub fn router(state: RealtimeState) -> Router {
    Router::new()
        .route("/realtime/stream", get(realtime_stream))
        .route("/realtime/feed", get(realtime_feed))
        .route("/realtime/presence", get(realtime_presence))
        .route("/realtime/presence/join", post(realtime_join))
        .route("/realtime/presence/leave", post(realtime_leave))
        .with_state(state)
}

fn allowed_scopes(identity: &lattice_auth::Identity) -> Option<HashSet<String>> {
    if identity.email.is_empty() {
        None
    } else {
        let mut set = HashSet::new();
        set.insert("personal".into());
        Some(set)
    }
}

async fn realtime_stream(State(state): State<RealtimeState>, headers: HeaderMap) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let scope = allowed_scopes(&identity);
    let events = state.bus.recent(10, scope.as_ref());
    let mut body = String::new();
    for event in events {
        body.push_str(&sse_format(&event));
    }
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "text/event-stream; charset=utf-8")
        .header(header::CACHE_CONTROL, "no-cache")
        .header("x-accel-buffering", "no")
        .header(header::CONNECTION, "keep-alive")
        .body(Body::from(body))
        .unwrap_or_else(|_| Response::new(Body::empty()))
}

async fn realtime_feed(
    State(state): State<RealtimeState>,
    headers: HeaderMap,
    Query(query): Query<HashMap<String, String>>,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let limit = query
        .get("limit")
        .and_then(|s| s.parse::<usize>().ok())
        .unwrap_or(50);
    let scope = allowed_scopes(&identity);
    let events = state.bus.recent(limit, scope.as_ref());
    let contracts: Vec<Value> = events
        .iter()
        .filter_map(|e| e.get("contract").cloned())
        .collect();
    let mut map = OrderedMap::new();
    map.insert("events", json!(events));
    map.insert("contracts", json!(contracts));
    map.insert("stats", json!(state.bus.stats()));
    json_ok(map)
}

async fn realtime_presence(State(state): State<RealtimeState>, headers: HeaderMap) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let scope = allowed_scopes(&identity);
    let mut map = OrderedMap::new();
    map.insert("presence", json!(state.bus.presence(scope.as_ref())));
    map.insert("stats", json!(state.bus.stats()));
    json_ok(map)
}

async fn realtime_join(
    State(state): State<RealtimeState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let object = if body.is_empty() {
        serde_json::Map::new()
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(refusal) => return refusal,
        }
    };
    let scope = allowed_scopes(&identity);
    let mut workspace_id = object
        .get("workspace_id")
        .and_then(Value::as_str)
        .map(str::to_string);
    if let Some(scope) = scope.as_ref() {
        if workspace_id.is_none() {
            if scope.is_empty() {
                return detail(
                    StatusCode::FORBIDDEN,
                    "No accessible workspace for presence.",
                );
            }
            workspace_id = Some(if scope.contains("personal") {
                "personal".into()
            } else {
                let mut ids: Vec<_> = scope.iter().cloned().collect();
                ids.sort();
                ids.into_iter().next().unwrap_or_default()
            });
        } else if let Some(ws) = workspace_id.as_deref() {
            if !scope.contains(ws) {
                return detail(StatusCode::FORBIDDEN, "Workspace presence access denied.");
            }
        }
    }
    let client_id = object
        .get("client_id")
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(token8);
    let user = if identity.email.is_empty() {
        None
    } else {
        Some(identity.email.as_str())
    };
    match state.bus.join(&client_id, user, workspace_id.as_deref()) {
        Ok(record) => {
            let mut map = OrderedMap::new();
            map.insert("presence", json!(record));
            json_ok(map)
        }
        Err(message) => detail(StatusCode::FORBIDDEN, &message),
    }
}

async fn realtime_leave(
    State(state): State<RealtimeState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match state.auth.require_user(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    let object = if body.is_empty() {
        serde_json::Map::new()
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(refusal) => return refusal,
        }
    };
    if let Some(client_id) = object.get("client_id").and_then(Value::as_str) {
        let user = if identity.email.is_empty() {
            None
        } else {
            Some(identity.email.as_str())
        };
        if let Err(message) = state.bus.leave(client_id, user) {
            return detail(StatusCode::FORBIDDEN, &message);
        }
    }
    let mut map = OrderedMap::new();
    map.insert("status", json!("ok"));
    json_ok(map)
}

fn sse_format(event: &OrderedMap) -> String {
    let payload = serde_json::to_string(event).unwrap_or_else(|_| "{}".into());
    format!("data: {payload}\n\n")
}

fn token8() -> String {
    let mut buf = [0u8; 8];
    let _ = getrandom::fill(&mut buf);
    base64::Engine::encode(&base64::engine::general_purpose::URL_SAFE_NO_PAD, buf)
}

/// In-process bus. Same shape as `latticeai.core.realtime.RealtimeBus`.
pub struct RealtimeBus {
    feed: Mutex<Vec<OrderedMap>>,
    presence: Mutex<HashMap<String, OrderedMap>>,
    seq: Mutex<u64>,
}

impl RealtimeBus {
    pub fn new() -> Self {
        Self {
            feed: Mutex::new(Vec::new()),
            presence: Mutex::new(HashMap::new()),
            seq: Mutex::new(0),
        }
    }

    pub fn publish(&self, event: OrderedMap) -> OrderedMap {
        let mut seq = self.seq.lock().expect("seq");
        *seq += 1;
        let n = *seq;
        drop(seq);
        let mut enriched = OrderedMap::new();
        enriched.insert("seq", json!(n));
        enriched.insert("received_at", json!(now_iso_utc()));
        enriched.insert(
            "area",
            event.get("area").cloned().unwrap_or(json!("workspace")),
        );
        enriched.insert(
            "event_type",
            event.get("event_type").cloned().unwrap_or(json!("event")),
        );
        enriched.insert(
            "workspace_id",
            event.get("workspace_id").cloned().unwrap_or(Value::Null),
        );
        enriched.insert(
            "payload",
            event.get("payload").cloned().unwrap_or(json!({})),
        );
        for (k, v) in event.iter() {
            if !matches!(k, "area" | "event_type" | "workspace_id" | "payload") {
                enriched.insert(k, v.clone());
            }
        }
        enriched.insert("contract", json!(realtime_contract(&enriched)));
        let mut feed = self.feed.lock().expect("feed");
        feed.push(enriched.clone());
        if feed.len() > FEED_LIMIT {
            let drain = feed.len() - FEED_LIMIT;
            feed.drain(0..drain);
        }
        enriched
    }

    pub fn recent(&self, limit: usize, scope: Option<&HashSet<String>>) -> Vec<OrderedMap> {
        let feed = self.feed.lock().expect("feed");
        let mut events: Vec<OrderedMap> = feed
            .iter()
            .filter(|e| match scope {
                None => true,
                Some(set) => e
                    .get("workspace_id")
                    .and_then(Value::as_str)
                    .map(|w| set.contains(w))
                    .unwrap_or(false),
            })
            .cloned()
            .collect();
        let keep = limit.clamp(1, FEED_LIMIT);
        if events.len() > keep {
            events = events.split_off(events.len() - keep);
        }
        events.reverse();
        events
    }

    pub fn join(
        &self,
        client_id: &str,
        user: Option<&str>,
        workspace_id: Option<&str>,
    ) -> Result<OrderedMap, String> {
        let stamp = now_iso_utc();
        let mut record = OrderedMap::new();
        record.insert("client_id", json!(client_id));
        record.insert("user", json!(user));
        record.insert("workspace_id", json!(workspace_id));
        record.insert("joined_at", json!(stamp));
        record.insert("last_seen", json!(stamp));
        {
            let mut presence = self.presence.lock().expect("presence");
            if let Some(existing) = presence.get(client_id) {
                if existing.get("user").and_then(Value::as_str) != user {
                    return Err("Presence client belongs to another user.".into());
                }
            }
            presence.insert(client_id.to_string(), record.clone());
        }
        let mut event = OrderedMap::new();
        event.insert("area", json!("presence"));
        event.insert("event_type", json!("join"));
        event.insert("workspace_id", json!(workspace_id));
        event.insert("payload", json!({"user": user, "client_id": client_id}));
        self.publish(event);
        Ok(record)
    }

    pub fn leave(&self, client_id: &str, user: Option<&str>) -> Result<(), String> {
        let record = {
            let mut presence = self.presence.lock().expect("presence");
            if let Some(existing) = presence.get(client_id) {
                if user.is_some() && existing.get("user").and_then(Value::as_str) != user {
                    return Err("Presence client belongs to another user.".into());
                }
            }
            presence.remove(client_id)
        };
        if let Some(record) = record {
            let mut event = OrderedMap::new();
            event.insert("area", json!("presence"));
            event.insert("event_type", json!("leave"));
            event.insert(
                "workspace_id",
                record.get("workspace_id").cloned().unwrap_or(Value::Null),
            );
            event.insert("payload", json!({"client_id": client_id}));
            self.publish(event);
        }
        Ok(())
    }

    pub fn presence(&self, scope: Option<&HashSet<String>>) -> Vec<OrderedMap> {
        let presence = self.presence.lock().expect("presence");
        presence
            .values()
            .filter(|r| match scope {
                None => true,
                Some(set) => r
                    .get("workspace_id")
                    .and_then(Value::as_str)
                    .map(|w| set.contains(w))
                    .unwrap_or(false),
            })
            .cloned()
            .collect()
    }

    pub fn stats(&self) -> OrderedMap {
        let mut map = OrderedMap::new();
        map.insert("version", json!(REALTIME_VERSION));
        map.insert("subscribers", json!(0));
        map.insert("presence", json!(self.presence.lock().expect("p").len()));
        map.insert("feed_size", json!(self.feed.lock().expect("f").len()));
        map.insert("transport", json!("sse"));
        map
    }
}

impl Default for RealtimeBus {
    fn default() -> Self {
        Self::new()
    }
}

fn realtime_contract(event: &OrderedMap) -> OrderedMap {
    let seq = event.get("seq").cloned().unwrap_or(json!(0));
    let payload = event.get("payload").cloned().unwrap_or(json!({}));
    let area = event
        .get("area")
        .and_then(Value::as_str)
        .unwrap_or("workspace");
    let event_type = event
        .get("event_type")
        .and_then(Value::as_str)
        .unwrap_or("event");
    let received = event.get("received_at").cloned().unwrap_or(Value::Null);
    let mut map = OrderedMap::new();
    map.insert("run_id", Value::Null);
    map.insert("agent_id", json!(format!("realtime:{area}")));
    map.insert("runtime", json!("realtime"));
    map.insert("mode", json!("event"));
    map.insert("goal", json!(event_type));
    map.insert("roles", json!([]));
    map.insert("current_role", Value::Null);
    map.insert("retries", json!(0));
    map.insert(
        "timeline",
        json!([{
            "event": event_type,
            "timestamp": received,
            "payload": payload,
        }]),
    );
    map.insert(
        "artifacts",
        json!([{ "type": "realtime_payload", "payload": payload }]),
    );
    map.insert("blocking_reasons", json!([]));
    map.insert("is_terminal", json!(false));
    map.insert("family", json!("agent-run-contract/v1"));
    map.insert("schema_version", json!("realtime-event-contract/v1"));
    map.insert("kind", json!("realtime_event"));
    let seq_label = seq
        .as_u64()
        .map(|n| n.to_string())
        .unwrap_or_else(|| seq.to_string());
    map.insert("id", json!(format!("rt:{seq_label}")));
    map.insert("status", json!(event_type));
    map.insert("timestamp", received);
    map
}
