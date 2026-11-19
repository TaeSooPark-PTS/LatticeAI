use super::*;

#[test]
fn normalize_is_pythons_two_strips_and_a_character_cap() {
    // `_WHITESPACE.sub(" ", …).strip().strip(_TRAILING)`: the trailing set
    // is stripped from **both** ends, and it contains a space, so the two
    // strips compose.
    assert_eq!(
        normalize("  파이썬을   좋아합니다.  "),
        "파이썬을 좋아합니다"
    );
    assert_eq!(normalize("(quoted)"), "quoted");
    assert_eq!(normalize("...."), "");
    assert_eq!(normalize("a\n\tb"), "a b");
    // The cap counts code points, not bytes: 200 Korean characters are 600
    // bytes, and a byte slice would land mid-character.
    let long = "가".repeat(200);
    assert_eq!(normalize(&long).chars().count(), MAX_FACT_CHARS);
}

#[test]
fn a_fact_id_is_the_statement_not_the_wording() {
    // `sha256(f"{kind}|{text.lower()}")[:12]`, so case does not matter and
    // the same statement under two kinds is two facts.
    assert_eq!(
        fact_id("preference", "답변은 한국어로 받고 싶습니다"),
        "self:preference:e28d88323aff"
    );
    assert_eq!(
        fact_id("decision", "Keep Alpha Fusion"),
        fact_id("decision", "keep alpha fusion")
    );
    assert_ne!(fact_id("decision", "x"), fact_id("habit", "x"));
}

#[test]
fn extraction_is_deterministic_deduplicated_and_ordered() {
    let text = "저는 파이썬을 좋아합니다. 매일 아침 회의록을 정리합니다. \
                I decided to keep alpha fusion.";
    let first = extract(text, "fixture");
    assert_eq!(first, extract(text, "fixture"), "the same text twice");
    let seen: Vec<(&str, &str)> = first
        .iter()
        .map(|fact| {
            (
                fact["kind"].as_str().unwrap_or_default(),
                fact["signal"].as_str().unwrap_or_default(),
            )
        })
        .collect();
    // Sorted by (kind, text), which is Python's `sorted(...)` key.
    assert_eq!(
        seen,
        vec![
            ("decision", "en_decided_to"),
            ("habit", "ko_routine"),
            ("preference", "ko_first_person_preference"),
        ]
    );
    assert_eq!(first[2]["text"], json!("파이썬"));
    assert_eq!(first[2]["source"], json!("fixture"));
    // One statement said twice is one candidate.
    let twice = extract("I decided to ship it. I decided to ship it.", "");
    assert_eq!(twice.len(), 1, "{twice:?}");
    // Nothing first-person is nothing at all — no fabricated profile.
    assert!(extract("The build finished.", "").is_empty());
    // A capture under two characters is dropped (`len(value) < 2`).
    assert!(extract("Decision: x", "").is_empty());
}

#[test]
fn every_kind_has_a_node_type_and_nothing_else_does() {
    for kind in crate::self_model::KIND_ORDER {
        assert!(node_type_for(kind).is_some(), "{kind}");
        assert!(!kind_label(kind).is_empty(), "{kind}");
    }
    assert!(node_type_for("Preference").is_none(), "kinds are lowercase");
    assert!(node_type_for("").is_none());
}

#[test]
fn a_refusal_carries_the_code_the_router_reads() {
    // `self_model::seam_error` picks the catalog sentence by looking for
    // the code *inside* the detail, which is how the Python worker
    // reported `SelfModelError.code`. If this format changes, every
    // Self-Model refusal silently becomes a 500.
    let refused = refusal(400, "invalid_kind");
    assert_eq!(refused.detail, "invalid_kind", "the code travels alone");
    assert_eq!(
        crate::memory_api::self_model::seam_error(&refused.detail, "ko").status(),
        400
    );
    let missing = refusal(404, "not_found");
    assert_eq!(
        crate::memory_api::self_model::seam_error(&missing.detail, "ko").status(),
        404
    );
}
