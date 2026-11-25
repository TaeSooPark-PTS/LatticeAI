use lattice_core::graph_write::types::ChunkPiece;
use serde_json::{json, Map, Value};

use crate::document_structure::MAX_ROWS_PER_TABLE_CHUNK;

#[derive(Debug)]
pub(crate) struct ParsedMarkdownTable {
    pub caption: Option<String>,
    pub page_number: Option<u32>,
    pub headers: Vec<String>,
    pub rows: Vec<Vec<String>>,
}

pub(crate) fn split_pdf_pages(text: &str) -> Vec<(u32, String)> {
    let mut out = Vec::new();
    if text.contains('\u{000c}') {
        for (idx, part) in text.split('\u{000c}').enumerate() {
            let trimmed = part.trim();
            if !trimmed.is_empty() {
                out.push(((idx + 1) as u32, trimmed.to_string()));
            }
        }
        return out;
    }
    let mut current_page = 1u32;
    let mut buf = Vec::new();
    let mut saw_marker = false;
    for line in text.lines() {
        let trimmed = line.trim();
        if let Some(rest) = trimmed
            .strip_prefix("[Page ")
            .and_then(|s| s.strip_suffix(']'))
        {
            if let Ok(num) = rest.trim().parse::<u32>() {
                if saw_marker && !buf.is_empty() {
                    out.push((
                        current_page,
                        buf.join(
                            "
",
                        ),
                    ));
                    buf.clear();
                }
                current_page = num;
                saw_marker = true;
                continue;
            }
        }
        buf.push(line.to_string());
    }
    if !buf.is_empty() {
        let joined = buf
            .join(
                "
",
            )
            .trim()
            .to_string();
        if !joined.is_empty() {
            out.push((current_page, joined));
        }
    }
    out
}

pub(crate) fn extract_heading_topics(text: &str) -> Vec<String> {
    let mut topics = Vec::new();
    for line in text.lines() {
        let t = line.trim();
        if let Some(heading) = t.strip_prefix('#') {
            let clean = heading.trim_start_matches('#').trim();
            if !clean.is_empty() && !topics.iter().any(|k| k == clean) {
                topics.push(clean.to_string());
            }
        }
    }
    topics
}

fn parse_pipe_row(line: &str) -> Option<Vec<String>> {
    let trimmed = line.trim();
    if !trimmed.starts_with('|') || !trimmed.ends_with('|') || trimmed.len() < 3 {
        return None;
    }
    let cells: Vec<String> = trimmed[1..trimmed.len() - 1]
        .split('|')
        .map(|c| c.trim().to_string())
        .collect();
    (!cells.is_empty()).then_some(cells)
}

fn is_separator_row(cells: &[String]) -> bool {
    !cells.is_empty()
        && cells.iter().all(|c| {
            let core = c.trim_matches(':').trim_matches('-');
            !c.is_empty() && c.contains('-') && core.is_empty()
        })
}

pub(crate) fn extract_markdown_tables(text: &str) -> Vec<ParsedMarkdownTable> {
    let lines: Vec<&str> = text.lines().collect();
    let mut tables = Vec::new();
    let mut i = 0usize;
    let mut current_page: Option<u32> = None;

    while i < lines.len() {
        let trimmed = lines[i].trim();
        if let Some(rest) = trimmed
            .strip_prefix("[Page ")
            .and_then(|s| s.strip_suffix(']'))
        {
            if let Ok(num) = rest.trim().parse::<u32>() {
                current_page = Some(num);
            }
        }
        if i + 2 < lines.len() {
            if let (Some(headers), Some(sep)) =
                (parse_pipe_row(lines[i]), parse_pipe_row(lines[i + 1]))
            {
                if headers.len() == sep.len() && is_separator_row(&sep) {
                    let caption = if i > 0 {
                        let prev = lines[i - 1].trim();
                        if !prev.is_empty() && !prev.starts_with('|') {
                            Some(prev.trim_start_matches('#').trim().to_string())
                        } else {
                            None
                        }
                    } else {
                        None
                    };
                    i += 2;
                    let mut rows = Vec::new();
                    while i < lines.len() {
                        if let Some(row) = parse_pipe_row(lines[i]) {
                            rows.push(row);
                            i += 1;
                        } else {
                            break;
                        }
                    }
                    if !rows.is_empty() {
                        tables.push(ParsedMarkdownTable {
                            caption,
                            page_number: current_page,
                            headers,
                            rows,
                        });
                    }
                    continue;
                }
            }
        }
        i += 1;
    }
    tables
}

