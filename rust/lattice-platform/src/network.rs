//! Peer network sync — native port of `latticeai/api/network.py`.
//!
//! `POST /network/receive` authenticates paired devices by Ed25519 signature
//! (no session). Everything else is a signed-in user; pairing and push are
//! owner actions (`require_admin`). Device identity lives in
//! `device_identity.key`, the same state file Python uses.

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::useless_format,
    clippy::collapsible_str_replace,
    clippy::manual_repeat_n,
    clippy::module_inception
)]
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::body::Bytes;
use axum::extract::{Path as AxumPath, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};
use lattice_auth::{AuthState, OrderedMap};
use lattice_core::db::tables::state_files;
use lattice_core::db::RuntimeConfig;
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::project_sessions::{detail, json_ok, missing_fields, parse_json_object};

/// Mounted (method, path) pairs.
pub const MOUNTED: &[(&str, &str)] = &[
    ("GET", "/network/identity"),
    ("GET", "/network/peers"),
    ("POST", "/network/peers"),
    ("DELETE", "/network/peers/:peer_id"),
    ("POST", "/network/push/:peer_id"),
    ("POST", "/network/receive"),
];

const PEER_AUTH_WINDOW_SECONDS: u64 = 300;
const HEADER_DEVICE: &str = "x-lattice-device";
const HEADER_TIMESTAMP: &str = "x-lattice-timestamp";
const HEADER_NONCE: &str = "x-lattice-nonce";
const HEADER_SIGNATURE: &str = "x-lattice-signature";

/// Router state: auth, the data dir, and an optional graph-import seam.
#[derive(Clone)]
pub struct NetworkState {
    pub auth: Arc<AuthState>,
    pub config: Arc<RuntimeConfig>,
    pub identity: Arc<DeviceIdentity>,
    pub peers: Arc<PeerRegistry>,
    pub seam: Option<WorkerSeamClient>,
    pub graph: Option<lattice_core::graph_write::GraphWriter>,
}

impl NetworkState {
    pub fn new(
        auth: Arc<AuthState>,
        config: RuntimeConfig,
        seam: Option<WorkerSeamClient>,
    ) -> Self {
        let identity = Arc::new(DeviceIdentity::load_or_create(
            &config.state_file(state_files::DEVICE_IDENTITY),
        ));
        let peers = Arc::new(PeerRegistry::open(
            config.data_dir().join("brain_peers.json"),
        ));
        Self {
            auth,
            config: Arc::new(config),
            identity,
            peers,
            seam,
            graph: None,
        }
    }
}

/// Build the peer-network router.
pub fn router(state: NetworkState) -> Router {
    Router::new()
        .route("/network/identity", get(network_identity))
        .route("/network/peers", get(network_peers).post(network_pair))
        .route(
            "/network/peers/:peer_id",
            axum::routing::delete(network_unpair),
        )
        .route("/network/push/:peer_id", post(network_push))
        .route("/network/receive", post(network_receive))
        .with_state(state)
}

async fn network_identity(State(state): State<NetworkState>, headers: HeaderMap) -> Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    json_ok(state.identity.describe())
}

async fn network_peers(State(state): State<NetworkState>, headers: HeaderMap) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    let mut map = OrderedMap::new();
    map.insert("peers", json!(state.peers.list()));
    json_ok(map)
}

async fn network_pair(
    State(state): State<NetworkState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    let mut missing = Vec::new();
    if !object.contains_key("name") {
        missing.push("name");
    }
    if !object.contains_key("base_url") {
        missing.push("base_url");
    }
    if !object.contains_key("public_key") {
        missing.push("public_key");
    }
    if !missing.is_empty() {
        return missing_fields(&object, &missing);
    }
    let name = object.get("name").and_then(Value::as_str).unwrap_or("");
    let base_url = object.get("base_url").and_then(Value::as_str).unwrap_or("");
    let public_key = object
        .get("public_key")
        .and_then(Value::as_str)
        .unwrap_or("");
    match state.peers.add_peer(name, base_url, public_key) {
        Ok(peer) => {
            let mut map = OrderedMap::new();
            map.insert("status", json!("paired"));
            map.insert("peer", json!(peer));
            json_ok(map)
        }
        Err(message) => detail(StatusCode::BAD_REQUEST, &message),
    }
}

async fn network_unpair(
    State(state): State<NetworkState>,
    headers: HeaderMap,
    AxumPath(peer_id): AxumPath<String>,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    match state.peers.remove_peer(&peer_id) {
        Ok(result) => json_ok(result),
        Err(_) => detail(StatusCode::NOT_FOUND, &format!("Unknown peer: {peer_id}")),
    }
}

