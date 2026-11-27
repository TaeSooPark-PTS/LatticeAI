use std::io::{Cursor, Read, Write};
use std::path::{Path, PathBuf};

use axum::body::Bytes;
use axum::extract::{Path as AxumPath, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_auth::OrderedMap;
use lattice_core::graph_write::share_import::SharedSubgraphImport;
use serde_json::{json, Value};

use crate::adminops::admin::enterprise::{
    load_org_governance_policy, normalize_tenant_scope, validate_tenant_scope,
};
use crate::governance::review_queue::store::{
    create_review_item, load_review_item, transition_approve, ReviewError,
};
use crate::governance::review_queue::GovernanceState;
use crate::http_reply::{self, detail, json_ok, message_detail, missing_body, parse_json_object};

use super::archive::missing_archive;
use super::archive_tables::array_rows;
use super::crypto::{
    seal_and_write_archive, sha256_hex, validate_zip_entries, verify_and_decrypt_archive,
    DecryptionKey, EncryptionMode, ARCHIVE_FORMAT, ARCHIVE_FORMAT_VERSION,
};
use super::postgres::stamp;
use super::status::{brain_network_on, require_graph, schema_versions};
use super::*;

const SUBGRAPH_ENTRY: &str = "bundle.json";
const SHARE_REVIEW_SOURCE: &str = "kg_change_digest";
const SHARE_REVIEW_KIND: &str = "kg_share_bundle";
const UNSIGNED_BUNDLE: &str =
    "Shared subgraph bundles must carry a valid Ed25519 signature; unsigned or altered bundles are refused.";

pub(crate) async fn share_status(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.auth.require_user(&headers) {
        return refusal;
    }
    let enabled = brain_network_on();
    let mut gate = OrderedMap::new();
    gate.insert("name", json!("brain_network"));
    gate.insert("flag", json!(BRAIN_NETWORK_ENV));
    gate.insert("enabled", json!(enabled));
    gate.insert("default", json!(false));
    gate.insert("source", json!("resolver"));
    gate.insert("detail", json!(BRAIN_NETWORK_DISABLED_EN));
    let mut map = OrderedMap::new();
    map.insert("enabled", json!(enabled));
    map.insert("flag", json!(BRAIN_NETWORK_ENV));
    map.insert("format", json!(SUBGRAPH_FORMAT));
    map.insert("format_version", json!(1));
    map.insert("graph_available", json!(state.graph_available()));
    map.insert("signing", json!(true));
    map.insert("device", json!(state.identity.share_device()));
    map.insert("proposal_cap", json!(200));
    map.insert("encryption", json!(["passphrase", "recipient_public_key"]));
    map.insert("recipient_public_key_encryption", json!(true));
    map.insert("sealed_box_algorithm", json!(SEALED_BOX_ALGORITHM));
    map.insert("gate", json!(gate));
    if enabled {
        map.insert("detail", Value::Null);
    } else {
        let detail = lattice_core::messages::text(
            "portability.brain_network_disabled",
            http_reply::language_of(&headers),
            &[],
        );
        map.insert("detail", json!(detail));
    }
    json_ok(map)
}

#[path = "share_bundle.rs"]
mod bundle;
pub(super) use bundle::emit_portability_audit;
pub(super) use bundle::{
    belongs_to_other_workspace, foreign_conversation_nodes, is_node_share_blocked,
    matches_workspace_boundary, metadata_of, node_workspace, v2_workspaces,
};
use bundle::{build_subgraph_bundle, verify_subgraph_bundle_signature, ShareViewer};

fn sender_is_paired_peer(state: &PortabilityState, bundle: &Value) -> bool {
    let Some(signer) = bundle
        .get("signature")
        .and_then(|s| s.get("public_key"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|key| !key.is_empty())
    else {
        return false;
    };
    crate::knowledge::network::PeerRegistry::open(
        state
            .config
            .data_dir()
            .join(crate::knowledge::network::PEERS_FILE),
    )
    .list()
    .iter()
    .any(|peer| {
        peer.get("public_key")
            .and_then(Value::as_str)
            .map(str::trim)
            == Some(signer)
    })
}

fn share_viewer(state: &PortabilityState, email: &str) -> ShareViewer {
    ShareViewer {
        viewer: state.auth.graph_viewer(email),
        require_workspace: state.auth.effective_require_auth(),
    }
}

fn share_disabled(headers: &HeaderMap) -> Response {
    message_detail(403, "portability.brain_network_disabled", headers)
}

pub(crate) async fn share_recipient_key(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    if !brain_network_on() {
        return share_disabled(&headers);
    }
    let mut map = OrderedMap::new();
    map.insert("available", json!(true));
    map.insert("algorithm", json!(SEALED_BOX_ALGORITHM));
    map.insert("signing_algorithm", json!("ed25519"));
    map.insert("public_key", json!(state.identity.x25519_public_key_b64()));
    map.insert(
        "x25519_public_key",
        json!(state.identity.x25519_public_key_b64()),
    );
    map.insert("ed25519_public_key", json!(state.identity.public_key_b64()));
    map.insert("fingerprint", json!(state.identity.fingerprint()));
    json_ok(map)
}

pub(crate) async fn share_export(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match state.auth.require_admin(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    if !brain_network_on() {
        return share_disabled(&headers);
    }
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    let scope = share_viewer(&state, &identity.email);
    match build_subgraph_bundle(&state, &object, &scope) {
        Ok((bundle, counts, excluded_local_only)) => {
            emit_portability_audit(
                &state,
                "share_exported",
                json!({
                    "counts": counts,
                    "excluded_local_only": excluded_local_only,
                }),
            );
            let sig_ok = verify_subgraph_bundle_signature(&bundle).unwrap_or(false);
            let mut map = OrderedMap::new();
            map.insert("format", json!(SUBGRAPH_FORMAT));
            map.insert("format_version", json!(1));
            map.insert("counts", counts);
            map.insert("excluded_local_only", json!(excluded_local_only));
            map.insert("signed", json!(sig_ok));
            map.insert("signature_verified", json!(sig_ok));
            map.insert(
                "signature",
                bundle.get("signature").cloned().unwrap_or(Value::Null),
            );
            map.insert("bundle", bundle.clone());
            map.insert("artifact", bundle);
            json_ok(map)
        }
        Err(err) => detail(StatusCode::BAD_REQUEST, &err),
    }
}

pub(crate) async fn share_archive(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match state.auth.require_admin(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    if !brain_network_on() {
        return share_disabled(&headers);
    }
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    let scope = share_viewer(&state, &identity.email);
    let passphrase = object
        .get("passphrase")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());
    let recipient_key = object
        .get("recipient_public_key")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());

    let mode = match (passphrase, recipient_key) {
        (Some(pw), None) => EncryptionMode::Passphrase(pw),
        (None, Some(pk)) => EncryptionMode::RecipientPublicKey { public_key_b64: pk },
        _ => {
            return detail(
                StatusCode::BAD_REQUEST,
                "Choose exactly one encryption mechanism: passphrase or recipient_public_key.",
            );
        }
    };

    let (bundle, counts, excluded_local_only) = match build_subgraph_bundle(&state, &object, &scope)
    {
        Ok(v) => v,
        Err(err) => return detail(StatusCode::BAD_REQUEST, &err),
    };

    let dest = object
        .get("path")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            state
                .exports_dir()
                .join(format!("kg-share-{}.latticebrain", stamp()))
        });

    let bundle_bytes = match serde_json::to_vec_pretty(&bundle) {
        Ok(b) => b,
        Err(e) => return detail(StatusCode::BAD_REQUEST, &e.to_string()),
    };
    let bundle_sha = sha256_hex(&bundle_bytes);

    let mut inner_buf = Cursor::new(Vec::new());
    {
        let mut inner_zip = zip::ZipWriter::new(&mut inner_buf);
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);
        if let Err(e) = inner_zip.start_file(SUBGRAPH_ENTRY, options) {
            return detail(StatusCode::BAD_REQUEST, &e.to_string());
        }
        if let Err(e) = inner_zip.write_all(&bundle_bytes) {
            return detail(StatusCode::BAD_REQUEST, &e.to_string());
        }
        if let Err(e) = inner_zip.finish() {
            return detail(StatusCode::BAD_REQUEST, &e.to_string());
        }
    }
    let plaintext = inner_buf.into_inner();

    let encryption_label = match mode {
        EncryptionMode::Passphrase(_) => "passphrase",
        EncryptionMode::RecipientPublicKey { .. } => "recipient_public_key",
    };

    let mut manifest = schema_versions();
    manifest.insert("subgraph_format", json!(SUBGRAPH_FORMAT));
    manifest.insert("counts", counts.clone());
    manifest.insert("excluded_local_only", json!(excluded_local_only));
    manifest.insert("checksums", json!({ "bundle_sha256": bundle_sha }));

    let (header, manifest_val) = match seal_and_write_archive(
        &dest,
        "subgraph",
        &plaintext,
        &mut manifest,
        mode,
        &state.identity,
    ) {
        Ok(v) => v,
        Err(err) => return detail(StatusCode::BAD_REQUEST, &err),
    };

    emit_portability_audit(
        &state,
        "share_archive_created",
        json!({
            "path": dest.to_string_lossy(),
            "encryption_mode": encryption_label,
            "counts": counts,
        }),
    );

    let bytes = std::fs::metadata(&dest).map(|m| m.len()).unwrap_or(0);
    let mut map = OrderedMap::new();
    map.insert("status", json!("created"));
    map.insert("path", json!(dest.to_string_lossy().to_string()));
    map.insert("bytes", json!(bytes));
    map.insert("encryption_mode", json!(encryption_label));
    map.insert("format", json!(ARCHIVE_FORMAT));
    map.insert("format_version", json!(ARCHIVE_FORMAT_VERSION));
    map.insert("counts", counts);
    map.insert("excluded_local_only", json!(excluded_local_only));
    map.insert("header", header);
    map.insert("manifest", manifest_val);
    json_ok(map)
}