pub(crate) fn extract_markdown_images(text: &str) -> Vec<(String, String)> {
    let mut images = Vec::new();
    let mut rest = text;
    while let Some(start) = rest.find("![") {
        let after_bang = &rest[start + 2..];
        let Some(close_alt) = after_bang.find("](") else {
            break;
        };
        let alt = &after_bang[..close_alt];
        let after_paren = &after_bang[close_alt + 2..];
        let Some(close_url) = after_paren.find(')') else {
            break;
        };
        let url = after_paren[..close_url].trim();
        if !url.is_empty() {
            images.push((alt.trim().to_string(), url.to_string()));
        }
        rest = &after_paren[close_url + 1..];
    }
    images
}

pub(crate) fn build_table_chunks_from_json_rows(
    label: &str,
    rows: &[Value],
    caption: Option<&str>,
    page_number: Option<u32>,
) -> Vec<ChunkPiece> {
    if rows.len() < 2 {
        return Vec::new();
    }
    let to_cells = |v: &Value| -> Vec<String> {
        match v {
            Value::Array(arr) => arr
                .iter()
                .map(|c| match c {
                    Value::String(s) => s.trim().to_string(),
                    Value::Null => String::new(),
                    other => other.to_string(),
                })
                .collect(),
            _ => Vec::new(),
        }
    };
    let headers = to_cells(&rows[0]);
    if headers.is_empty() {
        return Vec::new();
    }
    let body_rows: Vec<Vec<String>> = rows[1..]
        .iter()
        .map(to_cells)
        .filter(|r| r.iter().any(|c| !c.is_empty()))
        .collect();
    build_semantic_table_chunks(label, &headers, &body_rows, caption, page_number)
}

pub(crate) fn build_semantic_table_chunks(
    label: &str,
    headers: &[String],
    rows: &[Vec<String>],
    caption: Option<&str>,
    page_number: Option<u32>,
) -> Vec<ChunkPiece> {
    if headers.is_empty() || rows.is_empty() {
        return Vec::new();
    }
    let mut chunks = Vec::new();
    for (batch_idx, window) in rows.chunks(MAX_ROWS_PER_TABLE_CHUNK).enumerate() {
        let start_row = batch_idx * MAX_ROWS_PER_TABLE_CHUNK + 1;
        let end_row = start_row + window.len() - 1;
        let mut lines = Vec::new();
        lines.push(format!("[Table: {label}] Columns: {}", headers.join(" | ")));
        if let Some(cap) = caption.filter(|c| !c.is_empty() && *c != label) {
            lines.push(format!("Caption: {cap}"));
        }
        for (offset, row) in window.iter().enumerate() {
            let pairs: Vec<String> = headers
                .iter()
                .enumerate()
                .map(|(col_idx, col_name)| {
                    let val = row.get(col_idx).map(String::as_str).unwrap_or("");
                    format!("{col_name}={val}")
                })
                .collect();
            lines.push(format!("Row {}: {}", start_row + offset, pairs.join("; ")));
        }

        let mut fields = Map::new();
        fields.insert("strategy".into(), json!("table_structured"));
        fields.insert("table_label".into(), json!(label));
        fields.insert("columns".into(), json!(headers));
        fields.insert("row_start".into(), json!(start_row));
        fields.insert("row_end".into(), json!(end_row));
        if let Some(cap) = caption {
            fields.insert("caption".into(), json!(cap));
        }
        if let Some(page) = page_number {
            fields.insert("page_number".into(), json!(page));
        }

        chunks.push(ChunkPiece {
            text: lines.join(
                "
",
            ),
            fields,
            embedding: None,
        });
    }
    chunks
}
