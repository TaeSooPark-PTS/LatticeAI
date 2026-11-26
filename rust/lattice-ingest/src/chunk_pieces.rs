use lattice_core::graph_write::chunk_parents::{
    chunk_index_text, CONTEXT_PREFIX_FIELD, PARENT_INDEX_FIELD,
};
use lattice_core::graph_write::types::{ChunkParentPiece, ChunkPiece};
use serde_json::{json, Map, Value};

use crate::chunk::{
    plan_chunks, ChunkPlan, ChunkingConfig, PlannedChunk, PlannedParent, TOKEN_CHUNKER,
};

#[derive(Debug, Clone, Default)]
pub struct ChunkSet {
    pub children: Vec<ChunkPiece>,
    pub parents: Vec<ChunkParentPiece>,
}

impl ChunkSet {
    pub fn embedding_inputs(&self) -> Vec<String> {
        embedding_inputs(&self.children)
    }
}

pub fn embedding_inputs(pieces: &[ChunkPiece]) -> Vec<String> {
    pieces
        .iter()
        .map(|piece| chunk_index_text(&piece.text, &piece.fields))
        .collect()
}

fn insert_opt(fields: &mut Map<String, Value>, key: &str, value: Option<&str>) {
    if let Some(value) = value.filter(|v| !v.is_empty()) {
        fields.insert(key.into(), json!(value));
    }
}

fn child_fields(chunk: &PlannedChunk, plan: &ChunkPlan) -> Map<String, Value> {
    let mut fields = Map::new();
    fields.insert("strategy".into(), json!(chunk.strategy));
    fields.insert("start_char".into(), json!(chunk.start_char));
    insert_opt(&mut fields, "heading_path", chunk.heading_path.as_deref());
    fields.insert("end_char".into(), json!(chunk.end_char));
    fields.insert("token_count".into(), json!(chunk.token_count));
    fields.insert("chunker".into(), json!(TOKEN_CHUNKER));
    fields.insert("chunk_mode".into(), json!(plan.mode.as_str()));
    insert_opt(&mut fields, "symbol_path", chunk.symbol_path.as_deref());
    insert_opt(&mut fields, "ast_language", chunk.ast_language);
    if let Some(index) = chunk.parent_index {
        fields.insert(PARENT_INDEX_FIELD.into(), json!(index));
    }
    insert_opt(
        &mut fields,
        CONTEXT_PREFIX_FIELD,
        chunk.context_prefix.as_deref(),
    );
    fields
}

fn parent_fields(parent: &PlannedParent, plan: &ChunkPlan) -> Map<String, Value> {
    let mut fields = Map::new();
    fields.insert("strategy".into(), json!(plan.strategy));
    fields.insert("start_char".into(), json!(parent.start_char));
    fields.insert("end_char".into(), json!(parent.end_char));
    fields.insert("token_count".into(), json!(parent.token_count));
    insert_opt(&mut fields, "heading_path", parent.heading_path.as_deref());
    insert_opt(&mut fields, "symbol_path", parent.symbol_path.as_deref());
    fields
}

pub fn chunk_set_with(
    text: &str,
    filename: &str,
    mime: &str,
    title: &str,
    config: &ChunkingConfig,
) -> ChunkSet {
    if text.trim().is_empty() {
        return ChunkSet::default();
    }
    let plan = plan_chunks(text, filename, mime, title, config);
    ChunkSet {
        children: plan
            .children
            .iter()
            .map(|chunk| ChunkPiece {
                text: chunk.text.clone(),
                fields: child_fields(chunk, &plan),
                embedding: None,
            })
            .collect(),
        parents: plan
            .parents
            .iter()
            .map(|parent| ChunkParentPiece {
                text: parent.text.clone(),
                fields: parent_fields(parent, &plan),
            })
            .collect(),
    }
}

pub fn chunk_set_for(text: &str, filename: &str, mime: &str, title: &str) -> ChunkSet {
    chunk_set_with(text, filename, mime, title, &ChunkingConfig::from_env())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chunk::ChunkMode;

    #[test]
    fn a_short_note_carries_strategy_token_and_prefix_fields() {
        let set = chunk_set_with(
            "hello world from lattice",
            "note.md",
            "text/markdown",
            "Note",
            &ChunkingConfig::default(),
        );
        assert_eq!(set.children.len(), 1);
        assert!(set.parents.is_empty());
        let fields = &set.children[0].fields;
        assert_eq!(fields["strategy"], json!("markdown"));
        assert_eq!(fields["chunker"], json!(TOKEN_CHUNKER));
        assert_eq!(fields["chunk_mode"], json!("parent_child"));
        assert_eq!(fields["context_prefix"], json!("Document: Note"));
        assert!(fields.get(PARENT_INDEX_FIELD).is_none());
        assert_eq!(
            set.embedding_inputs(),
            vec!["Document: Note\nhello world from lattice".to_string()]
        );
        assert!(chunk_set_for("  ", "a.md", "", "a").children.is_empty());
    }

    #[test]
    fn long_documents_produce_linked_parents_and_children() {
        let text = (0..2400)
            .map(|i| format!("token{}", i % 211))
            .collect::<Vec<_>>()
            .join(" ");
        let set = chunk_set_with(&text, "long.txt", "", "Long", &ChunkingConfig::default());
        assert!(!set.parents.is_empty());
        for child in &set.children {
            let index = child.fields[PARENT_INDEX_FIELD].as_u64().expect("linked") as usize;
            assert!(set.parents[index].text.contains(&child.text));
        }
        assert!(set.parents[0].fields["token_count"].as_u64().unwrap_or(0) >= 800);
        let flat = chunk_set_with(
            &text,
            "long.txt",
            "",
            "Long",
            &ChunkingConfig {
                mode: ChunkMode::Flat,
                contextual: false,
                ..ChunkingConfig::default()
            },
        );
        assert!(flat.parents.is_empty());
        assert!(flat
            .children
            .iter()
            .all(|c| c.fields.get(CONTEXT_PREFIX_FIELD).is_none()));
        assert_eq!(flat.embedding_inputs()[0], flat.children[0].text);
    }

    #[test]
    fn code_children_record_the_syntax_tree_language_and_symbol() {
        let source = "def load(path):\n    return path\n\nclass Store:\n    pass\n";
        let set = chunk_set_with(
            source,
            "store.py",
            "",
            "store.py",
            &ChunkingConfig::default(),
        );
        let fields = &set.children[0].fields;
        assert_eq!(fields["ast_language"], json!("python"));
        assert_eq!(fields["strategy"], json!("code"));
        assert!(fields.contains_key("symbol_path"));
    }
}
