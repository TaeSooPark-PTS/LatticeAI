//! The ingest doors — port of `lattice_brain/graph/ingest.py`.
//!
//! Four doors, one shape: derive the content identity, upsert the content node,
//! attach a `Source` node and the people and conversations around it, write the
//! retrieval chunks, then the extracted concepts, the concept–concept edges and
//! the Task/Decision items. The order is Python's, statement for statement,
//! because the ids of everything downstream are hashed from what came before.
//!
//! What crosses the seam as *data* (see [`super::types`]): chunk boundaries,
//! concepts with their classification, triples with their evidence class, and
//! semantic items. What is derived **here**: every id, every hash, the blob
//! sidecar, the sensitivity stamp, and the rows.

use std::path::Path;

use rusqlite::Transaction;
use serde_json::{json, Map, Value};

use crate::db::CoreError;
use crate::pytext::{round_to, truncate_chars};

use super::pyaux::{json_of, scoped_slug_id, sha256_text};
use super::types::{ChunkPiece, ConceptSpec, EdgeSpec, NodeSpec, StructureChild, TripleSpec};
use super::GraphWriter;

/// What every ingest door hands back.
#[derive(Debug, Clone, PartialEq)]
pub struct IngestOutcome {
    /// The content node.
    pub node_id: String,
    /// Its legacy type label.
    pub node_type: String,
    /// The `Source` node it was indexed from, when there is one.
    pub source_node_id: Option<String>,
    /// The identity hash the dedup check is made against.
    pub content_hash: Option<String>,
    /// Set by the file door only (`ingest_document` reports both keys).
    pub sha256: Option<String>,
    pub chunk_ids: Vec<String>,
    pub chunk_count: usize,
    /// Whether the content node already existed before this call.
    pub duplicate: bool,
    pub captured_at: Option<String>,
    /// The file door's full node metadata, as it reports it.
    pub metadata: Option<Map<String, Value>>,
}

impl IngestOutcome {
    /// The JSON body the Python doors return, key for key.
    pub fn to_json(&self) -> Value {
        let mut map = Map::new();
        map.insert("node_id".into(), json!(self.node_id));
        map.insert("type".into(), json!(self.node_type));
        if let Some(sha) = &self.sha256 {
            map.insert("sha256".into(), json!(sha));
        }
        if let Some(hash) = &self.content_hash {
            map.insert("content_hash".into(), json!(hash));
        }
        map.insert("source_node_id".into(), json!(self.source_node_id));
        map.insert("chunk_ids".into(), json!(self.chunk_ids));
        map.insert("chunk_count".into(), json!(self.chunk_count));
        map.insert("duplicate".into(), json!(self.duplicate));
        map.insert("captured_at".into(), json!(self.captured_at));
        if let Some(metadata) = &self.metadata {
            map.insert("metadata".into(), Value::Object(metadata.clone()));
        }
        Value::Object(map)
    }

    /// `{"node_id": …, "type": …}` — all `ingest_message` and `ingest_event`
    /// report. The other doors report the dedup and chunk facts a caller needs
    /// to record provenance; these two have nothing to add.
    pub fn to_json_brief(&self) -> Value {
        json!({"node_id": self.node_id, "type": self.node_type})
    }
}

/// `_triple_edge_metadata` — the evidence class travels with the edge.
///
/// Review 2026-07-27 P1 #6: a verb-backed relation and a bare co-occurrence
/// used to land in the graph identically, so a reader could not tell a meaning
/// edge from an adjacency edge.
pub(crate) fn triple_edge_metadata(triple: &TripleSpec) -> Map<String, Value> {
    let mut metadata = Map::new();
    metadata.insert(
        "context".into(),
        Value::String(truncate_chars(&triple.context, 240)),
    );
    if !triple.evidence.is_empty() {
        metadata.insert("evidence".into(), Value::String(triple.evidence.clone()));
    }
    if let Some(confidence) = triple.confidence {
        metadata.insert("confidence".into(), Value::from(round_to(confidence, 4)));
    }
    metadata
}

