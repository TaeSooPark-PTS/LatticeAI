//! Section nodes: the document's own outline, as graph structure.
//!
//! The typed chunker already knows where every chunk sits — `heading_path` on
//! each markdown chunk is a `" > "`-joined trail like `아키텍처 > 저장소`. Until
//! v12.0.0 that string was written onto the Chunk node's metadata and stopped
//! there: nothing in the graph said a document *had* sections, so "이 사실은
//! 어느 절에서 나왔나" could only be answered by reading a metadata field, and
//! "그 절에 또 뭐가 있나" could not be answered at all.
//!
//! This turns the outline into what it already was — a tree:
//!
//! ```text
//! Document ←─part_of─ Section(아키텍처) ←─part_of─ Section(아키텍처 > 저장소)
//!                                                      │
//!                                                      has_chunk
//!                                                      ↓
//!                                                    Chunk
//! ```
//!
//! `part_of` and `has_chunk` are both existing `EdgeType` members (`PART_OF`,
//! `HAS_CHUNK`) and `Section` is an existing `NodeType`, so nothing here
//! widens the schema — the taxonomy had the words, and the writer never wrote
//! them. Everything goes through the public [`GraphWriter::upsert_nodes`] /
//! [`GraphWriter::upsert_edges`] door, after the document itself has landed.
//!
//! Documents with no headings produce no sections. A single fabricated
//! "Untitled section" per file would be worse than nothing: it would put a
//! node in the graph that names something the author never wrote.

use lattice_core::graph_write::pyaux::sha256_text;
use lattice_core::graph_write::types::{ChunkPiece, EdgeSpec, NodeSpec};
use serde_json::{json, Map, Value};

/// `Section -part_of-> parent` — the outline's own spine.
pub const SECTION_PARENT_EDGE: &str = "part_of";
/// `Section -has_chunk-> Chunk` — which text belongs to this heading.
pub const SECTION_CHUNK_EDGE: &str = "has_chunk";
/// The separator `typed_chunks` joins a heading trail with.
pub const HEADING_SEPARATOR: &str = " > ";

/// The nodes and edges one document's outline contributes.
///
/// Not `PartialEq`: `NodeSpec`/`EdgeSpec` are the writer's own request shapes
/// and do not compare, so a test asserts on the fields it means rather than on
/// two whole request vectors.
#[derive(Debug, Clone, Default)]
pub struct SectionOverlay {
    pub nodes: Vec<NodeSpec>,
    pub edges: Vec<EdgeSpec>,
}

impl SectionOverlay {
    pub fn is_empty(&self) -> bool {
        self.nodes.is_empty() && self.edges.is_empty()
    }
}

/// `section:<24 hex>` — stable for one `(workspace, document, heading path)`.
///
/// Scoped by document rather than globally by heading text, because `## 배경`
/// in two different files is two different sections. Two workspaces with the
/// same document id are still kept apart, the way every other id here is.
pub fn section_id(document_id: &str, path: &str, workspace_id: Option<&str>) -> String {
    let scope = workspace_id
        .filter(|id| !id.is_empty())
        .unwrap_or("legacy-global");
    format!(
        "section:{}",
        &sha256_text(&format!("{scope}|{document_id}|{path}"))[..24]
    )
}

/// The heading path one level up, or `None` for a top-level heading.
fn parent_path(path: &str) -> Option<&str> {
    path.rfind(HEADING_SEPARATOR).map(|cut| &path[..cut])
}

/// The last segment — what the author actually typed after the `#`.
fn leaf(path: &str) -> &str {
    path.rsplit(HEADING_SEPARATOR).next().unwrap_or(path)
}

fn heading_of(piece: &ChunkPiece) -> Option<&str> {
    piece
        .fields
        .get("heading_path")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|path| !path.is_empty())
}

