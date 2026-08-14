//! `ingest_content` — the unified text / URL / browser-tab / note door.

use serde_json::{json, Map, Value};

use crate::db::CoreError;
use crate::pytext::{clean_text, truncate_chars};

use super::super::pyaux::{scoped_slug_id, sha256_text};
use super::super::types::{EdgeSpec, IngestContentRequest, NodeSpec};
use super::super::GraphWriter;
use super::IngestOutcome;

impl GraphWriter {
    /// `ingest_source` — the unified text / URL / browser-tab / note door.
    ///
    /// Idempotent by content hash: re-ingesting the same `(source_type,
    /// source_uri, text)` reuses the same node id and reports `duplicate`.
    pub fn ingest_content(
        &self,
        request: &IngestContentRequest,
    ) -> Result<IngestOutcome, CoreError> {
        let source_type = if request.source_type.is_empty() {
            "text".to_string()
        } else {
            request.source_type.clone()
        };
        let text = request.text.clone();
        let title_source = if !request.title.is_empty() {
            request.title.clone()
        } else if let Some(uri) = request.source_uri.as_deref().filter(|v| !v.is_empty()) {
            uri.to_string()
        } else {
            source_type.clone()
        };
        let title = {
            let cleaned = truncate_chars(&clean_text(&title_source), 240);
            if cleaned.is_empty() {
                source_type.clone()
            } else {
                cleaned
            }
        };
        let captured_at = request
            .captured_at
            .clone()
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| self.clock.now_iso());
        let content_hash = sha256_text(&format!(
            "{source_type}|{}|{text}",
            request.source_uri.clone().unwrap_or_default()
        ));
        let identity_hash = sha256_text(&format!(
            "{}|{content_hash}",
            request
                .workspace_id
                .clone()
                .filter(|w| !w.is_empty())
                .unwrap_or_else(|| "legacy-global".into())
        ));
        let content_id = format!("webdoc:{}", &identity_hash[..24]);
        let node_type = request
            .node_type
            .clone()
            .unwrap_or_else(|| "Document".into());
        let workspace = request.workspace_id.as_deref().filter(|w| !w.is_empty());

        let mut node_meta = Map::new();
        node_meta.insert("source_type".into(), json!(source_type));
        node_meta.insert("source_uri".into(), json!(request.source_uri));
        node_meta.insert("content_hash".into(), json!(content_hash));
        node_meta.insert("title".into(), json!(title));
        node_meta.insert("captured_at".into(), json!(captured_at));
        node_meta.insert("modified_at".into(), json!(request.modified_at));
        node_meta.insert("owner".into(), json!(request.owner));
        node_meta.insert("workspace_id".into(), json!(request.workspace_id));
        node_meta.insert(
            "permissions".into(),
            Value::Object(request.permissions.clone()),
        );
        node_meta.insert("chars".into(), json!(text.chars().count()));
        for (key, value) in &request.metadata {
            node_meta.insert(key.clone(), value.clone());
        }