impl GraphWriter {
    /// `_attach_source_node` — every ingested node points at exactly one
    /// `Source`, so the graph can always explain where a node came from.
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn attach_source_node(
        &self,
        txn: &Transaction<'_>,
        content_node_id: &str,
        source_type: &str,
        source_uri: Option<&str>,
        title: Option<&str>,
        content_hash: Option<&str>,
        captured_at: Option<&str>,
        extra: Map<String, Value>,
    ) -> Result<String, CoreError> {
        let mut meta = Map::new();
        meta.insert("source_type".into(), json!(source_type));
        meta.insert("source_uri".into(), json!(source_uri));
        meta.insert("content_hash".into(), json!(content_hash));
        meta.insert(
            "captured_at".into(),
            json!(captured_at
                .filter(|value| !value.is_empty())
                .map(str::to_string)
                .unwrap_or_else(|| self.clock.now_iso())),
        );
        for (key, value) in extra {
            meta.insert(key, value);
        }
        let key = source_uri
            .filter(|value| !value.is_empty())
            .or(content_hash.filter(|value| !value.is_empty()))
            .unwrap_or(content_node_id);
        let source_scope = meta
            .get("workspace_id")
            .filter(|value| super::pyaux::truthy(value))
            .map(super::pyaux::py_str)
            .unwrap_or_else(|| "legacy-global".into());
        let source_id = format!(
            "source:{}",
            &sha256_text(&format!("{source_scope}|{source_type}|{key}"))[..24]
        );
        let label = title
            .filter(|value| !value.is_empty())
            .or(source_uri.filter(|value| !value.is_empty()))
            .unwrap_or(source_type);
        let summary_source = source_uri
            .filter(|value| !value.is_empty())
            .or(title.filter(|value| !value.is_empty()))
            .unwrap_or(source_type);
        let owner = meta
            .get("owner")
            .filter(|value| super::pyaux::truthy(value))
            .map(super::pyaux::py_str);
        let workspace_id = meta
            .get("workspace_id")
            .filter(|value| super::pyaux::truthy(value))
            .map(super::pyaux::py_str);
        self.upsert_node(
            txn,
            &NodeSpec {
                id: source_id.clone(),
                node_type: "Source".into(),
                title: label.to_string(),
                summary: truncate_chars(summary_source, 400),
                metadata: meta,
                raw: Map::new(),
                owner,
                workspace_id,
                visibility: None,
            },
        )?;
        let mut edge_meta = Map::new();
        edge_meta.insert("source_type".into(), json!(source_type));
        self.upsert_edge(
            txn,
            &EdgeSpec {
                from_node: content_node_id.to_string(),
                to_node: source_id.clone(),
                edge_type: "indexed_from".into(),
                weight: 1.0,
                metadata: edge_meta,
                legacy_label: None,
            },
        )?;
        Ok(source_id)
    }

    /// The chunk loop the three text doors share.
    ///
    /// `collect_ids` is the one difference between them: `ingest_message` does
    /// not report chunk ids, and reproducing that means not collecting them
    /// rather than dropping them afterwards.
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn write_chunks(
        &self,
        txn: &Transaction<'_>,
        chunks: &[ChunkPiece],
        source_node: &str,
        title_for: impl Fn(usize) -> String,
        owner: Option<&str>,
        workspace_id: Option<&str>,
        scope_node_metadata: bool,
    ) -> Result<Vec<String>, CoreError> {
        let mut ids = Vec::with_capacity(chunks.len());
        for (index, piece) in chunks.iter().enumerate() {
            let chunk_id = format!(
                "chunk:{}",
                &sha256_text(&format!("{source_node}:{index}:{}", piece.text))[..24]
            );
            let mut node_metadata = Map::new();
            node_metadata.insert("index".into(), json!(index));
            node_metadata.insert("source_node".into(), json!(source_node));
            if scope_node_metadata {
                node_metadata.insert("workspace_id".into(), json!(workspace_id));
            }
            for (key, value) in &piece.fields {
                node_metadata.insert(key.clone(), value.clone());
            }
            self.upsert_node(
                txn,
                &NodeSpec {
                    id: chunk_id.clone(),
                    node_type: "Chunk".into(),
                    title: title_for(index),
                    summary: truncate_chars(&piece.text, 500),
                    metadata: node_metadata,
                    raw: Map::new(),
                    owner: owner.map(str::to_string),
                    workspace_id: workspace_id.map(str::to_string),
                    visibility: None,
                },
            )?;
            // The chunk *row*'s metadata is never workspace-stamped, in either
            // door — only the Chunk node's is.
            let mut chunk_metadata = Map::new();
            chunk_metadata.insert("index".into(), json!(index));
            chunk_metadata.insert("source_node".into(), json!(source_node));
            for (key, value) in &piece.fields {
                chunk_metadata.insert(key.clone(), value.clone());
            }
            self.upsert_chunk_with_vector(
                txn,
                &chunk_id,
                source_node,
                &piece.text,
                &chunk_metadata,
                piece.embedding.as_ref(),
            )?;
            self.upsert_edge(
                txn,
                &EdgeSpec {
                    from_node: source_node.to_string(),
                    to_node: chunk_id.clone(),
                    edge_type: "포함함".into(),
                    weight: 1.0,
                    metadata: Map::new(),
                    legacy_label: None,
                },
            )?;
            ids.push(chunk_id);
        }
        Ok(ids)
    }

