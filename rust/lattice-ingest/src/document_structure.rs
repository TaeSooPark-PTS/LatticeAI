use lattice_core::db::CoreError;
use lattice_core::graph_write::pyaux::sha256_text;
use lattice_core::graph_write::types::{ChunkPiece, EdgeSpec, NodeSpec, StructureChild};
use lattice_core::graph_write::GraphWriter;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::document_structure_parsers::{
    build_semantic_table_chunks, build_table_chunks_from_json_rows, extract_heading_topics,
    extract_markdown_images, extract_markdown_tables, split_pdf_pages,
};
use crate::sections;

pub const MAX_ROWS_PER_TABLE_CHUNK: usize = 12;

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct OcrVisionObservation {
    pub image_key: String,
    pub page_number: Option<u32>,
    pub caption: Option<String>,
    pub extracted_text: String,
    pub confidence: f64,
    pub model_or_engine: String,
}

pub trait OcrVisionProvider: Send + Sync {
    fn engine_id(&self) -> &str;
    fn describe_or_ocr(
        &self,
        filename: &str,
        mime_type: &str,
        image_payload: &Map<String, Value>,
    ) -> Option<OcrVisionObservation>;
}

#[derive(Debug, Clone, Default)]
pub struct DocumentStructurePlan {
    pub structure: Map<String, Value>,
    pub structure_nodes: Vec<StructureChild>,
    pub headings: Vec<String>,
    pub table_chunks: Vec<ChunkPiece>,
    pub multimodal_observations: Vec<OcrVisionObservation>,
}

