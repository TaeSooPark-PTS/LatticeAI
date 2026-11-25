use lattice_core::graph_write::pyaux::sha256_text;
use lattice_core::graph_write::types::{ChunkPiece, EdgeSpec, NodeSpec};
use serde_json::{json, Map, Value};

pub const SECTION_PARENT_EDGE: &str = "part_of";
pub const SECTION_CHUNK_EDGE: &str = "has_chunk";
pub const HEADING_SEPARATOR: &str = " > ";

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

pub fn section_id(document_id: &str, path: &str, workspace_id: Option<&str>) -> String {
    let scope = workspace_id
        .filter(|id| !id.is_empty())
        .unwrap_or("legacy-global");
    format!(
        "section:{}",
        &sha256_text(&format!("{scope}|{document_id}|{path}"))[..24]
    )
}

fn parent_path(path: &str) -> Option<&str> {
    path.rfind(HEADING_SEPARATOR).map(|cut| &path[..cut])
}

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