        let mut chunk_ids: Vec<String> = Vec::new();
        let (source_node_id, duplicate) = self.store.with_write_txn(|txn| {
            let duplicate = self.node_exists(txn, &content_id)?;
            self.upsert_node_with_vector(
                txn,
                &NodeSpec {
                    id: content_id.clone(),
                    node_type: node_type.clone(),
                    title: title.clone(),
                    summary: truncate_chars(if text.is_empty() { &title } else { &text }, 500),
                    metadata: node_meta.clone(),
                    raw: node_meta.clone(),
                    owner: request.owner.clone(),
                    workspace_id: request.workspace_id.clone(),
                    visibility: None,
                },
                request.embedding.as_ref(),
            )?;
            let mut source_extra = Map::new();
            source_extra.insert("owner".into(), json!(request.owner));
            source_extra.insert("workspace_id".into(), json!(request.workspace_id));
            let source_node_id = self.attach_source_node(
                txn,
                &content_id,
                &source_type,
                request.source_uri.as_deref(),
                Some(&title),
                Some(&content_hash),
                Some(&captured_at),
                source_extra,
            )?;
            if let Some(owner) = request.owner.as_deref().filter(|v| !v.is_empty()) {
                let person_id = scoped_slug_id("person", owner, workspace);
                let mut person_meta = Map::new();
                person_meta.insert("email".into(), json!(owner));
                person_meta.insert("workspace_id".into(), json!(request.workspace_id));
                self.upsert_node(
                    txn,
                    &NodeSpec {
                        id: person_id.clone(),
                        node_type: "Person".into(),
                        title: owner.to_string(),
                        metadata: person_meta,
                        owner: request.owner.clone(),
                        workspace_id: request.workspace_id.clone(),
                        ..NodeSpec::default()
                    },
                )?;
                self.upsert_edge(
                    txn,
                    &EdgeSpec {
                        from_node: person_id,
                        to_node: content_id.clone(),
                        edge_type: "업로드함".into(),
                        weight: 1.0,
                        metadata: Map::new(),
                        legacy_label: None,
                    },
                )?;
            }
            if let Some(conversation) = request.conversation_id.as_deref().filter(|v| !v.is_empty())
            {
                let conv_id = scoped_slug_id("conversation", conversation, workspace);
                let mut conv_meta = Map::new();
                conv_meta.insert("conversation_id".into(), json!(conversation));
                conv_meta.insert("workspace_id".into(), json!(request.workspace_id));
                self.upsert_node(
                    txn,
                    &NodeSpec {
                        id: conv_id.clone(),
                        node_type: "Chat".into(),
                        title: conversation.to_string(),
                        metadata: conv_meta,
                        owner: request.owner.clone(),
                        workspace_id: request.workspace_id.clone(),
                        ..NodeSpec::default()
                    },
                )?;
                self.upsert_edge(
                    txn,
                    &EdgeSpec {
                        from_node: conv_id,
                        to_node: content_id.clone(),
                        edge_type: "언급함".into(),
                        weight: 0.8,
                        metadata: Map::new(),
                        legacy_label: None,
                    },
                )?;
            }
            chunk_ids = self.write_chunks(
                txn,
                &request.chunks,
                &content_id,
                |index| format!("{title} chunk {}", index + 1),
                request.owner.as_deref(),
                request.workspace_id.as_deref(),
                true,
            )?;
            let concept_ids = self.write_concepts(
                txn,
                &request.concepts,
                |_| {
                    let mut metadata = Map::new();
                    metadata.insert("auto_extracted".into(), json!(true));
                    metadata.insert("source_type".into(), json!(source_type));
                    metadata.insert("workspace_id".into(), json!(request.workspace_id));
                    metadata
                },
                &content_id,
                "포함함",
                0.8,
                Map::new(),
                request.owner.as_deref(),
                request.workspace_id.as_deref(),
            )?;
            self.write_triples(txn, &request.triples, &concept_ids)?;
            for item in &request.semantic {
                let sem_id = format!(
                    "{}:{}",
                    item.item_type.to_lowercase(),
                    &sha256_text(&format!("{content_id}:{}:{}", item.item_type, item.title))[..24]
                );
                let mut metadata = Map::new();
                metadata.insert("auto_extracted".into(), json!(true));
                metadata.insert("source_node".into(), json!(content_id));
                metadata.insert("workspace_id".into(), json!(request.workspace_id));
                self.upsert_node(
                    txn,
                    &NodeSpec {
                        id: sem_id.clone(),
                        node_type: item.item_type.clone(),
                        title: item.title.clone(),
                        summary: item.summary.clone(),
                        metadata,
                        raw: item.raw.clone(),
                        owner: request.owner.clone(),
                        workspace_id: request.workspace_id.clone(),
                        visibility: None,
                    },
                )?;
                self.upsert_edge(
                    txn,
                    &EdgeSpec {
                        from_node: content_id.clone(),
                        to_node: sem_id,
                        edge_type: "포함함".into(),
                        weight: 0.9,
                        metadata: Map::new(),
                        legacy_label: None,
                    },
                )?;
            }
            Ok((source_node_id, duplicate))
        })?;
        Ok(IngestOutcome {
            node_id: content_id,
            node_type,
            source_node_id: Some(source_node_id),
            content_hash: Some(content_hash),
            sha256: None,
            chunk_count: chunk_ids.len(),
            chunk_ids,
            duplicate,
            captured_at: Some(captured_at),
            metadata: None,
        })
    }
}