/// Build the outline overlay for one ingested document.
///
/// `chunk_ids` is the writer's own return value, positionally aligned with
/// `chunks` — that alignment is the contract `write_chunks` guarantees, and it
/// is what lets a section point at the exact rows it covers. A short
/// `chunk_ids` (a door that did not collect them) simply yields no chunk edges
/// rather than guessing at ids.
pub fn build_overlay(
    chunks: &[ChunkPiece],
    chunk_ids: &[String],
    document_id: &str,
    owner: Option<&str>,
    workspace_id: Option<&str>,
) -> SectionOverlay {
    let mut overlay = SectionOverlay::default();
    if document_id.is_empty() {
        return overlay;
    }
    // Insertion-ordered: sections appear in the order the document introduces
    // them, so a reader walking the overlay reads the outline top to bottom.
    let mut order: Vec<String> = Vec::new();
    let mut counts: Vec<usize> = Vec::new();

    for (index, piece) in chunks.iter().enumerate() {
        let Some(path) = heading_of(piece) else {
            continue;
        };
        let position = match order.iter().position(|known| known == path) {
            Some(position) => position,
            None => {
                order.push(path.to_string());
                counts.push(0);
                order.len() - 1
            }
        };
        counts[position] += 1;
        if let Some(chunk_id) = chunk_ids.get(index) {
            let mut metadata = Map::new();
            metadata.insert("index".into(), json!(index));
            metadata.insert("heading_path".into(), json!(path));
            overlay.edges.push(EdgeSpec {
                from_node: section_id(document_id, path, workspace_id),
                to_node: chunk_id.clone(),
                edge_type: SECTION_CHUNK_EDGE.into(),
                weight: 1.0,
                metadata,
                legacy_label: None,
            });
        }
    }

    // Ancestors: `아키텍처 > 저장소` implies `아키텍처`, even when no chunk
    // landed directly under the parent heading (a heading whose only content
    // is its subsections is still a section).
    let mut complete = order.clone();
    for path in &order {
        let mut cursor = parent_path(path);
        while let Some(ancestor) = cursor {
            if !complete.iter().any(|known| known == ancestor) {
                complete.push(ancestor.to_string());
            }
            cursor = parent_path(ancestor);
        }
    }

    for path in &complete {
        let depth = path.split(HEADING_SEPARATOR).count();
        let chunk_count = order
            .iter()
            .position(|known| known == path)
            .map(|position| counts[position])
            .unwrap_or(0);
        let mut metadata = Map::new();
        metadata.insert("heading_path".into(), json!(path));
        metadata.insert("depth".into(), json!(depth));
        metadata.insert("chunk_count".into(), json!(chunk_count));
        metadata.insert("source_node".into(), json!(document_id));
        metadata.insert("auto_extracted".into(), json!(true));
        metadata.insert("workspace_id".into(), json!(workspace_id));
        overlay.nodes.push(NodeSpec {
            id: section_id(document_id, path, workspace_id),
            node_type: "Section".into(),
            title: leaf(path).to_string(),
            summary: path.clone(),
            metadata,
            raw: Map::new(),
            owner: owner.map(str::to_string),
            workspace_id: workspace_id.map(str::to_string),
            visibility: None,
        });

        // One parent each: the section above it, or the document itself. A
        // subsection hanging off *both* its parent and the document would read
        // as two different claims about where it lives.
        let parent = parent_path(path)
            .map(|ancestor| section_id(document_id, ancestor, workspace_id))
            .unwrap_or_else(|| document_id.to_string());
        let mut metadata = Map::new();
        metadata.insert("heading_path".into(), json!(path));
        metadata.insert("depth".into(), json!(depth));
        overlay.edges.push(EdgeSpec {
            from_node: section_id(document_id, path, workspace_id),
            to_node: parent,
            edge_type: SECTION_PARENT_EDGE.into(),
            weight: 1.0,
            metadata,
            legacy_label: None,
        });
    }
    overlay
}

#[cfg(test)]
mod tests {
    use super::*;

    fn piece(text: &str, heading: Option<&str>) -> ChunkPiece {
        let mut fields = Map::new();
        fields.insert("strategy".into(), json!("markdown"));
        if let Some(path) = heading {
            fields.insert("heading_path".into(), json!(path));
        }
        ChunkPiece {
            text: text.into(),
            fields,
            embedding: None,
        }
    }

    fn ids(count: usize) -> Vec<String> {
        (0..count).map(|index| format!("chunk:{index}")).collect()
    }

    #[test]
    fn a_document_with_no_headings_contributes_nothing() {
        let chunks = vec![piece("plain text", None), piece("more", None)];
        let overlay = build_overlay(&chunks, &ids(2), "doc:1", None, None);
        assert!(overlay.is_empty());
        // …and neither does a document with no id to hang the outline off.
        let headed = vec![piece("x", Some("A"))];
        assert!(build_overlay(&headed, &ids(1), "", None, None).is_empty());
    }