    /// The concept loop: one node per extracted concept, one edge back to the
    /// content node. Returns `(lowercased concept, node id)` in order — which
    /// is what the triple loop resolves its subjects and objects against.
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn write_concepts(
        &self,
        txn: &Transaction<'_>,
        concepts: &[ConceptSpec],
        metadata_for: impl Fn(&ConceptSpec) -> Map<String, Value>,
        anchor_id: &str,
        edge_type: &str,
        edge_weight: f64,
        edge_metadata: Map<String, Value>,
        owner: Option<&str>,
        workspace_id: Option<&str>,
    ) -> Result<Vec<(String, String)>, CoreError> {
        let workspace = workspace_id.filter(|value| !value.is_empty());
        let mut resolved: Vec<(String, String)> = Vec::new();
        for concept in concepts {
            let concept_id =
                scoped_slug_id(&concept.node_type.to_lowercase(), &concept.text, workspace);
            let key = concept.text.to_lowercase();
            match resolved.iter_mut().find(|(existing, _)| *existing == key) {
                Some(slot) => slot.1 = concept_id.clone(),
                None => resolved.push((key, concept_id.clone())),
            }
            self.upsert_node(
                txn,
                &NodeSpec {
                    id: concept_id.clone(),
                    node_type: concept.node_type.clone(),
                    title: concept.text.clone(),
                    metadata: metadata_for(concept),
                    owner: owner.map(str::to_string),
                    workspace_id: workspace_id.map(str::to_string),
                    ..NodeSpec::default()
                },
            )?;
            self.upsert_edge(
                txn,
                &EdgeSpec {
                    from_node: anchor_id.to_string(),
                    to_node: concept_id,
                    edge_type: edge_type.to_string(),
                    weight: edge_weight,
                    metadata: edge_metadata.clone(),
                    legacy_label: None,
                },
            )?;
        }
        Ok(resolved)
    }

    /// The concept–concept edge loop.
    pub(crate) fn write_triples(
        &self,
        txn: &Transaction<'_>,
        triples: &[TripleSpec],
        concept_ids: &[(String, String)],
    ) -> Result<(), CoreError> {
        for triple in triples {
            let subject = lookup_concept(concept_ids, &triple.subject.to_lowercase());
            let object = lookup_concept(concept_ids, &triple.object.to_lowercase());
            if let (Some(subject), Some(object)) = (subject, object) {
                if subject != object {
                    self.upsert_edge(
                        txn,
                        &EdgeSpec {
                            from_node: subject.to_string(),
                            to_node: object.to_string(),
                            edge_type: triple.relation.clone(),
                            weight: triple.weight,
                            metadata: triple_edge_metadata(triple),
                            legacy_label: None,
                        },
                    )?;
                }
            }
        }
        Ok(())
    }

