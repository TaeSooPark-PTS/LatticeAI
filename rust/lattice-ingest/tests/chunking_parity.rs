//! Python↔Rust chunking parity, over the committed goldens.
//!
//! Same corpus, same strategies, same files the Python chunker produced.
//! Comparison is **exact**: every chunk's text, both of its offsets, its
//! metadata, its id and its two lengths, compared as `serde_json::Value`, so a
//! drifting boundary, a renamed key and an `int` that became a `float` all fail
//! the same way.
//!
//! The goldens are **frozen** — `rust/fixtures/chunking/FROZEN.md` records what
//! wrote them, and there is nothing left to regenerate them with. If one of
//! them and this crate disagree, the chunker changed.

use std::path::PathBuf;
use std::sync::OnceLock;

use lattice_ingest::chunk::{chunk_meta_fields, typed_chunks};
use lattice_ingest::hashes::{
    chunk_id, content_id, file_content_hash, identity_hash, sha256_text, text_content_hash,
    vector_text_hash,
};
use lattice_ingest::pages::{citation_locator, page_for_offset, pdf_page_offsets};
use lattice_ingest::strategy::chunk_strategy_for;
use serde_json::{json, Value};

fn golden_dir() -> PathBuf {
    [
        env!("CARGO_MANIFEST_DIR"),
        "..",
        "fixtures",
        "chunking",
        "golden",
    ]
    .iter()
    .collect()
}

fn read_json(name: &str) -> Value {
    let path = golden_dir().join(name);
    let raw = std::fs::read_to_string(&path).unwrap_or_else(|err| {
        panic!(
            "missing fixture {} ({err}) — these goldens are frozen and \
             committed; see rust/fixtures/chunking/FROZEN.md",
            path.display()
        )
    });
    serde_json::from_str(&raw).expect("fixture must be valid JSON")
}

fn manifest() -> &'static Value {
    static MANIFEST: OnceLock<Value> = OnceLock::new();
    MANIFEST.get_or_init(|| read_json("manifest.json"))
}

fn cases() -> &'static Vec<Value> {
    static CASES: OnceLock<Vec<Value>> = OnceLock::new();
    CASES.get_or_init(|| {
        manifest()["cases"]
            .as_array()
            .cloned()
            .expect("cases array")
    })
}

/// One golden case, re-derived from the Rust port in the golden's own shape.
fn rebuild(golden: &Value) -> Value {
    let text = golden["text"].as_str().expect("text");
    let strategy = golden["strategy"].as_str().expect("strategy");
    let node = golden["source_node_id"].as_str().expect("source_node_id");
    let size = golden["size"].as_i64().expect("size");
    let overlap = golden["overlap"].as_i64().expect("overlap");
    let chunks = typed_chunks(text, strategy, size, overlap);
    let described: Vec<Value> = chunks
        .iter()
        .enumerate()
        .map(|(index, chunk)| {
            json!({
                "index": index,
                "text": chunk.text,
                "meta": chunk.meta,
                "meta_fields": Value::Object(chunk_meta_fields(chunk)),
                "chunk_id": chunk_id(node, index, &chunk.text),
                "len_chars": chunk.text.chars().count(),
                "len_bytes": chunk.text.len(),
            })
        })
        .collect();
    json!({"chunk_count": described.len(), "chunks": described})
}

#[test]
fn every_chunking_case_matches_its_python_golden() {
    let mut failures: Vec<String> = Vec::new();
    let mut checked = 0usize;
    let mut chunks_checked = 0usize;
    for case in cases() {
        let key = case["key"].as_str().expect("key");
        let golden = read_json(&format!("chunks__{key}.json"));
        // The manifest and the case file must agree before anything else does.
        for field in ["strategy", "size", "overlap", "source_node_id", "filename"] {
            assert_eq!(golden[field], case[field], "{key}/{field}");
        }
        let rebuilt = rebuild(&golden);
        chunks_checked += golden["chunk_count"].as_u64().unwrap_or(0) as usize;
        if rebuilt["chunk_count"] != golden["chunk_count"] {
            failures.push(format!(
                "  {key}: {} chunks in rust, {} in python",
                rebuilt["chunk_count"], golden["chunk_count"]
            ));
            continue;
        }
        if rebuilt["chunks"] != golden["chunks"] {
            failures.push(format!(
                "  {key}: {}",
                first_difference(&golden["chunks"], &rebuilt["chunks"])
            ));
        }
        checked += 1;
    }
    assert!(
        failures.is_empty(),
        "{} of {} cases mismatched:\n{}",
        failures.len(),
        cases().len(),
        failures.join("\n")
    );
    assert_eq!(checked, cases().len());
    assert!(
        cases().len() >= 30,
        "the corpus is the coverage — keep it wide"
    );
    assert!(
        chunks_checked >= 250,
        "only {chunks_checked} chunks compared"
    );
}

