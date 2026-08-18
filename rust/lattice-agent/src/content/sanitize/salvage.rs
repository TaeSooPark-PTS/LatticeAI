//! `_salvage_score` — how useful a rejected candidate is as raw material.
//!
//! A port of the last piece of `latticeai.core.file_generation.orchestration`
//! (deleted in 6a03294, read back out of `659de86`). v11.7.0 ported the
//! pipeline's *pure* stages — validate, extract, repair — and left its driver
//! behind, which quietly dropped three behaviours the weak-model path was built
//! out of. This is the first of them.
//!
//! The question it answers: two candidates were both rejected, which one should
//! [`super::repair_file_content`] work from? **Not the longer one.** Python's
//! comment on that is worth keeping verbatim, because it is the bug report:
//!
//! > Longest-wins handed repair a 900-character apology in preference to a
//! > 300-character HTML document that only needed its `</html>` closing — and
//! > repair can finish the document but can only bury the apology.
//!
//! So the score is `(tier, length)`, compared tier first:
//!
//! * **2** — the right shape, finishable: an HTML document that opens
//!   correctly, parseable-ish JSON, Python that tokenises, balanced braces.
//! * **1** — ordinary text: no structure, but the words may be the content.
//! * **0** — a refusal. Repair should prefer literally anything else, because
//!   an apology written into the file is worse than an empty stub.

use crate::parse::pystr::char_len;

use super::extract::slice_json_document;
use super::python::python_parses;
use super::text::{ext_of, starts_with_ci};
use super::validate::{check_balanced_delimiters, check_component_blocks, looks_like_refusal};

/// `(tier, length)` for one rejected candidate. Higher is better material.
pub fn salvage_score(candidate: &str, target_path: &str) -> (u8, usize) {
    let text = candidate.trim();
    if text.is_empty() {
        return (0, 0);
    }
    let length = char_len(text);
    if looks_like_refusal(text) {
        return (0, length);
    }
    let finishable = match ext_of(target_path).as_str() {
        ".html" | ".htm" => starts_with_ci(text, "<!doctype") || starts_with_ci(text, "<html"),
        ".json" => slice_json_document(text).is_some(),
        // Deviation, the same one `validate` already declares: there is no
        // CPython here, so "tokenises" is the structural check, not `ast.parse`.
        ".py" => python_parses(text).is_ok(),
        ".js" | ".jsx" | ".ts" | ".tsx" => check_balanced_delimiters(text).0,
        ".vue" | ".svelte" => check_component_blocks(text).0,
        ".css" => text.contains('{') && text.contains('}'),
        _ => false,
    };
    (if finishable { 2 } else { 1 }, length)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_short_document_beats_a_long_apology() {
        // The exact regression Python's comment describes.
        let document = "<!doctype html>\n<html><body><h1>Hi</h1></body>";
        let apology = "I'm sorry, but I can't create that file for you. ".repeat(6);
        assert!(
            looks_like_refusal(&apology),
            "the fixture must be a refusal"
        );
        assert!(
            salvage_score(document, "a.html") > salvage_score(&apology, "a.html"),
            "tier must outrank length"
        );
        assert_eq!(salvage_score(document, "a.html").0, 2);
        assert_eq!(salvage_score(&apology, "a.html").0, 0);
    }

    #[test]
    fn length_only_breaks_ties_inside_a_tier() {
        let short = "<!doctype html><html>";
        let long = "<!doctype html><html><body>plenty more text here</body>";
        assert!(salvage_score(long, "a.html") > salvage_score(short, "a.html"));
        assert_eq!(
            salvage_score(long, "a.html").0,
            salvage_score(short, "a.html").0
        );
    }

    #[test]
    fn every_target_type_has_a_finishable_tier() {
        for (candidate, path) in [
            ("<html><body>", "a.html"),
            ("{\"a\": 1}", "a.json"),
            ("def f():\n    return 1\n", "a.py"),
            ("function f() { return 1; }", "a.js"),
            ("<template><p/></template>", "a.vue"),
            ("body { color: red; }", "a.css"),
        ] {
            assert_eq!(salvage_score(candidate, path).0, 2, "{path}");
        }
    }

    #[test]
    fn the_wrong_shape_for_its_type_is_ordinary_text_not_material() {
        for (candidate, path) in [
            ("Here is the page you asked for.", "a.html"),
            ("not json at all", "a.json"),
            ("def f(:\n", "a.py"),
            ("function f( { return 1;", "a.js"),
            ("<template><p/>", "a.vue"),
            ("no rules here", "a.css"),
            // A type with no grammar is never tier 2 and never tier 0 either.
            ("# A note\n\nbody", "a.md"),
        ] {
            assert_eq!(salvage_score(candidate, path).0, 1, "{path}: {candidate}");
        }
    }

    #[test]
    fn nothing_at_all_scores_nothing() {
        assert_eq!(salvage_score("", "a.html"), (0, 0));
        assert_eq!(salvage_score("   \n  ", "a.md"), (0, 0));
    }
}