pub(crate) async fn share_import(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let identity = match state.auth.require_admin(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    if !brain_network_on() {
        return share_disabled(&headers);
    }
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };

    let dry_run = object
        .get("dry_run")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let workspace_id = object
        .get("workspace_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());
    let passphrase = object
        .get("passphrase")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());

    let bundle = if let Some(artifact) = object.get("artifact").filter(|v| v.is_object()) {
        artifact.clone()
    } else if let Some(path) = object
        .get("path")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        let p = Path::new(path);
        if !p.exists() {
            return missing_archive(path);
        }
        let decrypted = match verify_and_decrypt_archive(
            p,
            DecryptionKey::PassphraseOrIdentity {
                passphrase,
                identity: &state.identity,
            },
        ) {
            Ok(d) => d,
            Err(err) => return detail(StatusCode::BAD_REQUEST, &err),
        };
        let cursor = Cursor::new(&decrypted.plaintext);
        let mut inner_zip = match zip::ZipArchive::new(cursor) {
            Ok(z) => z,
            Err(e) => return detail(StatusCode::BAD_REQUEST, &e.to_string()),
        };
        if let Err(err) = validate_zip_entries(&mut inner_zip) {
            return detail(StatusCode::BAD_REQUEST, &err);
        }
        let mut entry = match inner_zip.by_name(SUBGRAPH_ENTRY) {
            Ok(e) => e,
            Err(_) => {
                return detail(
                    StatusCode::BAD_REQUEST,
                    "Archive does not contain a shared subgraph bundle.",
                )
            }
        };
        let mut text = String::new();
        if let Err(e) = entry.read_to_string(&mut text) {
            return detail(StatusCode::BAD_REQUEST, &e.to_string());
        }
        match serde_json::from_str::<Value>(&text) {
            Ok(v) if v.is_object() => v,
            _ => return detail(StatusCode::BAD_REQUEST, "Corrupt shared subgraph JSON."),
        }
    } else {
        return detail(
            StatusCode::BAD_REQUEST,
            "Either artifact or path must be provided to import shared knowledge.",
        );
    };

    let signature_verified = match verify_subgraph_bundle_signature(&bundle) {
        Ok(true) => true,
        Ok(false) => return detail(StatusCode::BAD_REQUEST, UNSIGNED_BUNDLE),
        Err(err) => return detail(StatusCode::BAD_REQUEST, &err),
    };
    let sender_trusted = sender_is_paired_peer(&state, &bundle);
    let ignored_sources = array_rows(&bundle, "knowledge_sources").len();

    let nodes = array_rows(&bundle, "nodes").len();
    let edges = array_rows(&bundle, "edges").len();
    let chunks = array_rows(&bundle, "chunks").len();
    let provenance = array_rows(&bundle, "provenance").len();
    let counts = json!({
        "nodes": nodes,
        "edges": edges,
        "chunks": chunks,
        "provenance": provenance,
    });

    if dry_run {
        let mut map = OrderedMap::new();
        map.insert("status", json!("dry_run"));
        map.insert("dry_run", json!(true));
        map.insert("staged", json!(false));
        map.insert("signature_verified", json!(signature_verified));
        map.insert("sender_trusted", json!(sender_trusted));
        map.insert("counts", counts);
        return json_ok(map);
    }

    let gov = GovernanceState::open(
        state.auth.clone(),
        state.config.data_dir(),
        state.config.data_dir(),
        None,
    );
    let sender_fp = bundle
        .get("signature")
        .and_then(|s| s.get("fingerprint"))
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_string();
    let title = format!("Shared Subgraph ({nodes} nodes, {edges} edges) from {sender_fp}");
    let summary = format!(
        "Staged selective subgraph package ({nodes} nodes, {edges} edges, {chunks} chunks, {provenance} provenance records; {ignored_sources} knowledge sources ignored) awaiting review approval. Existing local items are never overwritten."
    );
    let provenance_meta = json!({
        "sender_fingerprint": sender_fp,
        "signature_verified": signature_verified,
        "sender_trusted": sender_trusted,
        "format": bundle.get("format").cloned().unwrap_or_else(|| json!(SUBGRAPH_FORMAT)),
        "received_at": http_reply::now_iso_utc(),
        "workspace_id": workspace_id,
    });
    let user_email = if identity.email.is_empty() {
        None
    } else {
        Some(identity.email.as_str())
    };
    let review_item = create_review_item(
        &gov,
        &title,
        &summary,
        SHARE_REVIEW_SOURCE,
        SHARE_REVIEW_KIND,
        bundle,
        provenance_meta,
        user_email,
        workspace_id,
    );
    let proposal_id = review_item
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();

    emit_portability_audit(
        &state,
        "share_imported",
        json!({
            "proposal_id": proposal_id,
            "sender_fingerprint": sender_fp,
            "counts": counts,
            "workspace_id": workspace_id,
        }),
    );

    let mut map = OrderedMap::new();
    map.insert("staged", json!(true));
    map.insert("status", json!("pending_review"));
    map.insert("dry_run", json!(false));
    map.insert("signature_verified", json!(signature_verified));
    map.insert("sender_trusted", json!(sender_trusted));
    map.insert("proposal_id", json!(proposal_id));
    map.insert("counts", counts);
    map.insert("review_item", review_item);
    json_ok(map)
}