pub fn extract_document_structure(
    filename: &str,
    mime_type: &str,
    text: &str,
    extracted: &Map<String, Value>,
    ocr_provider: Option<&dyn OcrVisionProvider>,
) -> DocumentStructurePlan {
    let mut plan = DocumentStructurePlan::default();
    let lower_name = filename.to_lowercase();

    let mut page_count = 0usize;
    if let Some(pages) = extracted.get("pages").and_then(Value::as_array) {
        for (idx, page) in pages.iter().enumerate() {
            let page_num = page
                .get("page")
                .or_else(|| page.get("index"))
                .and_then(Value::as_u64)
                .unwrap_or((idx + 1) as u64);
            let preview = page
                .get("text")
                .or_else(|| page.get("preview"))
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim()
                .to_string();
            let mut payload = Map::new();
            payload.insert("index".into(), json!(page_num));
            payload.insert("page_number".into(), json!(page_num));
            payload.insert("preview".into(), json!(preview));
            plan.structure_nodes.push(StructureChild {
                kind: "page".into(),
                payload,
                topics: extract_heading_topics(&preview),
            });
            page_count += 1;

            if preview.is_empty() {
                if let Some(provider) = ocr_provider {
                    let mut img_req = Map::new();
                    img_req.insert("page".into(), json!(page_num));
                    img_req.insert("scanned_page".into(), json!(true));
                    if let Some(obs) = provider.describe_or_ocr(filename, mime_type, &img_req) {
                        plan.multimodal_observations.push(obs);
                    }
                }
            }
        }
    } else if lower_name.ends_with(".pdf") || text.contains('\u{000c}') || text.contains("[Page ") {
        for (page_num, segment) in split_pdf_pages(text) {
            let mut payload = Map::new();
            payload.insert("index".into(), json!(page_num));
            payload.insert("page_number".into(), json!(page_num));
            payload.insert("preview".into(), json!(segment));
            plan.structure_nodes.push(StructureChild {
                kind: "page".into(),
                payload,
                topics: extract_heading_topics(&segment),
            });
            page_count += 1;
        }
    }

    let mut slide_count = 0usize;
    if let Some(slides) = extracted.get("slides").and_then(Value::as_array) {
        for (idx, slide) in slides.iter().enumerate() {
            let slide_num = slide
                .get("index")
                .or_else(|| slide.get("slide"))
                .and_then(Value::as_u64)
                .unwrap_or((idx + 1) as u64);
            let texts: Vec<Value> = if let Some(arr) = slide.get("texts").and_then(Value::as_array)
            {
                arr.clone()
            } else if let Some(s) = slide.get("text").and_then(Value::as_str) {
                vec![json!(s)]
            } else {
                Vec::new()
            };
            let joined: String = texts
                .iter()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>()
                .join(
                    "
",
                );
            let mut payload = Map::new();
            payload.insert("index".into(), json!(slide_num));
            payload.insert("texts".into(), Value::Array(texts));
            if let Some(notes) = slide.get("notes").and_then(Value::as_str) {
                payload.insert("speaker_notes".into(), json!(notes));
            }
            plan.structure_nodes.push(StructureChild {
                kind: "slide".into(),
                payload,
                topics: extract_heading_topics(&joined),
            });
            slide_count += 1;
        }
    }

    let mut sheet_count = 0usize;
    let mut table_count = 0usize;

    if let Some(sheets) = extracted.get("sheets").and_then(Value::as_array) {
        for sheet in sheets {
            let title = sheet
                .get("title")
                .or_else(|| sheet.get("name"))
                .and_then(Value::as_str)
                .unwrap_or("Sheet1")
                .to_string();
            let mut payload = Map::new();
            payload.insert("title".into(), json!(title));
            if let Some(rows) = sheet.get("rows").and_then(Value::as_array) {
                payload.insert("row_count".into(), json!(rows.len()));
                let chunks = build_table_chunks_from_json_rows(
                    &title,
                    rows,
                    sheet.get("caption").and_then(Value::as_str),
                    None,
                );
                if !chunks.is_empty() {
                    table_count += 1;
                    plan.table_chunks.extend(chunks);
                }
            }
            plan.structure_nodes.push(StructureChild {
                kind: "sheet".into(),
                payload,
                topics: Vec::new(),
            });
            sheet_count += 1;
        }
    }

    if let Some(tables) = extracted.get("tables").and_then(Value::as_array) {
        for (idx, tbl) in tables.iter().enumerate() {
            let label = tbl
                .get("title")
                .or_else(|| tbl.get("caption"))
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(|| format!("Table {}", idx + 1));
            let page_num = tbl.get("page").and_then(Value::as_u64).map(|n| n as u32);
            if let Some(rows) = tbl.get("rows").and_then(Value::as_array) {
                let chunks = build_table_chunks_from_json_rows(
                    &label,
                    rows,
                    tbl.get("caption").and_then(Value::as_str),
                    page_num,
                );
                if !chunks.is_empty() {
                    table_count += 1;
                    plan.table_chunks.extend(chunks);
                }
            }
        }
    }

    for (idx, md_table) in extract_markdown_tables(text).into_iter().enumerate() {
        let label = md_table
            .caption
            .clone()
            .unwrap_or_else(|| format!("Markdown Table {}", idx + 1));
        let chunks = build_semantic_table_chunks(
            &label,
            &md_table.headers,
            &md_table.rows,
            md_table.caption.as_deref(),
            md_table.page_number,
        );
        if !chunks.is_empty() {
            table_count += 1;
            plan.table_chunks.extend(chunks);
        }
    }

    let mut image_count = 0usize;
    if let Some(images) = extracted.get("images").and_then(Value::as_array) {
        for (idx, img) in images.iter().enumerate() {
            let Some(obj) = img.as_object() else {
                continue;
            };
            let mut payload = obj.clone();
            let img_key = payload
                .get("sha256")
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(|| {
                    sha256_text(&format!(
                        "{filename}:image:{idx}:{}",
                        serde_json::to_string(&payload).unwrap_or_default()
                    ))
                });
            payload
                .entry("sha256".to_string())
                .or_insert_with(|| json!(img_key));
            plan.structure_nodes.push(StructureChild {
                kind: "image".into(),
                payload: payload.clone(),
                topics: Vec::new(),
            });
            image_count += 1;

            let inline_ocr = payload
                .get("ocr_text")
                .or_else(|| payload.get("description"))
                .or_else(|| payload.get("alt"))
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(str::to_string);
            let caption = payload
                .get("caption")
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(str::to_string);
            let page_num = payload
                .get("page")
                .and_then(Value::as_u64)
                .map(|n| n as u32);

            if let Some(ocr_text) = inline_ocr {
                plan.multimodal_observations.push(OcrVisionObservation {
                    image_key: img_key,
                    page_number: page_num,
                    caption,
                    extracted_text: ocr_text,
                    confidence: payload
                        .get("ocr_confidence")
                        .and_then(Value::as_f64)
                        .unwrap_or(0.9),
                    model_or_engine: "inline-parser-metadata".into(),
                });
            } else if let Some(provider) = ocr_provider {
                if let Some(obs) = provider.describe_or_ocr(filename, mime_type, &payload) {
                    plan.multimodal_observations.push(obs);
                }
            } else if let Some(cap) = caption {
                plan.multimodal_observations.push(OcrVisionObservation {
                    image_key: img_key,
                    page_number: page_num,
                    caption: Some(cap.clone()),
                    extracted_text: cap,
                    confidence: 1.0,
                    model_or_engine: "document-caption".into(),
                });
            }
        }
    }

    for (idx, (alt, target)) in extract_markdown_images(text).into_iter().enumerate() {
        let img_key = sha256_text(&format!("{filename}:md_img:{idx}:{target}"));
        let mut payload = Map::new();
        payload.insert("sha256".into(), json!(img_key));
        payload.insert("name".into(), json!(target));
        payload.insert("caption".into(), json!(alt));
        plan.structure_nodes.push(StructureChild {
            kind: "image".into(),
            payload,
            topics: Vec::new(),
        });
        image_count += 1;
        if !alt.trim().is_empty() {
            plan.multimodal_observations.push(OcrVisionObservation {
                image_key: img_key,
                page_number: None,
                caption: Some(alt.clone()),
                extracted_text: format!("Figure caption ({target}): {alt}"),
                confidence: 1.0,
                model_or_engine: "markdown-figure-caption".into(),
            });
        }
    }

    plan.headings = extract_heading_topics(text);
    let heading_count = plan.headings.len();

    plan.structure
        .insert("page_count".into(), json!(page_count));
    plan.structure
        .insert("slide_count".into(), json!(slide_count));
    plan.structure
        .insert("sheet_count".into(), json!(sheet_count));
    plan.structure
        .insert("table_count".into(), json!(table_count));
    plan.structure
        .insert("image_count".into(), json!(image_count));
    plan.structure
        .insert("heading_count".into(), json!(heading_count));
    plan.structure.insert(
        "ocr_applied".into(),
        json!(!plan.multimodal_observations.is_empty()),
    );

    plan
}