    /// `_ingest_structure_nodes` — slides, pages, sheets and images.
    ///
    /// The topic labels per slide and per page come from `_topic_candidates`,
    /// which is NLP; they arrive on [`StructureChild::topics`].
    pub(crate) fn write_structure_nodes(
        &self,
        txn: &Transaction<'_>,
        children: &[StructureChild],
        file_id: &str,
        filename: &str,
        owner: Option<&str>,
        workspace_id: Option<&str>,
    ) -> Result<(), CoreError> {
        let workspace = workspace_id.filter(|value| !value.is_empty());
        for child in children {
            let mut metadata = child.payload.clone();
            metadata.insert("workspace_id".into(), json!(workspace_id));
            let (child_id, node_type, title, summary, edge_type) = match child.kind.as_str() {
                "slide" => {
                    let index = child.payload.get("index").cloned().unwrap_or(Value::Null);
                    let id = format!(
                        "slide:{}",
                        &sha256_text(&format!("{file_id}:slide:{}", super::pyaux::py_str(&index)))
                            [..24]
                    );
                    let texts: Vec<String> = child
                        .payload
                        .get("texts")
                        .and_then(Value::as_array)
                        .map(|items| items.iter().map(super::pyaux::py_str).collect())
                        .unwrap_or_default();
                    (
                        id,
                        "Slide",
                        format!("{filename} slide {}", super::pyaux::py_str(&index)),
                        truncate_chars(&texts.join("\n"), 800),
                        "has_slide",
                    )
                }
                "page" => {
                    let index = child.payload.get("index").cloned().unwrap_or(Value::Null);
                    let id = format!(
                        "page:{}",
                        &sha256_text(&format!("{file_id}:page:{}", super::pyaux::py_str(&index)))
                            [..24]
                    );
                    let preview = child
                        .payload
                        .get("preview")
                        .filter(|value| super::pyaux::truthy(value))
                        .map(super::pyaux::py_str)
                        .unwrap_or_default();
                    (
                        id,
                        "Page",
                        format!("{filename} page {}", super::pyaux::py_str(&index)),
                        preview,
                        "has_page",
                    )
                }
                "sheet" => {
                    let sheet_title = child.payload.get("title").cloned().unwrap_or(Value::Null);
                    let rendered = super::pyaux::py_str(&sheet_title);
                    let id = format!(
                        "sheet:{}",
                        &sha256_text(&format!("{file_id}:sheet:{rendered}"))[..24]
                    );
                    (
                        id,
                        "Sheet",
                        format!("{filename} / {rendered}"),
                        String::new(),
                        "has_sheet",
                    )
                }
                _ => {
                    let image_key = child
                        .payload
                        .get("sha256")
                        .filter(|value| super::pyaux::truthy(value))
                        .map(super::pyaux::py_str)
                        .unwrap_or_else(|| sha256_text(&sorted_compact_json(&child.payload)));
                    let id = match workspace {
                        Some(scope) => format!(
                            "image:{}",
                            &sha256_text(&format!("{scope}|{image_key}"))[..24]
                        ),
                        None => format!("image:{}", truncate_chars(&image_key, 24)),
                    };
                    let mut title_parts = vec![filename.to_string(), "image".to_string()];
                    if let Some(page) = child
                        .payload
                        .get("page")
                        .filter(|value| super::pyaux::truthy(value))
                    {
                        title_parts.push(format!("page {}", super::pyaux::py_str(page)));
                    }
                    if let Some(name) = child
                        .payload
                        .get("name")
                        .filter(|value| super::pyaux::truthy(value))
                    {
                        let rendered = super::pyaux::py_str(name);
                        title_parts
                            .push(rendered.rsplit('/').next().unwrap_or(&rendered).to_string());
                    }
                    (
                        id,
                        "Image",
                        title_parts.join(" / "),
                        String::new(),
                        "contains_image",
                    )
                }
            };
            self.upsert_node(
                txn,
                &NodeSpec {
                    id: child_id.clone(),
                    node_type: node_type.into(),
                    title,
                    summary,
                    metadata,
                    raw: Map::new(),
                    owner: owner.map(str::to_string),
                    workspace_id: workspace_id.map(str::to_string),
                    visibility: None,
                },
            )?;
            self.upsert_edge(
                txn,
                &EdgeSpec {
                    from_node: file_id.to_string(),
                    to_node: child_id.clone(),
                    edge_type: edge_type.into(),
                    weight: 1.0,
                    metadata: Map::new(),
                    legacy_label: None,
                },
            )?;
            for topic in &child.topics {
                let topic_id = match workspace {
                    Some(scope) => {
                        format!("topic:{}", &sha256_text(&format!("{scope}|{topic}"))[..24])
                    }
                    None => format!("topic:{}", super::pyaux::slug(topic, 96)),
                };
                let mut topic_meta = Map::new();
                topic_meta.insert("auto_extracted".into(), json!(true));
                topic_meta.insert("workspace_id".into(), json!(workspace_id));
                self.upsert_node(
                    txn,
                    &NodeSpec {
                        id: topic_id.clone(),
                        node_type: "Topic".into(),
                        title: topic.clone(),
                        metadata: topic_meta,
                        owner: owner.map(str::to_string),
                        workspace_id: workspace_id.map(str::to_string),
                        ..NodeSpec::default()
                    },
                )?;
                self.upsert_edge(
                    txn,
                    &EdgeSpec {
                        from_node: child_id.clone(),
                        to_node: topic_id,
                        edge_type: "discusses".into(),
                        weight: 0.6,
                        metadata: Map::new(),
                        legacy_label: None,
                    },
                )?;
            }
        }
        Ok(())
    }
}

pub(crate) fn lookup_concept<'a>(
    concept_ids: &'a [(String, String)],
    key: &str,
) -> Option<&'a str> {
    concept_ids
        .iter()
        .find(|(existing, _)| existing == key)
        .map(|(_, id)| id.as_str())
}

/// `json.dumps(image, ensure_ascii=False, sort_keys=True)` for the image key.
pub(crate) fn sorted_compact_json(payload: &Map<String, Value>) -> String {
    json_of(payload)
}

/// Python's `Path.suffix.lower()` — the last dot-segment of the file name only.
pub(crate) fn extension_of(path: &Path) -> String {
    let Some(name) = path
        .file_name()
        .map(|name| name.to_string_lossy().to_string())
    else {
        return String::new();
    };
    match name.rfind('.') {
        Some(index) if index > 0 => name[index..].to_lowercase(),
        _ => String::new(),
    }
}

pub(crate) mod content;
pub(crate) mod event;
pub(crate) mod file;
pub(crate) mod message;