/// The corpus must actually reach all four strategies, or "parity" would only
/// mean "the plain walk still works".
#[test]
fn the_corpus_covers_every_strategy_and_the_fallback() {
    let strategies: Vec<&str> = manifest()["strategies"]
        .as_array()
        .expect("strategies")
        .iter()
        .map(|value| value.as_str().expect("strategy"))
        .collect();
    for wanted in ["plain", "markdown", "code", "prose"] {
        assert!(
            strategies.contains(&wanted),
            "{wanted} is not in the corpus"
        );
    }
    // An unknown label must fall back to plain, and the corpus must prove it.
    let golden = read_json("chunks__unknown_strategy_falls_back.json");
    let text = golden["text"].as_str().expect("text");
    assert_eq!(golden["strategy"], Value::from("sideways"));
    assert_eq!(
        typed_chunks(text, "sideways", 1200, 160),
        typed_chunks(text, "plain", 1200, 160)
    );
}

/// The whole reason this port indexes characters: byte offsets would disagree.
///
/// Three claims, each proved from the goldens rather than asserted: multibyte
/// chunks exist, their `start_char` is a character offset (a byte offset would
/// be larger), and re-slicing the source *by characters* at that offset
/// reproduces the chunk exactly.
#[test]
fn offsets_are_character_offsets_not_byte_offsets() {
    let mut multibyte_cases = 0usize;
    let mut offsets_that_differ = 0usize;
    for case in cases() {
        let key = case["key"].as_str().expect("key");
        let golden = read_json(&format!("chunks__{key}.json"));
        let cleaned: Vec<char> = golden["text"]
            .as_str()
            .expect("text")
            .trim_matches(lattice_ingest::is_py_space)
            .chars()
            .collect();
        assert_eq!(
            cleaned.len() as u64,
            golden["cleaned_len_chars"]
                .as_u64()
                .expect("cleaned_len_chars"),
            "{key}: character length"
        );
        let multibyte = golden["cleaned_len_bytes"] != golden["cleaned_len_chars"];
        if multibyte {
            multibyte_cases += 1;
        }
        for chunk in golden["chunks"].as_array().expect("chunks") {
            let start = chunk["meta"]["start_char"].as_u64().expect("start_char") as usize;
            let text = chunk["text"].as_str().expect("chunk text");
            let end = start + text.chars().count();
            let slice: String = cleaned[start..end].iter().collect();
            assert_eq!(slice, text, "{key}: chunk at {start} does not re-slice");
            if multibyte && start > 0 {
                // The byte offset of the same chunk is strictly larger, so a
                // byte-indexed port would have produced a different chunk here.
                let byte_offset: usize = cleaned[..start].iter().map(|c| c.len_utf8()).sum();
                if byte_offset > start {
                    offsets_that_differ += 1;
                }
            }
        }
    }
    assert!(
        multibyte_cases >= 15,
        "only {multibyte_cases} multibyte cases"
    );
    assert!(
        offsets_that_differ >= 50,
        "only {offsets_that_differ} chunks where byte and character offsets differ"
    );
}

#[test]
fn the_strategy_router_matches_its_golden() {
    let golden = read_json("strategy_golden.json");
    let cases = golden.as_array().expect("strategy cases");
    for case in cases {
        let filename = case["filename"].as_str().expect("filename");
        let content_type = case["content_type"].as_str().expect("content_type");
        assert_eq!(
            chunk_strategy_for(filename, content_type),
            case["expected"].as_str().expect("expected"),
            "chunk_strategy_for({filename:?}, {content_type:?})"
        );
    }
    assert!(cases.len() >= 25);
}

#[test]
fn the_pdf_arithmetic_matches_its_golden() {
    let golden = read_json("pdf_golden.json");
    for case in golden["structures"].as_array().expect("structures") {
        let expected: Vec<i64> = case["offsets"]
            .as_array()
            .expect("offsets")
            .iter()
            .map(|value| value.as_i64().expect("offset"))
            .collect();
        assert_eq!(
            pdf_page_offsets(&case["structure"]),
            expected,
            "pdf_page_offsets({})",
            case["key"]
        );
    }
    for group in golden["page_for_offset"]
        .as_array()
        .expect("page_for_offset")
    {
        let offsets: Vec<i64> = group["offsets"]
            .as_array()
            .expect("offsets")
            .iter()
            .map(|value| value.as_i64().expect("offset"))
            .collect();
        for probe in group["probes"].as_array().expect("probes") {
            let offset = probe["offset"].as_i64().expect("offset");
            let expected = probe["page"].as_i64();
            assert_eq!(
                page_for_offset(&offsets, offset),
                expected,
                "page_for_offset({offsets:?}, {offset})"
            );
        }
    }
    for case in golden["citation_locator"]
        .as_array()
        .expect("citation_locator")
    {
        let metadata = case["metadata"].as_object().expect("metadata").clone();
        assert_eq!(
            citation_locator(&metadata),
            case["expected"].as_str().expect("expected"),
            "citation_locator({})",
            case["metadata"]
        );
    }
}

