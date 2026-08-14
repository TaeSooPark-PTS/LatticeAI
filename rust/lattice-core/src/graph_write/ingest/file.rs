//! `ingest_file` — the upload door, with its content-addressed sidecar.

use serde_json::{json, Map, Value};

use crate::db::CoreError;
use crate::pytext::truncate_chars;

use super::super::pyaux::{
    scoped_hash_id, scoped_slug_id, sha256_bytes, sha256_text, stamp_sensitivity,
};
use super::super::types::{EdgeSpec, IngestFileRequest, NodeSpec};
use super::super::GraphWriter;
use super::{extension_of, IngestOutcome};

impl GraphWriter {
    /// `ingest_document` — the upload door, with its content-addressed sidecar.
    pub fn ingest_file(&self, request: &IngestFileRequest) -> Result<IngestOutcome, CoreError> {
        let data = std::fs::read(&request.path).map_err(|err| {
            CoreError::Io(format!(
                "cannot read the file being ingested {}: {err}",
                request.path.display()
            ))
        })?;
        let digest = sha256_bytes(&data);
        let ext = extension_of(&request.path);
        let filename = request
            .original_filename
            .clone()
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| {
                request
                    .path
                    .file_name()
                    .map(|name| name.to_string_lossy().to_string())
                    .unwrap_or_default()
            });
        let captured_at = request
            .captured_at
            .clone()
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| self.clock.now_iso());
        let blob_path = self
            .blob_dir
            .join(&digest[..2])
            .join(format!("{digest}{ext}"));
        if let Some(parent) = blob_path.parent() {
            std::fs::create_dir_all(parent).map_err(|err| {
                CoreError::Io(format!(
                    "cannot create the blob directory {}: {err}",
                    parent.display()
                ))
            })?;
        }
        if !blob_path.exists() {
            std::fs::copy(&request.path, &blob_path).map_err(|err| {
                CoreError::Io(format!(
                    "cannot store the blob sidecar {}: {err}",
                    blob_path.display()
                ))
            })?;
        }
        let text = request
            .extracted
            .get("content")
            .filter(|value| super::super::pyaux::truthy(value))
            .or_else(|| {
                request
                    .extracted
                    .get("preview")
                    .filter(|value| super::super::pyaux::truthy(value))
            })
            .map(super::super::pyaux::py_str)
            .unwrap_or_default();
        let workspace = request.workspace_id.as_deref().filter(|w| !w.is_empty());
        let file_id = scoped_hash_id("file", &digest, workspace);
        let owner = request
            .owner
            .clone()
            .filter(|value| !value.is_empty())
            .or_else(|| request.uploader.clone());
        let source_uri = request
            .source_uri
            .clone()
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| request.path.to_string_lossy().to_string());

        let mut metadata = Map::new();
        metadata.insert("filename".into(), json!(filename));
        metadata.insert("ext".into(), json!(ext));
        metadata.insert("mime_type".into(), json!(request.mime_type));
        metadata.insert("bytes".into(), json!(data.len()));
        metadata.insert("sha256".into(), json!(digest));
        metadata.insert("content_hash".into(), json!(digest));
        metadata.insert("blob_path".into(), json!(blob_path.to_string_lossy()));
        metadata.insert("uploader".into(), json!(request.uploader));
        metadata.insert("owner".into(), json!(owner));
        metadata.insert("workspace_id".into(), json!(request.workspace_id));
        metadata.insert(
            "permissions".into(),
            Value::Object(request.permissions.clone()),
        );
        metadata.insert(
            "source_type".into(),
            json!(request
                .source_type
                .clone()
                .filter(|value| !value.is_empty())
                .unwrap_or_else(|| "file".into())),
        );
        metadata.insert("source_uri".into(), json!(source_uri));
        metadata.insert("captured_at".into(), json!(captured_at));
        metadata.insert("modified_at".into(), json!(request.modified_at));
        metadata.insert("conversation_id".into(), json!(request.conversation_id));
        let mut extracted_without_content = request.extracted.clone();
        extracted_without_content.remove("content");
        metadata.insert("extracted".into(), Value::Object(extracted_without_content));
        metadata.insert("structure".into(), Value::Object(request.structure.clone()));
        // Stamp never-leaves at ingestion: a user who indexes a project folder
        // should not have to remember that it contains a `.env`.
        stamp_sensitivity(&mut metadata, &source_uri);

        let mut chunk_ids: Vec<String> = Vec::new();
        let (source_node_id, duplicate) = self.store.with_write_txn(|txn| {
            let duplicate = self.node_exists(txn, &file_id)?;
            self.upsert_node_with_vector(
                txn,
                &NodeSpec {
                    id: file_id.clone(),
                    node_type: "Document".into(),
                    title: filename.clone(),
                    summary: truncate_chars(if text.is_empty() { &filename } else { &text }, 500),
                    metadata: metadata.clone(),
                    raw: metadata.clone(),
                    owner: owner.clone(),
                    workspace_id: request.workspace_id.clone(),
                    visibility: None,
                },
                request.embedding.as_ref(),
            )?;
            self.write_structure_nodes(
                txn,
                &request.structure_nodes,
                &file_id,
                &filename,
                owner.as_deref(),
                request.workspace_id.as_deref(),
            )?;
            let mut source_node_id = None;
            if let Some(source_type) = request.source_type.as_deref().filter(|v| !v.is_empty()) {
                let mut extra = Map::new();
                extra.insert("owner".into(), json!(owner));
                extra.insert("workspace_id".into(), json!(request.workspace_id));
                extra.insert("ext".into(), json!(ext));
                source_node_id = Some(self.attach_source_node(
                    txn,
                    &file_id,
                    source_type,
                    Some(&source_uri),
                    Some(&filename),
                    Some(&digest),
                    Some(&captured_at),
                    extra,
                )?);
            }
            if let Some(uploader) = request.uploader.as_deref().filter(|v| !v.is_empty()) {
                let person_id = scoped_slug_id("person", uploader, workspace);
                let mut person_meta = Map::new();
                person_meta.insert("email".into(), json!(uploader));
                self.upsert_node(
                    txn,
                    &NodeSpec {
                        id: person_id.clone(),
                        node_type: "Person".into(),
                        title: uploader.to_string(),
                        metadata: person_meta,
                        owner: Some(uploader.to_string()),
                        workspace_id: request.workspace_id.clone(),
                        ..NodeSpec::default()
                    },
                )?;
                self.upsert_edge(
                    txn,
                    &EdgeSpec {
                        from_node: person_id,
                        to_node: file_id.clone(),
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
                        owner: owner.clone(),
                        workspace_id: request.workspace_id.clone(),
                        ..NodeSpec::default()
                    },
                )?;
                self.upsert_edge(
                    txn,
                    &EdgeSpec {
                        from_node: conv_id,
                        to_node: file_id.clone(),
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
                &file_id,
                |index| format!("{filename} chunk {}", index + 1),
                owner.as_deref(),
                request.workspace_id.as_deref(),
                true,
            )?;
            let concept_ids = self.write_concepts(
                txn,
                &request.concepts,
                |_| {
                    let mut metadata = Map::new();
                    metadata.insert("auto_extracted".into(), json!(true));
                    metadata.insert("source_file".into(), json!(filename));
                    metadata.insert("workspace_id".into(), json!(request.workspace_id));
                    metadata
                },
                &file_id,
                "포함함",
                0.8,
                Map::new(),
                owner.as_deref(),
                request.workspace_id.as_deref(),
            )?;
            self.write_triples(txn, &request.triples, &concept_ids)?;
            for item in &request.semantic {
                let sem_id = format!(
                    "{}:{}",
                    item.item_type.to_lowercase(),
                    &sha256_text(&format!("{file_id}:{}:{}", item.item_type, item.title))[..24]
                );
                let mut item_meta = Map::new();
                item_meta.insert("auto_extracted".into(), json!(true));
                item_meta.insert("source_node".into(), json!(file_id));
                item_meta.insert("filename".into(), json!(filename));
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
                        owner: owner.clone(),
                        workspace_id: request.workspace_id.clone(),
                        visibility: None,
                    },
                )?;
                self.upsert_edge(
                    txn,
                    &EdgeSpec {
                        from_node: file_id.clone(),
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
            node_id: file_id,
            node_type: "Document".into(),
            source_node_id,
            content_hash: Some(digest.clone()),
            sha256: Some(digest),
            chunk_count: chunk_ids.len(),
            chunk_ids,
            duplicate,
            captured_at: Some(captured_at),
            metadata: Some(metadata),
        })
    }
}
