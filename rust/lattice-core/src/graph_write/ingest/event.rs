//! `ingest_event` — an analytics/system event as a first-class node.

use serde_json::{json, Map, Value};

use crate::db::CoreError;

use super::super::pyaux::{json_of, scoped_hash_id, scoped_slug_id};
use super::super::types::{EdgeSpec, IngestEventRequest, NodeSpec};
use super::super::GraphWriter;
use super::IngestOutcome;

impl GraphWriter {
    /// `ingest_event` — an analytics/system event as a first-class node.
    ///
    /// One of the `GRAPH_MUTATION_OPS` additions (WP-R7: review-queue
    /// `agent_followup` promotion, workspace run and memory writes).
    pub fn ingest_event(&self, request: &IngestEventRequest) -> Result<IngestOutcome, CoreError> {
        let event_type = if request.event_type.is_empty() {
            "Event".to_string()
        } else {
            request.event_type.clone()
        };
        let title = if request.title.is_empty() {
            event_type.clone()
        } else {
            request.title.clone()
        };
        let workspace = request.workspace_id.as_deref().filter(|w| !w.is_empty());
        let mut payload = Map::new();
        payload.insert("event_type".into(), json!(event_type));
        payload.insert("title".into(), json!(title));
        payload.insert("user_email".into(), json!(request.user_email));
        payload.insert("user_nickname".into(), json!(request.user_nickname));
        payload.insert("source".into(), json!(request.source));
        payload.insert("conversation_id".into(), json!(request.conversation_id));
        payload.insert("workspace_id".into(), json!(request.workspace_id));
        payload.insert("metadata".into(), Value::Object(request.metadata.clone()));
        payload.insert("timestamp".into(), json!(self.clock.now_iso()));
        let event_id = scoped_hash_id("event", &json_of(&payload), workspace);
        let conv_key = request
            .conversation_id
            .clone()
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| "default".into());
        let conv_id = scoped_slug_id("conversation", &conv_key, workspace);
        self.store.with_write_txn(|txn| {
            self.upsert_node(
                txn,
                &NodeSpec {
                    id: event_id.clone(),
                    node_type: event_type.clone(),
                    title: title.clone(),
                    summary: title.clone(),
                    metadata: payload.clone(),
                    raw: payload.clone(),
                    owner: request.user_email.clone(),
                    workspace_id: request.workspace_id.clone(),
                    visibility: None,
                },
            )?;
            let mut conv_meta = Map::new();
            conv_meta.insert("source".into(), json!(request.source));
            conv_meta.insert("workspace_id".into(), json!(request.workspace_id));
            self.upsert_node(
                txn,
                &NodeSpec {
                    id: conv_id.clone(),
                    node_type: "Conversation".into(),
                    title: request
                        .conversation_id
                        .clone()
                        .filter(|value| !value.is_empty())
                        .unwrap_or_else(|| "Default conversation".into()),
                    metadata: conv_meta,
                    owner: request.user_email.clone(),
                    workspace_id: request.workspace_id.clone(),
                    ..NodeSpec::default()
                },
            )?;
            let mut edge_meta = Map::new();
            edge_meta.insert("source".into(), json!(request.source));
            self.upsert_edge(
                txn,
                &EdgeSpec {
                    from_node: conv_id.clone(),
                    to_node: event_id.clone(),
                    edge_type: "has_event".into(),
                    weight: 1.0,
                    metadata: edge_meta,
                    legacy_label: None,
                },
            )?;
            let person_key = request
                .user_email
                .clone()
                .filter(|value| !value.is_empty())
                .or_else(|| request.user_nickname.clone().filter(|v| !v.is_empty()));
            if let Some(person_key) = person_key {
                let person_id = scoped_slug_id("person", &person_key, workspace);
                let mut person_meta = Map::new();
                person_meta.insert("email".into(), json!(request.user_email));
                person_meta.insert("workspace_id".into(), json!(request.workspace_id));
                let label = request
                    .user_nickname
                    .clone()
                    .filter(|value| !value.is_empty())
                    .or_else(|| request.user_email.clone().filter(|v| !v.is_empty()))
                    .unwrap_or_else(|| "Unknown user".into());
                self.upsert_node(
                    txn,
                    &NodeSpec {
                        id: person_id.clone(),
                        node_type: "Person".into(),
                        title: label,
                        metadata: person_meta,
                        owner: request.user_email.clone(),
                        workspace_id: request.workspace_id.clone(),
                        ..NodeSpec::default()
                    },
                )?;
                let mut triggered_meta = Map::new();
                triggered_meta.insert("event_type".into(), json!(event_type));
                self.upsert_edge(
                    txn,
                    &EdgeSpec {
                        from_node: person_id,
                        to_node: event_id.clone(),
                        edge_type: "triggered".into(),
                        weight: 1.0,
                        metadata: triggered_meta,
                        legacy_label: None,
                    },
                )?;
            }
            Ok(())
        })?;
        Ok(IngestOutcome {
            node_id: event_id,
            node_type: event_type,
            source_node_id: None,
            content_hash: None,
            sha256: None,
            chunk_ids: Vec::new(),
            chunk_count: 0,
            duplicate: false,
            captured_at: None,
            metadata: None,
        })
    }
}
