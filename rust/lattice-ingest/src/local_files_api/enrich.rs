//! Shared extract / parse / embed / chunk helpers for native ingest doors.
//!
//! The worker already serves `POST /worker/parse` (filename + base64 → text)
//! and `POST /worker/embed` (`texts` is a list). These helpers are the only
//! place the upload and browser doors talk to those seams, so a seam failure
//! degrades the same way at every call site: empty extract, no supplied
//! vectors, never a 500.

use axum::http::HeaderMap;
use lattice_core::graph_write::types::{ChunkPiece, ExtractReply, SuppliedVector};
use lattice_core::graph_write::GraphWriter;
use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Map, Value};

use crate::chunk::{chunk_meta_fields, typed_chunks_default};
use crate::strategy::chunk_strategy_for;

pub(crate) const EXTRACT_PATH: &str = "/worker/extract";
pub(crate) const EMBED_PATH: &str = "/worker/embed";
pub(crate) const PARSE_PATH: &str = "/worker/parse";

/// Extensions the worker parser matrix actually reads as documents.
const PARSEABLE_EXTS: &[&str] = &[
    ".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".csv", ".html", ".htm",
];
/// Binary (non-UTF-8) document types — always go through `/worker/parse`.
const BINARY_EXTS: &[&str] = &[".pdf", ".docx", ".xlsx", ".pptx", ".doc", ".odt", ".epub"];
/// Markup that *is* valid UTF-8 but is not what the document says. Reading an
/// `.html` file verbatim put `<div class=…>` and whole `<script>` bodies into
/// the graph; `/worker/parse` turns it into the page's text (v12.0.0).
const MARKUP_EXTS: &[&str] = &[".html", ".htm"];

/// Filename + raw file bytes from an upload request.
///
/// Multipart (`file` field, SPA/Telegram) is unwrapped so the blob we store
/// is the document, not the envelope. A missing name falls back to
/// `upload.bin`, then a magic-byte sniff may replace the extension.
pub(crate) fn unwrap_upload(headers: &HeaderMap, body: &[u8]) -> (String, Vec<u8>) {
    let content_type = header_str(headers, "content-type");
    let hinted = filename_from_headers(headers);
    if let Some((name, bytes)) = parse_multipart(&content_type, body) {
        let name = hinted.unwrap_or(name);
        return (refine_filename(&sanitize_filename(&name), &bytes), bytes);
    }
    let name = hinted.unwrap_or_else(|| "upload.bin".to_string());
    let bytes = body.to_vec();
    (refine_filename(&sanitize_filename(&name), &bytes), bytes)
}

/// True when the upload cannot be treated as UTF-8 text.
pub(crate) fn needs_parse(filename: &str, bytes: &[u8]) -> bool {
    let ext = crate::pystr::py_suffix(filename).to_ascii_lowercase();
    if BINARY_EXTS.contains(&ext.as_str()) || MARKUP_EXTS.contains(&ext.as_str()) {
        return true;
    }
    if looks_like_pdf(bytes) || looks_like_zip(bytes) || looks_like_ole(bytes) {
        return true;
    }
    std::str::from_utf8(bytes).is_err()
}

/// `POST /worker/parse` — text out, or `None` when the seam is down / refuses.
pub(crate) async fn parse_via_seam(
    seam: Option<&WorkerSeamClient>,
    filename: &str,
    bytes: &[u8],
) -> Option<Map<String, Value>> {
    let seam = seam?;
    let payload = seam
        .post_json(
            PARSE_PATH,
            &json!({
                "filename": filename,
                "content_b64": encode_base64(bytes),
            }),
        )
        .await
        .ok()?;
    payload.as_object().cloned()
}

/// Text the extract/embed chain should see. Prefers `content`, then `preview`.
pub(crate) fn parsed_text(parsed: &Map<String, Value>) -> String {
    parsed
        .get("content")
        .and_then(Value::as_str)
        .filter(|text| !text.trim().is_empty())
        .or_else(|| {
            parsed
                .get("preview")
                .and_then(Value::as_str)
                .filter(|text| !text.trim().is_empty())
        })
        .unwrap_or("")
        .to_string()
}

