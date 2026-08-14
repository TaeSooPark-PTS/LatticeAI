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

use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{Path as AxumPath, Query, State};
use axum::http::HeaderMap;
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use lattice_auth::{AuthState, OrderedMap};
use lattice_core::db::tables::state_files;
use lattice_core::db::RuntimeConfig;
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::network::DeviceIdentity;
use crate::project_sessions::{detail, json_ok, message_detail, missing_body, parse_json_object};

use super::status::require_graph;
use super::*;

pub(crate) async fn postgres_docker(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    let object = if body.is_empty() {
        serde_json::Map::new()
    } else {
        match parse_json_object(&body) {
            Ok(v) => v,
            Err(refusal) => return refusal,
        }
    };
    let consent = object
        .get("consent")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let port = object.get("port").and_then(Value::as_i64).unwrap_or(5432);
    let compose_dir = state.config.data_dir().join("postgres");
    let _ = std::fs::create_dir_all(&compose_dir);
    let compose_path = compose_dir.join("postgres.compose.yml");
    let _ = std::fs::write(
        &compose_path,
        format!(
            "services:\n  postgres:\n    image: pgvector/pgvector:pg16\n    restart: unless-stopped\n    environment:\n      POSTGRES_DB: lattice_brain\n      POSTGRES_USER: lattice\n      POSTGRES_PASSWORD: lattice-local-only\n    ports:\n      - \"127.0.0.1:{port}:5432\"\n    volumes:\n      - ./postgres-data:/var/lib/postgresql/data\n"
        ),
    );
    if !consent {
        let mut map = OrderedMap::new();
        map.insert("status", json!("consent_required"));
        map.insert("started", json!(false));
        map.insert(
            "compose_path",
            json!(compose_path.to_string_lossy().to_string()),
        );
        map.insert(
            "command",
            json!([
                "docker",
                "compose",
                "-p",
                "lattice-brain",
                "-f",
                compose_path.to_string_lossy().to_string(),
                "up",
                "-d",
                "postgres"
            ]),
        );
        return json_ok(map);
    }
    let mut map = OrderedMap::new();
    map.insert("status", json!("planned"));
    json_ok(map)
}

pub(crate) async fn migrate_postgres(
    State(state): State<PortabilityState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(refusal) = state.auth.require_admin(&headers) {
        return refusal;
    }
    if let Err(refusal) = require_graph(&state, &headers) {
        return refusal;
    }
    let object = match parse_json_object(&body) {
        Ok(v) => v,
        Err(refusal) => return refusal,
    };
    let dsn = object.get("dsn").and_then(Value::as_str).unwrap_or("");
    if dsn.is_empty() {
        return detail(
            axum::http::StatusCode::BAD_REQUEST,
            "Postgres DSN is required for SQLite to Postgres migration.",
        );
    }
    let schema = object
        .get("schema_name")
        .and_then(Value::as_str)
        .unwrap_or("lattice_brain");
    let tables = plan_sqlite_tables(&state.config.graph_db_path());
    let mut map = OrderedMap::new();
    map.insert("status", json!("planned"));
    map.insert(
        "source",
        json!(state.config.graph_db_path().to_string_lossy().to_string()),
    );
    map.insert("target_engine", json!("postgres"));
    map.insert("target_schema", json!(schema));
    map.insert("tables", json!(tables));
    json_ok(map)
}

fn plan_sqlite_tables(path: &Path) -> Vec<OrderedMap> {
    let mut tables = Vec::new();
    if !path.exists() {
        return tables;
    }
    let Ok(conn) = lattice_core::db::open_read_only(path) else {
        return tables;
    };
    let Ok(mut stmt) = conn.prepare(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
    ) else {
        return tables;
    };
    let names: Vec<String> = stmt
        .query_map([], |row| row.get::<_, String>(0))
        .into_iter()
        .flatten()
        .flatten()
        .collect();
    for name in names {
        let mut columns = Vec::new();
        if let Ok(mut info) = conn.prepare(&format!("PRAGMA table_info(\"{name}\")")) {
            if let Ok(rows) = info.query_map([], |row| {
                Ok(json!({
                    "name": row.get::<_, String>(1)?,
                    "type": row.get::<_, String>(2).unwrap_or_else(|_| "TEXT".into()),
                }))
            }) {
                columns.extend(rows.flatten());
            }
        }
        let rows = conn
            .query_row(&format!("SELECT COUNT(*) FROM \"{name}\""), [], |row| {
                row.get::<_, i64>(0)
            })
            .unwrap_or(0);
        let mut table = OrderedMap::new();
        table.insert("name", json!(name));
        table.insert("columns", json!(columns));
        table.insert("rows", json!(rows));
        table.insert("conflict_key", json!("id"));
        table.insert("conflict_columns", json!(["id"]));
        table.insert("rowid_available", json!(true));
        tables.push(table);
    }
    tables
}

pub(crate) fn stamp() -> String {
    crate::project_sessions::now_iso_utc()
        .replace(':', "")
        .replace('-', "")
        .replace('.', "")
        .chars()
        .take(15)
        .collect()
}