pub(crate) async fn share_accept(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    AxumPath(item_id): AxumPath<String>,
    body: Bytes,
) -> Response {
    if body.is_empty() {
        return missing_body();
    }
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    let identity = match state.auth.require_admin(&headers) {
        Ok(id) => id,
        Err(refusal) => return refusal,
    };
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    let workspace_id = object
        .get("workspace_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());

    let gov = GovernanceState::open(
        state.auth.clone(),
        state.config.data_dir(),
        state.config.data_dir(),
        None,
    );
    let stored = match load_review_item(&gov, &item_id, None) {
        Ok(item) => item,
        Err(ReviewError::NotFound) => {
            return message_detail(404, "review.item_not_found", &headers)
        }
        Err(ReviewError::Conflict(msg)) => return detail(StatusCode::CONFLICT, &msg),
        Err(ReviewError::Invalid(msg)) => return detail(StatusCode::UNPROCESSABLE_ENTITY, &msg),
        Err(ReviewError::Failed(msg)) => return detail(StatusCode::BAD_REQUEST, &msg),
    };
    let stored_ws = stored.get("workspace_id").and_then(Value::as_str);
    if let Some(requested_ws) = workspace_id {
        if let Err(_msg) = validate_tenant_scope(
            state.config.data_dir(),
            Some(requested_ws),
            stored_ws,
            if identity.email.is_empty() {
                None
            } else {
                Some(identity.email.as_str())
            },
            "share_accept",
        ) {
            return message_detail(404, "review.item_not_found", &headers);
        }
    }
    let actor_org = normalize_tenant_scope(workspace_id.or(stored_ws));
    let org_policy = load_org_governance_policy(state.config.data_dir(), actor_org);
    if stored_ws.is_some_and(|s| normalize_tenant_scope(Some(s)) != actor_org)
        && !org_policy.allow_cross_org_share
    {
        return detail(
            StatusCode::FORBIDDEN,
            "Organization governance policy disallows cross-organization share acceptance.",
        );
    }
    let status = stored
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("pending");
    if status != "pending" && status != "snoozed" {
        return detail(
            StatusCode::CONFLICT,
            &format!("cannot 'accept' a share proposal in status '{status}'"),
        );
    }
    if stored.get("kind").and_then(Value::as_str) != Some(SHARE_REVIEW_KIND) {
        return detail(
            StatusCode::BAD_REQUEST,
            "Review item is not a staged Knowledge Graph share proposal.",
        );
    }
    let payload = stored.get("payload").cloned().unwrap_or(Value::Null);
    if !matches!(verify_subgraph_bundle_signature(&payload), Ok(true)) {
        return detail(StatusCode::CONFLICT, UNSIGNED_BUNDLE);
    }
    let origin = payload
        .get("signature")
        .and_then(|s| s.get("fingerprint"))
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_string();

    let Some(graph) = state.graph.clone() else {
        return message_detail(503, "portability.review_queue_unavailable", &headers);
    };
    let req = SharedSubgraphImport {
        data: payload.as_object().cloned().unwrap_or_default(),
        workspace_id: workspace_id
            .or(stored_ws)
            .unwrap_or(lattice_core::read::DEFAULT_WORKSPACE_ID)
            .to_string(),
        owner: Some(identity.email.trim().to_string()).filter(|email| !email.is_empty()),
        origin,
    };
    let outcome =
        match tokio::task::spawn_blocking(move || graph.import_shared_subgraph(&req)).await {
            Ok(Ok(outcome)) => outcome,
            Ok(Err(err)) => return detail(StatusCode::BAD_REQUEST, &err.to_string()),
            Err(err) => return detail(StatusCode::BAD_GATEWAY, &err.to_string()),
        };

    let updated = match transition_approve(&gov, &item_id, workspace_id) {
        Ok(item) => item,
        Err(ReviewError::Conflict(msg)) => return detail(StatusCode::CONFLICT, &msg),
        Err(ReviewError::NotFound) => {
            return message_detail(404, "review.item_not_found", &headers)
        }
        Err(ReviewError::Invalid(msg)) => return detail(StatusCode::UNPROCESSABLE_ENTITY, &msg),
        Err(ReviewError::Failed(msg)) => return detail(StatusCode::BAD_REQUEST, &msg),
    };

    emit_portability_audit(
        &state,
        "share_accepted",
        json!({
            "proposal_id": item_id,
            "workspace_id": workspace_id,
            "import": outcome.to_json(),
        }),
    );

    let mut map = OrderedMap::new();
    map.insert("accepted", json!(true));
    map.insert("status", json!("approved"));
    map.insert("proposal_id", json!(item_id));
    map.insert("workspace_id", json!(workspace_id));
    map.insert("import", outcome.to_json());
    map.insert("review_item", updated);
    json_ok(map)
}