pub(crate) async fn extract_via_seam(
    seam: Option<&WorkerSeamClient>,
    text: &str,
    kind: &str,
) -> ExtractReply {
    if text.trim().is_empty() {
        return ExtractReply::default();
    }
    let Some(seam) = seam else {
        return ExtractReply::default();
    };
    match seam
        .post_json(EXTRACT_PATH, &json!({"text": text, "kind": kind}))
        .await
    {
        Ok(payload) => ExtractReply::from_json(&payload),
        Err(_) => ExtractReply::default(),
    }
}

pub(crate) async fn embed_via_seam(
    seam: Option<&WorkerSeamClient>,
    text: &str,
) -> Option<SuppliedVector> {
    if text.trim().is_empty() {
        return None;
    }
    let (model_id, dim, rows) = embed_texts_via_seam(seam, &[text.to_string()]).await?;
    let values = rows.into_iter().next().filter(|row| !row.is_empty())?;
    Some(SuppliedVector {
        model_id,
        dim: if dim == 0 { values.len() } else { dim },
        values,
    })
}

/// Batch embed. Seam failure → `None` (today's "no supplied vectors").
pub(crate) async fn embed_texts_via_seam(
    seam: Option<&WorkerSeamClient>,
    texts: &[String],
) -> Option<(String, usize, Vec<Vec<f64>>)> {
    if texts.is_empty() {
        return None;
    }
    let seam = seam?;
    let payload = seam
        .post_json(EMBED_PATH, &json!({"texts": texts, "kind": "passage"}))
        .await
        .ok()?;
    let rows = payload.get("vectors").and_then(Value::as_array)?;
    let values: Vec<Vec<f64>> = rows
        .iter()
        .map(|row| {
            row.as_array()
                .map(|cells| cells.iter().filter_map(Value::as_f64).collect())
                .unwrap_or_default()
        })
        .collect();
    if values.iter().all(|row| row.is_empty()) {
        return None;
    }
    let model_id = payload
        .get("model_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let dim = payload.get("dim").and_then(Value::as_u64).unwrap_or(0) as usize;
    Some((model_id, dim, values))
}

/// `typed_chunks` pieces for this document, no vectors yet.
pub(crate) fn chunk_pieces_for(text: &str, filename: &str, mime: &str) -> Vec<ChunkPiece> {
    if text.trim().is_empty() {
        return Vec::new();
    }
    let strategy = chunk_strategy_for(filename, mime);
    typed_chunks_default(text, strategy)
        .into_iter()
        .map(|chunk| ChunkPiece {
            text: chunk.text.clone(),
            fields: chunk_meta_fields(&chunk),
            embedding: None,
        })
        .collect()
}

/// Attach provider vectors only when the embedder model agrees with the writer.
pub(crate) fn attach_chunk_embeddings(
    mut chunks: Vec<ChunkPiece>,
    batch: Option<(String, usize, Vec<Vec<f64>>)>,
    agrees: bool,
) -> Vec<ChunkPiece> {
    if !agrees {
        return chunks;
    }
    let Some((model_id, dim, rows)) = batch else {
        return chunks;
    };
    for (piece, values) in chunks.iter_mut().zip(rows) {
        if values.is_empty() {
            continue;
        }
        piece.embedding = Some(SuppliedVector {
            model_id: model_id.clone(),
            dim: if dim == 0 { values.len() } else { dim },
            values,
        });
    }
    chunks
}

pub(crate) fn model_agrees(graph: &GraphWriter, model_id: &str, dim: usize) -> bool {
    model_id == graph.embedder().model_id() && dim == graph.embedder().dim()
}

pub(crate) fn mime_hint(filename: &str) -> Option<String> {
    match crate::pystr::py_suffix(filename)
        .to_ascii_lowercase()
        .as_str()
    {
        ".pdf" => Some("application/pdf".into()),
        ".docx" => {
            Some("application/vnd.openxmlformats-officedocument.wordprocessingml.document".into())
        }
        ".xlsx" => Some("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet".into()),
        ".pptx" => {
            Some("application/vnd.openxmlformats-officedocument.presentationml.presentation".into())
        }
        ".md" | ".markdown" => Some("text/markdown".into()),
        ".txt" => Some("text/plain".into()),
        ".csv" => Some("text/csv".into()),
        ".html" | ".htm" => Some("text/html".into()),
        _ => None,
    }
}

/// Standard base64 (the worker's `ParseRequest.content_b64`).
pub(crate) fn encode_base64(input: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(input.len().div_ceil(3) * 4);
    let mut index = 0;
    while index < input.len() {
        let b0 = input[index];
        let b1 = if index + 1 < input.len() {
            input[index + 1]
        } else {
            0
        };
        let b2 = if index + 2 < input.len() {
            input[index + 2]
        } else {
            0
        };
        let n = (u32::from(b0) << 16) | (u32::from(b1) << 8) | u32::from(b2);
        out.push(TABLE[((n >> 18) & 63) as usize] as char);
        out.push(TABLE[((n >> 12) & 63) as usize] as char);
        if index + 1 < input.len() {
            out.push(TABLE[((n >> 6) & 63) as usize] as char);
        } else {
            out.push('=');
        }
        if index + 2 < input.len() {
            out.push(TABLE[(n & 63) as usize] as char);
        } else {
            out.push('=');
        }
        index += 3;
    }
    out
}

fn header_str(headers: &HeaderMap, name: &str) -> String {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .to_string()
}

fn filename_from_headers(headers: &HeaderMap) -> Option<String> {
    for name in ["x-filename", "x-file-name", "x-original-filename"] {
        let raw = header_str(headers, name);
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            return Some(sanitize_filename(trimmed));
        }
    }
    let disposition = header_str(headers, "content-disposition");
    disposition_filename(&disposition)
}

