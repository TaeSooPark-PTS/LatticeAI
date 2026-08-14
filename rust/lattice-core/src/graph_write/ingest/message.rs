//! `ingest_message` — one chat turn as a Chat / Person / message graph.

use serde_json::{json, Map};

use crate::db::CoreError;
use crate::pytext::{clean_text, truncate_chars};

use super::super::pyaux::{scoped_hash_id, scoped_slug_id, sha256_text};
use super::super::types::{EdgeSpec, IngestMessageRequest, NodeSpec};
use super::super::GraphWriter;
use super::IngestOutcome;

impl GraphWriter {
    /// `ingest_message` — one chat turn: Chat node, Person, the raw message,
    /// its chunks, and the concepts and items extracted from it.
    pub fn ingest_message(
        &self,
        request: &IngestMessageRequest,
    ) -> Result<IngestOutcome, CoreError> {
        let content = request.content.clone();
        let node_type = if request.role == "assistant" {
            "AIResponse"
        } else {
            "Message"
        };
        let workspace = request.workspace_id.as_deref().filter(|w| !w.is_empty());
        let message_identity = format!(
            "{}|{content}|{}|{}",
            request.role,
            request.conversation_id.clone().unwrap_or_default(),
            request.user_email.clone().unwrap_or_default()
        );
        let node_id = scoped_hash_id(&node_type.to_lowercase(), &message_identity, workspace);
        let conv_key = request
            .conversation_id
            .clone()
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| "default".into());
        let conv_id = scoped_slug_id("conversation", &conv_key, workspace);
        let mut metadata = Map::new();
        metadata.insert("role".into(), json!(request.role));
        metadata.insert("source".into(), json!(request.source));
        metadata.insert("conversation_id".into(), json!(request.conversation_id));
        metadata.insert("workspace_id".into(), json!(request.workspace_id));
        metadata.insert("user_email".into(), json!(request.user_email));
        metadata.insert("user_nickname".into(), json!(request.user_nickname));
        metadata.insert("chars".into(), json!(content.chars().count()));

        self.store.with_write_txn(|txn| {
            let cleaned = clean_text(&content);
            let chat_title = {
                let head = truncate_chars(&cleaned, 80);
                if head.is_empty() {
                    request
                        .conversation_id
                        .clone()
                        .filter(|value| !value.is_empty())
                        .unwrap_or_else(|| "대화".into())
                } else {
                    head
                }
            };
            let mut chat_meta = Map::new();
            chat_meta.insert("source".into(), json!(request.source));
            chat_meta.insert("conversation_id".into(), json!(request.conversation_id));
            chat_meta.insert("workspace_id".into(), json!(request.workspace_id));
            self.upsert_node(
                txn,
                &NodeSpec {
                    id: conv_id.clone(),
                    node_type: "Chat".into(),
                    title: chat_title,
                    summary: truncate_chars(&cleaned, 400),
                    metadata: chat_meta,
                    raw: Map::new(),
                    owner: request.user_email.clone(),
                    workspace_id: request.workspace_id.clone(),
                    visibility: None,
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
                person_meta.insert("nickname".into(), json!(request.user_nickname));
                let label = request
                    .user_nickname
                    .clone()
                    .filter(|value| !value.is_empty())
                    .or_else(|| request.user_email.clone().filter(|v| !v.is_empty()))
                    .unwrap_or_else(|| "Unknown".into());
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
                let mut edge_meta = Map::new();
                edge_meta.insert("role".into(), json!(request.role));
                self.upsert_edge(
                    txn,
                    &EdgeSpec {
                        from_node: person_id,
                        to_node: conv_id.clone(),
                        edge_type: "작성함".into(),
                        weight: 1.0,
                        metadata: edge_meta,
                        legacy_label: None,
                    },
                )?;
            }
            let raw = request.raw.clone().unwrap_or_else(|| metadata.clone());
            self.upsert_node_with_vector(
                txn,
                &NodeSpec {
                    id: node_id.clone(),
                    node_type: node_type.into(),
                    title: {
                        let head = truncate_chars(&cleaned, 80);
                        if head.is_empty() {
                            request.role.clone()
                        } else {
                            head
                        }
                    },
                    summary: truncate_chars(&cleaned, 500),
                    metadata: metadata.clone(),
                    raw,
                    owner: request.user_email.clone(),
                    workspace_id: request.workspace_id.clone(),
                    visibility: None,
                },
                request.embedding.as_ref(),
            )?;
            let mut edge_meta = Map::new();
            edge_meta.insert("role".into(), json!(request.role));
            self.upsert_edge(
                txn,
                &EdgeSpec {
                    from_node: conv_id.clone(),
                    to_node: node_id.clone(),
                    edge_type: "포함함".into(),
                    weight: 0.3,
                    metadata: edge_meta,
                    legacy_label: None,
                },
            )?;
            self.write_chunks(
                txn,
                &request.chunks,
                &node_id,
                |index| format!("chunk {}", index + 1),
                request.user_email.as_deref(),
                request.workspace_id.as_deref(),
                false,
            )?;
            let mut source_meta = Map::new();
            source_meta.insert("source".into(), json!(request.source));
            let concept_ids = self.write_concepts(
                txn,
                &request.concepts,
                |_| {
                    let mut concept_meta = Map::new();
                    concept_meta.insert("auto_extracted".into(), json!(true));
                    concept_meta.insert("source".into(), json!(request.source));
                    concept_meta.insert("workspace_id".into(), json!(request.workspace_id));
                    concept_meta
                },
                &conv_id,
                "언급함",
                0.7,
                source_meta,
                request.user_email.as_deref(),
                request.workspace_id.as_deref(),
            )?;
            self.write_triples(txn, &request.triples, &concept_ids)?;
            for item in &request.semantic {
                let sem_id = format!(
                    "{}:{}",
                    item.item_type.to_lowercase(),
                    &sha256_text(&format!("{conv_id}:{}:{}", item.item_type, item.title))[..24]
                );
                let mut item_meta = Map::new();
                item_meta.insert("auto_extracted".into(), json!(true));
                item_meta.insert("source_node".into(), json!(node_id));
                item_meta.insert("workspace_id".into(), json!(request.workspace_id));
                self.upsert_node(
                    txn,
                    &NodeSpec {
                        id: sem_id.clone(),
                        node_type: item.item_type.clone(),
                        title: item.title.clone(),
                        summary: item.summary.clone(),
                        metadata: item_meta,
                        raw: item.raw.clone(),
                        owner: request.user_email.clone(),
                        workspace_id: request.workspace_id.clone(),
                        visibility: None,
                    },
                )?;
                self.upsert_edge(
                    txn,
                    &EdgeSpec {
                        from_node: conv_id.clone(),
                        to_node: sem_id.clone(),
                        edge_type: "생성함".into(),
                        weight: 0.9,
                        metadata: Map::new(),
                        legacy_label: None,
                    },
                )?;
                for concept_id in concept_ids.iter().map(|(_, id)| id).take(3) {
                    self.upsert_edge(
                        txn,
                        &EdgeSpec {
                            from_node: sem_id.clone(),
                            to_node: concept_id.clone(),
                            edge_type: "언급함".into(),
                            weight: 0.6,
                            metadata: Map::new(),
                            legacy_label: None,
                        },
                    )?;
                }
            }
            Ok(())
        })?;
        Ok(IngestOutcome {
            node_id,
            node_type: node_type.into(),
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
