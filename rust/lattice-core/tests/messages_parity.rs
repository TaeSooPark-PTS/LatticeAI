//! Python↔Rust message-catalog parity.
//!
//! `scripts/gen_messages_catalog_fixture.py` dumps every id the live Python
//! catalog renders in both languages, plus a `resolve_language` vector table
//! that walks every branch of the Python function. This test is the Rust half:
//! if a wording, interpolation, envelope key, or Accept-Language branch
//! drifts, it fails here rather than on a migrated route.

use std::path::PathBuf;

use lattice_core::messages::{http_error, resolve_language, text};
use serde_json::Value;

fn fixture() -> Value {
    let path: PathBuf = [
        env!("CARGO_MANIFEST_DIR"),
        "..",
        "fixtures",
        "messages_catalog.json",
    ]
    .iter()
    .collect();
    let raw = std::fs::read_to_string(&path)
        .unwrap_or_else(|err| panic!("missing fixture {}: {err}", path.display()));
    serde_json::from_str(&raw).expect("messages_catalog.json must be valid JSON")
}

fn args_of(value: &Value) -> Vec<(String, String)> {
    value
        .as_object()
        .expect("args is an object")
        .iter()
        .map(|(key, val)| {
            (
                key.clone(),
                val.as_str()
                    .unwrap_or_else(|| panic!("arg {key} must be a string"))
                    .to_owned(),
            )
        })
        .collect()
}

#[test]
fn catalog_templates_match_python_byte_for_byte() {
    let data = fixture();
    let catalog = data["catalog"].as_object().expect("catalog object");
    assert!(
        !catalog.is_empty(),
        "fixture catalog must list every Python id"
    );
    for (id, entry) in catalog {
        for lang in ["ko", "en"] {
            let expected = entry[lang]
                .as_str()
                .unwrap_or_else(|| panic!("{id}.{lang} must be a string"));
            let got = text(id, lang, &[]);
            assert_eq!(got, expected, "template drift on {id} / {lang}");
        }
    }
}

#[test]
fn every_id_renders_identically_in_both_languages() {
    let data = fixture();
    let renders = data["renders"].as_array().expect("renders array");
    assert!(
        renders.len() >= 2,
        "fixture must render every id in ko and en"
    );
    for row in renders {
        let id = row["id"].as_str().expect("render.id");
        let lang = row["lang"].as_str().expect("render.lang");
        let expected = row["text"].as_str().expect("render.text");
        let owned = args_of(&row["args"]);
        let args: Vec<(&str, &str)> = owned
            .iter()
            .map(|(key, val)| (key.as_str(), val.as_str()))
            .collect();
        let got = text(id, lang, &args);
        assert_eq!(got, expected, "render drift on {id} / {lang}");

        let envelope = &row["http_error"];
        let err = http_error(418, id, lang, &args);
        assert_eq!(err.status, 418, "status is caller-chosen; body is catalog");
        assert_eq!(&err.body, envelope, "http_error envelope on {id} / {lang}");
        assert_eq!(err.detail(), expected);
        let (status, body) = err.into_response_parts();
        assert_eq!(status, 418);
        assert_eq!(&body, envelope);
    }
}

#[test]
fn resolve_language_matches_every_python_vector() {
    let data = fixture();
    let vectors = data["resolve_language"]
        .as_array()
        .expect("resolve_language array");
    assert!(
        vectors.len() >= 20,
        "fixture must cover every resolve_language branch"
    );
    for row in vectors {
        let case_id = row["id"].as_str().expect("vector.id");
        let expected = row["expected"].as_str().expect("vector.expected");
        let got = resolve_language(
            row["x_lattice_language"].as_str(),
            row["accept_language"].as_str(),
        );
        assert_eq!(got, expected, "resolve_language drift on {case_id}");
    }
}