fn disposition_filename(header: &str) -> Option<String> {
    for part in header.split(';') {
        let part = part.trim();
        if let Some(rest) = part
            .strip_prefix("filename*=")
            .or_else(|| part.strip_prefix("filename="))
        {
            let stripped = rest.trim().trim_matches('"');
            let value = stripped
                .rsplit_once("''")
                .map(|(_, name)| name)
                .unwrap_or(stripped);
            let cleaned = sanitize_filename(value);
            if !cleaned.is_empty() {
                return Some(cleaned);
            }
        }
    }
    None
}

fn sanitize_filename(name: &str) -> String {
    let trimmed = name.trim().trim_matches('"');
    let base = trimmed.rsplit(['/', '\\']).next().unwrap_or(trimmed).trim();
    if base.is_empty() || base == "." || base == ".." {
        "upload.bin".into()
    } else {
        base.to_string()
    }
}

fn refine_filename(name: &str, bytes: &[u8]) -> String {
    let ext = crate::pystr::py_suffix(name).to_ascii_lowercase();
    if PARSEABLE_EXTS.contains(&ext.as_str()) || BINARY_EXTS.contains(&ext.as_str()) {
        return name.to_string();
    }
    let replacement = if looks_like_pdf(bytes) {
        ".pdf"
    } else if looks_like_zip(bytes) {
        sniff_office_zip(bytes)
    } else if looks_like_ole(bytes) {
        ".doc"
    } else {
        return name.to_string();
    };
    replace_ext(name, replacement)
}

fn replace_ext(name: &str, ext: &str) -> String {
    let stem = match name.rfind('.') {
        Some(dot) if dot > 0 => &name[..dot],
        _ => name,
    };
    let stem = if stem.is_empty() { "upload" } else { stem };
    format!("{stem}{ext}")
}