async fn network_push(
    State(state): State<NetworkState>,
    headers: HeaderMap,
    AxumPath(peer_id): AxumPath<String>,
    body: Bytes,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    let _workspace = if body.is_empty() {
        None
    } else {
        parse_json_object(&body).ok().and_then(|o| {
            o.get("workspace_id")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
    };
    if state.peers.get(&peer_id).is_none() {
        return detail(StatusCode::NOT_FOUND, &format!("Unknown peer: {peer_id}"));
    }
    detail(
        StatusCode::BAD_GATEWAY,
        "Push failed: peer exchange is not wired in this process",
    )
}

async fn network_receive(
    State(state): State<NetworkState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    match state.peers.verify_peer_request(&headers, &body) {
        Ok(peer) => {
            let artifact: Value = match serde_json::from_slice(&body) {
                Ok(v) => v,
                Err(_) => return detail(StatusCode::BAD_REQUEST, "body is not a JSON bundle"),
            };
            let signer = artifact
                .get("signature")
                .and_then(|s| s.get("public_key"))
                .and_then(Value::as_str)
                .unwrap_or("");
            if signer != peer.get("public_key").and_then(Value::as_str).unwrap_or("") {
                return detail(
                    StatusCode::FORBIDDEN,
                    "bundle signer does not match the paired peer",
                );
            }
            if let Some(graph) = state.graph.clone() {
                let data = artifact.as_object().cloned().unwrap_or_default();
                let request = lattice_core::graph_write::types::ImportRequest {
                    data,
                    mode: "merge".into(),
                    dry_run: false,
                };
                match tokio::task::spawn_blocking(move || graph.import_graph_data(&request)).await {
                    Ok(Ok(outcome)) => {
                        let result = outcome.to_json();
                        let mut map = OrderedMap::new();
                        if let Some(obj) = result.as_object() {
                            for (k, v) in obj {
                                map.insert(k.clone(), v.clone());
                            }
                        }
                        map.insert(
                            "peer",
                            json!({
                                "id": peer.get("id"),
                                "name": peer.get("name"),
                                "fingerprint": peer.get("fingerprint"),
                            }),
                        );
                        return json_ok(map);
                    }
                    Ok(Err(err)) => return detail(StatusCode::BAD_GATEWAY, &err.to_string()),
                    Err(err) => return detail(StatusCode::BAD_GATEWAY, &err.to_string()),
                }
            } else if let Some(seam) = &state.seam {
                match seam
                    .post_json(
                        "/worker/graph/mutate",
                        &json!({"op":"import_graph_data","args":{"data": artifact, "mode": "merge"}}),
                    )
                    .await
                {
                    Ok(result) => {
                        let mut map = OrderedMap::new();
                        if let Some(obj) = result.get("result").and_then(Value::as_object) {
                            for (k, v) in obj {
                                map.insert(k.clone(), v.clone());
                            }
                        }
                        map.insert(
                            "peer",
                            json!({
                                "id": peer.get("id"),
                                "name": peer.get("name"),
                                "fingerprint": peer.get("fingerprint"),
                            }),
                        );
                        json_ok(map)
                    }
                    Err(err) => detail(
                        StatusCode::from_u16(err.status().unwrap_or(502))
                            .unwrap_or(StatusCode::BAD_GATEWAY),
                        &err.to_string(),
                    ),
                }
            } else {
                detail(
                    StatusCode::BAD_REQUEST,
                    "Invalid Knowledge Graph export artifact.",
                )
            }
        }
        Err(message) => detail(StatusCode::FORBIDDEN, &message),
    }
}

/// Installation Ed25519 keypair. Private key never leaves the machine.
pub struct DeviceIdentity {
    signing: SigningKey,
    storage: &'static str,
}

impl DeviceIdentity {
    pub fn load_or_create(key_file: &Path) -> Self {
        let raw = if key_file.exists() {
            std::fs::read_to_string(key_file)
                .ok()
                .and_then(|text| decode_b64(text.trim()))
                .filter(|bytes| bytes.len() == 32)
        } else {
            None
        };
        let (bytes, created) = match raw {
            Some(bytes) => {
                let mut arr = [0u8; 32];
                arr.copy_from_slice(&bytes);
                (arr, false)
            }
            None => {
                let mut arr = [0u8; 32];
                let _ = getrandom::fill(&mut arr);
                if let Some(parent) = key_file.parent() {
                    let _ = std::fs::create_dir_all(parent);
                }
                let _ = std::fs::write(key_file, encode_b64(&arr));
                #[cfg(unix)]
                {
                    use std::os::unix::fs::PermissionsExt;
                    let _ =
                        std::fs::set_permissions(key_file, std::fs::Permissions::from_mode(0o600));
                }
                (arr, true)
            }
        };
        let _ = created;
        Self {
            signing: SigningKey::from_bytes(&bytes),
            storage: "file",
        }
    }

    pub fn public_key_b64(&self) -> String {
        encode_b64(self.signing.verifying_key().as_bytes())
    }

    pub fn fingerprint(&self) -> String {
        fingerprint_of_raw(self.signing.verifying_key().as_bytes())
    }

    pub fn describe(&self) -> OrderedMap {
        let mut map = OrderedMap::new();
        map.insert("fingerprint", json!(self.fingerprint()));
        map.insert("public_key", json!(self.public_key_b64()));
        map.insert("algorithm", json!("ed25519"));
        map.insert("storage", json!(self.storage));
        map
    }

    pub fn share_device(&self) -> OrderedMap {
        let mut map = OrderedMap::new();
        map.insert("fingerprint", json!(self.fingerprint()));
        map.insert("public_key", json!(self.public_key_b64()));
        map.insert("algorithm", json!("ed25519"));
        map
    }

    pub fn sign(&self, payload: &[u8]) -> String {
        encode_b64(&self.signing.sign(payload).to_bytes())
    }
}

/// `brain_peers.json` — the same pairing file Python writes.
pub struct PeerRegistry {
    path: PathBuf,
    seen_nonces: Mutex<HashMap<String, f64>>,
}

impl PeerRegistry {
    pub fn open(path: PathBuf) -> Self {
        Self {
            path,
            seen_nonces: Mutex::new(HashMap::new()),
        }
    }

    fn load(&self) -> Vec<OrderedMap> {
        let Ok(text) = std::fs::read_to_string(&self.path) else {
            return Vec::new();
        };
        serde_json::from_str::<Vec<OrderedMap>>(&text).unwrap_or_default()
    }

    fn save(&self, peers: &[OrderedMap]) {
        if let Some(parent) = self.path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if let Ok(text) = serde_json::to_string_pretty(peers) {
            lattice_auth::atomic::write_text(&self.path, &text);
        }
    }

    pub fn list(&self) -> Vec<OrderedMap> {
        self.load()
    }

    pub fn get(&self, peer_id: &str) -> Option<OrderedMap> {
        self.load()
            .into_iter()
            .find(|p| p.get("id").and_then(Value::as_str) == Some(peer_id))
    }

    pub fn add_peer(
        &self,
        name: &str,
        base_url: &str,
        public_key: &str,
    ) -> Result<OrderedMap, String> {
        let name = name.trim();
        let base_url = base_url.trim().trim_end_matches('/');
        let public_key = public_key.trim();
        if name.is_empty() || base_url.is_empty() || public_key.is_empty() {
            return Err("pairing requires name, base_url, and the peer's public key".into());
        }
        if !(base_url.starts_with("http://") || base_url.starts_with("https://")) {
            return Err("base_url must be an http(s) URL".into());
        }
        let fingerprint = fingerprint_of(public_key)?;
        let mut peers = self.load();
        if peers
            .iter()
            .any(|p| p.get("public_key").and_then(Value::as_str) == Some(public_key))
        {
            return Err("this device is already paired".into());
        }
        let mut peer = OrderedMap::new();
        peer.insert("id", json!(format!("peer-{}", hex_id(6))));
        peer.insert("name", json!(name));
        peer.insert("base_url", json!(base_url));
        peer.insert("public_key", json!(public_key));
        peer.insert("fingerprint", json!(fingerprint));
        peer.insert("added_at", json!(crate::project_sessions::now_iso_utc()));
        peers.push(peer.clone());
        self.save(&peers);
        Ok(peer)
    }

    pub fn remove_peer(&self, peer_id: &str) -> Result<OrderedMap, ()> {
        let peers = self.load();
        let kept: Vec<OrderedMap> = peers
            .into_iter()
            .filter(|p| p.get("id").and_then(Value::as_str) != Some(peer_id))
            .collect();
        let original = self.load();
        if kept.len() == original.len() {
            return Err(());
        }
        self.save(&kept);
        let mut map = OrderedMap::new();
        map.insert("status", json!("removed"));
        map.insert("peer_id", json!(peer_id));
        Ok(map)
    }

    pub fn verify_peer_request(
        &self,
        headers: &HeaderMap,
        body: &[u8],
    ) -> Result<OrderedMap, String> {
        let device = header(headers, HEADER_DEVICE);
        let timestamp = header(headers, HEADER_TIMESTAMP);
        let nonce = header(headers, HEADER_NONCE);
        let signature = header(headers, HEADER_SIGNATURE);
        if device.is_empty() || timestamp.is_empty() || nonce.is_empty() || signature.is_empty() {
            return Err("missing peer authentication headers".into());
        }
        let peer = self
            .load()
            .into_iter()
            .find(|p| p.get("public_key").and_then(Value::as_str) == Some(device))
            .ok_or_else(|| "device is not a paired peer".to_string())?;
        let ts: u64 = timestamp
            .parse()
            .map_err(|_| "invalid timestamp".to_string())?;
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let age = now.abs_diff(ts);
        if age > PEER_AUTH_WINDOW_SECONDS {
            return Err("request outside the freshness window".into());
        }
        {
            let mut seen = self.seen_nonces.lock().expect("nonce lock");
            if seen.contains_key(nonce) {
                return Err("replayed nonce".into());
            }
            seen.insert(nonce.to_string(), now as f64);
        }
        let payload = signing_payload(body, timestamp, nonce);
        if !verify_signature(device, &payload, signature) {
            return Err("peer request signature invalid".into());
        }
        Ok(peer)
    }
}

fn header<'a>(headers: &'a HeaderMap, name: &str) -> &'a str {
    headers
        .get(name)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
}

