//! The embedding port, pinned bit-for-bit against CPython.
//!
//! `rust/fixtures/golden/embeddings_golden.json` is written by
//! `scripts/generate_rust_parity_fixtures.py` from the real
//! `lattice_brain.embeddings` model. If any of these assertions fail, the vector
//! lane is scoring against different vectors than the ones Python indexed —
//! which shows up as a subtly different ranking, never as an error.

use std::path::PathBuf;

use lattice_core::embeddings::{hash_to_index, tokenize};
use lattice_core::{round6, LocalEmbeddingModel};
use serde_json::Value;

fn golden(name: &str) -> Value {
    let path: PathBuf = [env!("CARGO_MANIFEST_DIR"), "..", "fixtures", "golden", name]
        .iter()
        .collect();
    let raw = std::fs::read_to_string(&path)
        .unwrap_or_else(|err| panic!("missing golden {}: {err}", path.display()));
    serde_json::from_str(&raw).expect("golden must be valid JSON")
}

#[test]
fn embeddings_match_the_python_model() {
    let data = golden("embeddings_golden.json");
    let dim = data["dim"].as_u64().unwrap() as usize;
    let model = LocalEmbeddingModel::new(dim);
    assert_eq!(model.model_id(), data["model_id"].as_str().unwrap());

    let cases = data["cases"].as_array().unwrap();
    assert!(
        cases.len() >= 8,
        "the golden must cover ko/en/mixed/symbol texts"
    );
    for case in cases {
        let text = case["text"].as_str().unwrap();

        let expected_features: Vec<&str> = case["features"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(
            tokenize(text),
            expected_features,
            "tokenizer drift on {text:?}"
        );

        for entry in case["hashes"].as_array().unwrap() {
            let feature = entry["feature"].as_str().unwrap();
            let (index, sign) = hash_to_index(feature, dim);
            assert_eq!(
                index as u64,
                entry["index"].as_u64().unwrap(),
                "index for {feature:?}"
            );
            assert_eq!(
                sign,
                entry["sign"].as_f64().unwrap(),
                "sign for {feature:?}"
            );
        }

        let expected: Vec<f64> = case["vector"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_f64().unwrap())
            .collect();
        let got = model.embed(text);
        assert_eq!(got.len(), expected.len(), "vector width on {text:?}");
        for (i, (a, b)) in got.iter().zip(expected.iter()).enumerate() {
            assert_eq!(
                a.to_bits(),
                b.to_bits(),
                "component {i} of {text:?}: {a} != {b}"
            );
        }

        let encoded = hex(&model.encode(&got));
        assert_eq!(
            encoded,
            case["encoded_hex"].as_str().unwrap(),
            "encoding of {text:?}"
        );
        let decoded = model.decode(&model.encode(&got), Some(dim));
        assert_eq!(decoded.len(), dim);
    }
}

#[test]
fn round6_matches_cpython_on_the_pinned_values() {
    let cases = golden("rounding_golden.json");
    let cases = cases.as_array().unwrap();
    assert!(cases.len() >= 12);
    for case in cases {
        let input = case["input"].as_f64().unwrap();
        let expected = case["expected"].as_f64().unwrap();
        assert_eq!(
            round6(input).to_bits(),
            expected.to_bits(),
            "round6({input}) = {} want {expected}",
            round6(input)
        );
    }
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