fn looks_like_pdf(bytes: &[u8]) -> bool {
    bytes.starts_with(b"%PDF")
}

fn looks_like_zip(bytes: &[u8]) -> bool {
    bytes.starts_with(b"PK")
}

fn looks_like_ole(bytes: &[u8]) -> bool {
    bytes.starts_with(&[0xD0, 0xCF, 0x11, 0xE0])
}

fn sniff_office_zip(bytes: &[u8]) -> &'static str {
    let window = &bytes[..bytes.len().min(4096)];
    let hay = String::from_utf8_lossy(window);
    if hay.contains("word/") {
        ".docx"
    } else if hay.contains("xl/") {
        ".xlsx"
    } else if hay.contains("ppt/") {
        ".pptx"
    } else {
        ".docx"
    }
}

fn parse_multipart(content_type: &str, body: &[u8]) -> Option<(String, Vec<u8>)> {
    if !content_type
        .to_ascii_lowercase()
        .starts_with("multipart/form-data")
    {
        return None;
    }
    let boundary = content_type.split(';').find_map(|part| {
        let part = part.trim();
        part.strip_prefix("boundary=")
            .or_else(|| part.strip_prefix("BOUNDARY="))
            .map(|raw| raw.trim().trim_matches('"').to_string())
    })?;
    if boundary.is_empty() {
        return None;
    }
    let delimiter = format!("--{boundary}");
    let delim = delimiter.as_bytes();
    let mut rest = body;
    let mut fallback: Option<(String, Vec<u8>)> = None;
    while let Some(pos) = find_bytes(rest, delim) {
        rest = &rest[pos + delim.len()..];
        if rest.starts_with(b"--") {
            break;
        }
        if rest.starts_with(b"\r\n") {
            rest = &rest[2..];
        } else if rest.starts_with(b"\n") {
            rest = &rest[1..];
        }
        let (headers, after_headers) = split_headers(rest)?;
        let next = find_bytes(after_headers, delim)?;
        let mut data = after_headers[..next].to_vec();
        if data.ends_with(b"\r\n") {
            data.truncate(data.len() - 2);
        } else if data.ends_with(b"\n") {
            data.pop();
        }
        let (field, filename) = part_identity(&headers);
        if filename.is_some() || field.as_deref() == Some("file") {
            let name = filename.unwrap_or_else(|| "upload.bin".into());
            if field.as_deref() == Some("file") {
                return Some((name, data));
            }
            if fallback.is_none() {
                fallback = Some((name, data));
            }
        }
        rest = after_headers;
    }
    fallback
}

fn split_headers(body: &[u8]) -> Option<(String, &[u8])> {
    if let Some(end) = find_bytes(body, b"\r\n\r\n") {
        let headers = std::str::from_utf8(&body[..end]).ok()?.to_string();
        return Some((headers, &body[end + 4..]));
    }
    if let Some(end) = find_bytes(body, b"\n\n") {
        let headers = std::str::from_utf8(&body[..end]).ok()?.to_string();
        return Some((headers, &body[end + 2..]));
    }
    None
}

fn part_identity(headers: &str) -> (Option<String>, Option<String>) {
    let mut field = None;
    let mut filename = None;
    for line in headers.split(['\r', '\n']) {
        let line = line.trim();
        let lower = line.to_ascii_lowercase();
        if !lower.starts_with("content-disposition:") {
            continue;
        }
        for part in line.split(';') {
            let part = part.trim();
            if let Some(value) = part
                .strip_prefix("name=")
                .or_else(|| part.strip_prefix("NAME="))
            {
                field = Some(value.trim_matches('"').to_string());
            }
            if let Some(value) = disposition_filename(part) {
                filename = Some(value);
            }
        }
    }
    (field, filename)
}