#[test]
fn the_hash_conventions_match_their_golden() {
    let golden = read_json("hash_golden.json");
    for case in golden["sha256_text"].as_array().expect("sha256_text") {
        assert_eq!(
            sha256_text(case["text"].as_str().expect("text")),
            case["sha256"].as_str().expect("sha256")
        );
    }
    for case in golden["file_content_hash"]
        .as_array()
        .expect("file_content_hash")
    {
        let bytes = decode_hex(case["bytes_hex"].as_str().expect("bytes_hex"));
        assert_eq!(
            file_content_hash(&bytes),
            case["sha256"].as_str().expect("sha256")
        );
    }
    for case in golden["text_content_hash"]
        .as_array()
        .expect("text_content_hash")
    {
        let content = text_content_hash(
            case["source_type"].as_str().expect("source_type"),
            case["source_uri"].as_str(),
            case["text"].as_str().expect("text"),
        );
        assert_eq!(
            content,
            case["content_hash"].as_str().expect("content_hash")
        );
        let identity = identity_hash(case["workspace_id"].as_str(), &content);
        assert_eq!(
            identity,
            case["identity_hash"].as_str().expect("identity_hash")
        );
        assert_eq!(
            content_id(&identity),
            case["content_id"].as_str().expect("content_id")
        );
    }
    for case in golden["vector_text_hash"]
        .as_array()
        .expect("vector_text_hash")
    {
        assert_eq!(
            vector_text_hash(case["text"].as_str().expect("text")),
            case["text_hash"].as_str().expect("text_hash")
        );
    }
}

/// Chunk ids are pinned per case inside the chunk goldens; this asserts the
/// convention itself against one recorded value so a changed prefix or digest
/// length fails with a name instead of thirty-five diffs.
#[test]
fn chunk_ids_keep_their_shape() {
    let golden = read_json("chunks__plain_default_long.json");
    let node = golden["source_node_id"].as_str().expect("source_node_id");
    let first = &golden["chunks"][0];
    let recorded = first["chunk_id"].as_str().expect("chunk_id");
    assert_eq!(
        chunk_id(node, 0, first["text"].as_str().expect("text")),
        recorded
    );
    assert!(recorded.starts_with("chunk:"));
    assert_eq!(recorded.len(), "chunk:".len() + 24);
}

fn decode_hex(raw: &str) -> Vec<u8> {
    (0..raw.len())
        .step_by(2)
        .map(|index| u8::from_str_radix(&raw[index..index + 2], 16).expect("hex"))
        .collect()
}

/// The first differing path, so a failure names the chunk instead of dumping
/// the whole corpus.
fn first_difference(expected: &Value, got: &Value) -> String {
    let mut trail = Vec::new();
    walk(expected, got, &mut String::new(), &mut trail);
    trail.first().cloned().unwrap_or_else(|| "differs".into())
}

fn walk(expected: &Value, got: &Value, path: &mut String, out: &mut Vec<String>) {
    if !out.is_empty() || expected == got {
        return;
    }
    match (expected, got) {
        (Value::Object(a), Value::Object(b)) => {
            let keys: std::collections::BTreeSet<&String> = a.keys().chain(b.keys()).collect();
            for key in keys {
                let mut next = format!("{path}.{key}");
                match (a.get(key), b.get(key)) {
                    (Some(x), Some(y)) => walk(x, y, &mut next, out),
                    (Some(_), None) => out.push(format!("{next} missing in rust")),
                    (None, Some(_)) => out.push(format!("{next} extra in rust")),
                    (None, None) => {}
                }
            }
        }
        (Value::Array(a), Value::Array(b)) if a.len() == b.len() => {
            for (index, (x, y)) in a.iter().zip(b.iter()).enumerate() {
                let mut next = format!("{path}[{index}]");
                walk(x, y, &mut next, out);
            }
        }
        _ => out.push(format!("{path}: python={expected} rust={got}")),
    }
}