    #[test]
    fn the_outline_becomes_a_tree_rooted_at_the_document() {
        let chunks = vec![
            piece("intro", Some("아키텍처")),
            piece("store one", Some("아키텍처 > 저장소")),
            piece("store two", Some("아키텍처 > 저장소")),
        ];
        let overlay = build_overlay(&chunks, &ids(3), "doc:1", Some("me@x"), Some("alpha"));

        let titles: Vec<&str> = overlay.nodes.iter().map(|n| n.title.as_str()).collect();
        assert_eq!(titles, ["아키텍처", "저장소"]);
        assert!(overlay.nodes.iter().all(|n| n.node_type == "Section"));
        assert_eq!(overlay.nodes[1].summary, "아키텍처 > 저장소");
        assert_eq!(overlay.nodes[1].metadata["depth"], json!(2));
        assert_eq!(overlay.nodes[1].metadata["chunk_count"], json!(2));
        assert_eq!(overlay.nodes[1].owner.as_deref(), Some("me@x"));
        assert_eq!(overlay.nodes[1].workspace_id.as_deref(), Some("alpha"));

        let top = section_id("doc:1", "아키텍처", Some("alpha"));
        let sub = section_id("doc:1", "아키텍처 > 저장소", Some("alpha"));
        let parents: Vec<(&str, &str)> = overlay
            .edges
            .iter()
            .filter(|e| e.edge_type == SECTION_PARENT_EDGE)
            .map(|e| (e.from_node.as_str(), e.to_node.as_str()))
            .collect();
        assert_eq!(
            parents,
            [(top.as_str(), "doc:1"), (sub.as_str(), top.as_str())],
            "a top-level section belongs to the document, a subsection to its parent"
        );

        let chunk_edges: Vec<(&str, &str)> = overlay
            .edges
            .iter()
            .filter(|e| e.edge_type == SECTION_CHUNK_EDGE)
            .map(|e| (e.from_node.as_str(), e.to_node.as_str()))
            .collect();
        assert_eq!(
            chunk_edges,
            [
                (top.as_str(), "chunk:0"),
                (sub.as_str(), "chunk:1"),
                (sub.as_str(), "chunk:2")
            ]
        );
    }

    #[test]
    fn a_heading_whose_only_content_is_its_subsections_still_exists() {
        let chunks = vec![piece("deep", Some("A > B > C"))];
        let overlay = build_overlay(&chunks, &ids(1), "doc:1", None, None);
        let titles: Vec<&str> = overlay.nodes.iter().map(|n| n.title.as_str()).collect();
        assert_eq!(titles, ["C", "B", "A"], "ancestors are filled in");
        let empty = overlay
            .nodes
            .iter()
            .find(|node| node.title == "B")
            .expect("the intermediate heading");
        assert_eq!(empty.metadata["chunk_count"], json!(0));
        assert_eq!(empty.metadata["depth"], json!(2));
    }

    #[test]
    fn ids_are_stable_per_document_and_never_collide_across_them() {
        let one = section_id("doc:1", "배경", None);
        assert_eq!(one, section_id("doc:1", "배경", None));
        assert_ne!(one, section_id("doc:2", "배경", None));
        assert_ne!(one, section_id("doc:1", "배경", Some("alpha")));
        assert!(one.starts_with("section:"));
        assert_eq!(one.len(), "section:".len() + 24);
    }

    #[test]
    fn a_door_that_did_not_collect_chunk_ids_still_gets_its_outline() {
        let chunks = vec![piece("intro", Some("A")), piece("more", Some("A"))];
        let overlay = build_overlay(&chunks, &[], "doc:1", None, None);
        assert_eq!(overlay.nodes.len(), 1);
        assert!(overlay
            .edges
            .iter()
            .all(|edge| edge.edge_type == SECTION_PARENT_EDGE));
        assert_eq!(overlay.nodes[0].metadata["chunk_count"], json!(2));
    }

    #[test]
    fn a_blank_heading_is_no_heading() {
        let chunks = vec![piece("x", Some("   ")), piece("y", Some(""))];
        assert!(build_overlay(&chunks, &ids(2), "doc:1", None, None).is_empty());
    }
}