fn find_bytes(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() || haystack.len() < needle.len() {
        return None;
    }
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderValue;

    /// Deterministic one-page PDF used by the binary-upload unit tests.
    /// Magic `%PDF` only; the HTTP test mocks `/worker/parse`. Do not regenerate.
    const TINY_PDF: &[u8] = b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/MediaBox[0 0 3 3]>>endobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n147\n%%EOF\n";

    /// Deterministic ZIP-shaped DOCX stub (PK magic). Tests mock the parse seam.
    const TINY_DOCX: &[u8] = b"PK\x03\x04word/document.xml LatticeAI-docx-fixture";

    #[test]
    fn base64_matches_the_standard_alphabet() {
        assert_eq!(encode_base64(b""), "");
        assert_eq!(encode_base64(b"f"), "Zg==");
        assert_eq!(encode_base64(b"fo"), "Zm8=");
        assert_eq!(encode_base64(b"foo"), "Zm9v");
        assert_eq!(encode_base64(b"Hello Lattice"), "SGVsbG8gTGF0dGljZQ==");
    }

    #[test]
    fn a_pdf_is_always_parsed_even_when_named_bin() {
        assert!(needs_parse("upload.bin", TINY_PDF));
        assert!(needs_parse("report.pdf", b"not-a-pdf-but-named-pdf"));
        assert!(!needs_parse("note.md", b"# hello\n"));
        assert!(needs_parse("note.md", &[0x61, 0xff, 0x62]));
        assert!(needs_parse("slide.pptx", TINY_DOCX));
    }

    #[test]
    fn valid_utf8_markup_still_goes_through_the_parser() {
        // The bytes are perfectly readable; what they *say* is not the text.
        let page = b"<html><body><h1>Title</h1><script>var x=1;</script></body></html>";
        assert!(needs_parse("page.html", page));
        assert!(needs_parse("page.HTM", page));
        assert_eq!(mime_hint("page.html").as_deref(), Some("text/html"));
        // A name already carrying a parseable extension is never renamed.
        assert_eq!(refine_filename("page.html", page), "page.html");
    }

    #[test]
    fn magic_bytes_rename_a_generic_upload() {
        assert_eq!(refine_filename("upload.bin", TINY_PDF), "upload.pdf");
        assert_eq!(refine_filename("upload.bin", TINY_DOCX), "upload.docx");
        assert_eq!(refine_filename("handbook.md", b"# hi"), "handbook.md");
    }

    #[test]
    fn multipart_unwraps_the_spa_file_field() {
        let body = b"--bound\r\nContent-Disposition: form-data; name=\"file\"; filename=\"handbook.md\"\r\nContent-Type: text/markdown\r\n\r\n# Lattice handbook\n\nFixture.\r\n--bound--\r\n";
        let (name, bytes) =
            parse_multipart("multipart/form-data; boundary=bound", body).expect("multipart");
        assert_eq!(name, "handbook.md");
        assert_eq!(bytes, b"# Lattice handbook\n\nFixture.");
    }

    #[test]
    fn unwrap_upload_prefers_the_x_filename_header() {
        let mut headers = HeaderMap::new();
        headers.insert("x-filename", HeaderValue::from_static("report.pdf"));
        let (name, bytes) = unwrap_upload(&headers, TINY_PDF);
        assert_eq!(name, "report.pdf");
        assert_eq!(bytes, TINY_PDF);
    }

    #[test]
    fn chunk_pieces_carry_strategy_fields() {
        let pieces = chunk_pieces_for("hello world from lattice", "note.md", "text/markdown");
        assert_eq!(pieces.len(), 1);
        assert_eq!(pieces[0].fields["strategy"], json!("markdown"));
        assert!(pieces[0].embedding.is_none());
    }

    #[test]
    fn attach_skips_vectors_when_the_model_disagrees() {
        let pieces = chunk_pieces_for("hello world from lattice", "note.txt", "");
        let batch = Some(("other-model".into(), 3, vec![vec![1.0, 2.0, 3.0]]));
        let attached = attach_chunk_embeddings(pieces, batch, false);
        assert!(attached[0].embedding.is_none());
    }
}