fn signing_payload(body: &[u8], timestamp: &str, nonce: &str) -> Vec<u8> {
    let digest = hex_sha256(body);
    format!("{digest}|{timestamp}|{nonce}").into_bytes()
}

fn hex_sha256(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn encode_b64(bytes: &[u8]) -> String {
    URL_SAFE_NO_PAD.encode(bytes)
}

fn decode_b64(text: &str) -> Option<Vec<u8>> {
    let text = text.trim();
    if let Ok(raw) = URL_SAFE_NO_PAD.decode(text) {
        return Some(raw);
    }
    let pad = (4 - text.len() % 4) % 4;
    let mut padded = text.to_string();
    padded.extend(std::iter::repeat('=').take(pad));
    base64::engine::general_purpose::URL_SAFE
        .decode(&padded)
        .ok()
        .or_else(|| base64::engine::general_purpose::STANDARD.decode(padded).ok())
}

fn fingerprint_of_raw(raw: &[u8]) -> String {
    let digest = hex_sha256(raw);
    digest
        .as_bytes()
        .chunks(4)
        .take(4)
        .map(|c| std::str::from_utf8(c).unwrap_or("0000"))
        .collect::<Vec<_>>()
        .join(":")
}

fn fingerprint_of(public_key_b64: &str) -> Result<String, String> {
    let raw = decode_b64(public_key_b64)
        .ok_or_else(|| "public_key is not a valid Ed25519 key: Invalid base64".to_string())?;
    if raw.len() != 32 {
        return Err(
            "public_key is not a valid Ed25519 key: An Ed25519 public key is 32 bytes long".into(),
        );
    }
    let mut arr = [0u8; 32];
    arr.copy_from_slice(&raw);
    VerifyingKey::from_bytes(&arr)
        .map_err(|exc| format!("public_key is not a valid Ed25519 key: {exc}"))?;
    Ok(fingerprint_of_raw(&raw))
}

fn verify_signature(public_key_b64: &str, payload: &[u8], signature_b64: &str) -> bool {
    let Ok(raw) = decode_b64(public_key_b64).ok_or(()).and_then(|b| {
        if b.len() != 32 {
            return Err(());
        }
        let mut arr = [0u8; 32];
        arr.copy_from_slice(&b);
        Ok(arr)
    }) else {
        return false;
    };
    let Ok(sig_raw) = decode_b64(signature_b64).ok_or(()).and_then(|b| {
        if b.len() != 64 {
            return Err(());
        }
        let mut arr = [0u8; 64];
        arr.copy_from_slice(&b);
        Ok(arr)
    }) else {
        return false;
    };
    let Ok(key) = VerifyingKey::from_bytes(&raw) else {
        return false;
    };
    let sig = Signature::from_bytes(&sig_raw);
    key.verify(payload, &sig).is_ok()
}

fn hex_id(nbytes: usize) -> String {
    let mut buf = vec![0u8; nbytes];
    let _ = getrandom::fill(&mut buf);
    buf.iter().map(|b| format!("{b:02x}")).collect()
}
