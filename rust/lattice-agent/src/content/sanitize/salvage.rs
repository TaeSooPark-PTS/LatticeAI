use crate::parse::pystr::char_len;

use super::extract::slice_json_document;
use super::python::python_parses;
use super::text::{ext_of, starts_with_ci};
use super::validate::{check_balanced_delimiters, check_component_blocks, looks_like_refusal};

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