pub fn persist_structure_and_multimodal_overlay(
    writer: &GraphWriter,
    document_id: &str,
    chunks: &[ChunkPiece],
    chunk_ids: &[String],
    plan: &DocumentStructurePlan,
    owner: Option<&str>,
    workspace_id: Option<&str>,
) -> Result<usize, CoreError> {
    let mut written_nodes = 0usize;

    let section_overlay =
        sections::build_overlay(chunks, chunk_ids, document_id, owner, workspace_id);
    if !section_overlay.is_empty() {
        written_nodes += section_overlay.nodes.len();
        writer.upsert_nodes(&section_overlay.nodes)?;
        writer.upsert_edges(&section_overlay.edges)?;
    } else if !plan.headings.is_empty() {
        let mut sec_nodes = Vec::new();
        let mut sec_edges = Vec::new();
        for (idx, heading) in plan.headings.iter().enumerate() {
            let sid = sections::section_id(document_id, heading, workspace_id);
            let mut meta = Map::new();
            meta.insert("source_node".into(), json!(document_id));
            meta.insert("heading_path".into(), json!(heading));
            meta.insert("order".into(), json!(idx));
            sec_nodes.push(NodeSpec {
                id: sid.clone(),
                node_type: "Section".into(),
                title: heading.clone(),
                summary: heading.clone(),
                metadata: meta,
                raw: Map::new(),
                owner: owner.map(str::to_string),
                workspace_id: workspace_id.map(str::to_string),
                visibility: None,
            });
            sec_edges.push(EdgeSpec {
                from_node: sid,
                to_node: document_id.to_string(),
                edge_type: "part_of".into(),
                weight: 1.0,
                metadata: Map::new(),
                legacy_label: None,
            });
        }
        written_nodes += sec_nodes.len();
        writer.upsert_nodes(&sec_nodes)?;
        writer.upsert_edges(&sec_edges)?;
    }

    if !plan.multimodal_observations.is_empty() {
        let mut mm_nodes = Vec::new();
        let mut mm_edges = Vec::new();
        let scope = workspace_id
            .filter(|w| !w.is_empty())
            .unwrap_or("legacy-global");

        for obs in &plan.multimodal_observations {
            if obs.extracted_text.trim().is_empty() {
                continue;
            }
            let node_id = format!(
                "imagetext:{}",
                &sha256_text(&format!("{scope}|{document_id}|{}", obs.image_key))[..24]
            );
            let title = obs
                .caption
                .clone()
                .filter(|c| !c.is_empty())
                .unwrap_or_else(|| match obs.page_number {
                    Some(p) => format!("OCR / Visual Text (page {p})"),
                    None => "OCR / Visual Text".into(),
                });
            let mut meta = Map::new();
            meta.insert("source_node".into(), json!(document_id));
            meta.insert("image_key".into(), json!(obs.image_key));
            meta.insert("ocr_confidence".into(), json!(obs.confidence));
            meta.insert("ocr_engine".into(), json!(obs.model_or_engine));
            meta.insert("workspace_id".into(), json!(workspace_id));
            if let Some(p) = obs.page_number {
                meta.insert("page_number".into(), json!(p));
            }
            if let Some(cap) = &obs.caption {
                meta.insert("caption".into(), json!(cap));
            }
            mm_nodes.push(NodeSpec {
                id: node_id.clone(),
                node_type: "ImageText".into(),
                title,
                summary: obs.extracted_text.clone(),
                metadata: meta,
                raw: Map::new(),
                owner: owner.map(str::to_string),
                workspace_id: workspace_id.map(str::to_string),
                visibility: None,
            });
            mm_edges.push(EdgeSpec {
                from_node: node_id,
                to_node: document_id.to_string(),
                edge_type: "part_of".into(),
                weight: obs.confidence.clamp(0.1, 1.0),
                metadata: Map::new(),
                legacy_label: None,
            });
        }
        written_nodes += mm_nodes.len();
        if !mm_nodes.is_empty() {
            writer.upsert_nodes(&mm_nodes)?;
            writer.upsert_edges(&mm_edges)?;
        }
    }

    Ok(written_nodes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use lattice_core::graph_write::types::IngestFileRequest;

    struct StubLocalOcr;

    impl OcrVisionProvider for StubLocalOcr {
        fn engine_id(&self) -> &str {
            "local-vision-ocr-v1"
        }

        fn describe_or_ocr(
            &self,
            _filename: &str,
            _mime_type: &str,
            image_payload: &Map<String, Value>,
        ) -> Option<OcrVisionObservation> {
            let page = image_payload.get("page").and_then(Value::as_u64)? as u32;
            Some(OcrVisionObservation {
                image_key: format!("scanned_page_{page}"),
                page_number: Some(page),
                caption: Some(format!("Scanned diagram page {page}")),
                extracted_text: "OCR: Loopback port 4824 isolates worker traffic.".into(),
                confidence: 0.94,
                model_or_engine: self.engine_id().into(),
            })
        }
    }

    #[test]
    fn preserves_pdf_pages_semantic_tables_captions_and_local_ocr_nodes_in_graph() {
        let dir = tempfile::tempdir().expect("tempdir");
        let pdf_path = dir.path().join("architecture_spec.pdf");
        let md_with_table = "
[Page 1]
# 시스템 보안 명세
Figure 1: ![로컬 루프백 게이트웨이 토폴로지](diagrams/onedoor.png)

## 포트 매핑 표
| 컴포넌트 | 포트 | 외부 노출 |
| --- | --- | --- |
| Lattice Host | 4824 | 차단 (Loopback) |
| Python Worker | 4825 | 차단 (Internal) |
";
        std::fs::write(&pdf_path, md_with_table).expect("write spec");

        let mut extracted = Map::new();
        extracted.insert("content".into(), json!(md_with_table));
        extracted.insert(
            "pages".into(),
            json!([
                {"page": 1, "text": md_with_table},
                {"page": 2, "text": ""}
            ]),
        );

        let ocr = StubLocalOcr;
        let plan = extract_document_structure(
            "architecture_spec.pdf",
            "application/pdf",
            md_with_table,
            &extracted,
            Some(&ocr),
        );

        assert_eq!(plan.structure["page_count"], json!(2));
        assert_eq!(plan.structure["table_count"], json!(1));
        assert_eq!(plan.structure["image_count"], json!(1));
        assert_eq!(plan.structure["ocr_applied"], json!(true));
        assert_eq!(plan.table_chunks.len(), 1);
        assert!(plan.table_chunks[0]
            .text
            .contains("컴포넌트=Lattice Host; 포트=4824; 외부 노출=차단 (Loopback)"));
        assert_eq!(
            plan.table_chunks[0].fields["strategy"],
            json!("table_structured")
        );

        let store = std::sync::Arc::new(
            lattice_core::db::Store::open(&dir.path().join("knowledge_graph.sqlite"))
                .expect("store"),
        );
        let writer = GraphWriter::open(store, dir.path().join("blobs")).expect("writer");
        let mut chunks = crate::chunk_pieces::chunk_set_for(
            md_with_table,
            "architecture_spec.pdf",
            "text/markdown",
            "architecture_spec.pdf",
        )
        .children;
        chunks.extend(plan.table_chunks.clone());

        let outcome = writer
            .ingest_file(&IngestFileRequest {
                path: pdf_path,
                original_filename: Some("architecture_spec.pdf".into()),
                mime_type: Some("application/pdf".into()),
                extracted,
                structure: plan.structure.clone(),
                structure_nodes: plan.structure_nodes.clone(),
                chunks: chunks.clone(),
                workspace_id: Some("personal".into()),
                ..Default::default()
            })
            .expect("ingest file");

        let overlay_written = persist_structure_and_multimodal_overlay(
            &writer,
            &outcome.node_id,
            &chunks,
            &outcome.chunk_ids,
            &plan,
            None,
            Some("personal"),
        )
        .expect("persist multimodal overlay");
        assert!(
            overlay_written >= 3,
            "sections + caption + scanned page OCR"
        );

        let (page_nodes, imagetext_nodes): (i64, i64) = writer
            .store()
            .with_read_conn(|conn| {
                let pages =
                    conn.query_row("SELECT COUNT(*) FROM nodes WHERE type = 'Page'", [], |r| {
                        r.get(0)
                    })?;
                let itext = conn.query_row(
                    "SELECT COUNT(*) FROM nodes WHERE type = 'ImageText'",
                    [],
                    |r| r.get(0),
                )?;
                Ok((pages, itext))
            })
            .expect("counts");
        assert_eq!(page_nodes, 2);
        assert_eq!(imagetext_nodes, 2);
    }
}
