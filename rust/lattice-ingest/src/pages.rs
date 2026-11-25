use serde_json::Value;

pub use lattice_core::pytext::citation_locator;

pub fn pdf_page_offsets(structure: &Value) -> Vec<i64> {
    let Some(object) = structure.as_object() else {
        return Vec::new();
    };
    let Some(Value::Array(pages)) = object.get("pages") else {
        return Vec::new();
    };
    if pages.is_empty() {
        return Vec::new();
    }
    let mut offsets = Vec::with_capacity(pages.len());
    let mut cursor: i64 = 0;
    for page in pages {
        let Some(page) = page.as_object() else {
            return Vec::new();
        };
        let chars = match page.get("chars") {
            Some(Value::Number(number)) => number.as_f64().unwrap_or(-1.0),
            _ => return Vec::new(),
        };
        if chars < 0.0 {
            return Vec::new();
        }
        offsets.push(cursor);
        cursor += chars.trunc() as i64 + 2;
    }
    offsets
}

pub fn page_for_offset(page_offsets: &[i64], offset: i64) -> Option<i64> {
    if page_offsets.is_empty() {
        return None;
    }
    let mut page = 0i64;
    for (index, start) in page_offsets.iter().enumerate() {
        if offset >= *start {
            page = index as i64 + 1;
        } else {
            break;
        }
    }
    (page >= 1).then_some(page)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn offsets_accumulate_the_two_character_joiner() {
        let structure = json!({"pages": [{"chars": 100}, {"chars": 250}, {"chars": 40}]});
        assert_eq!(pdf_page_offsets(&structure), vec![0, 102, 354]);
        assert_eq!(
            pdf_page_offsets(&json!({"pages": [{"chars": 1200}]})),
            vec![0]
        );
        assert_eq!(
            pdf_page_offsets(&json!({"pages": [{"chars": 10.9}, {"chars": 5.0}]})),
            vec![0, 12]
        );
    }

    #[test]
    fn anything_malformed_answers_nothing() {
        for structure in [
            json!({"meta": 1}),
            json!({"pages": {"chars": 5}}),
            json!({"pages": []}),
            json!({"pages": [{"chars": 5}, 7]}),
            json!({"pages": [{"chars": 5}, {}]}),
            json!({"pages": [{"chars": 5}, {"chars": -1}]}),
            json!({"pages": [{"chars": true}]}),
            json!({"pages": [{"chars": "5"}]}),
            json!([1, 2, 3]),
            json!(null),
        ] {
            assert!(pdf_page_offsets(&structure).is_empty(), "{structure}");
        }
    }

    #[test]
    fn a_page_is_the_last_start_at_or_before_the_offset() {
        let offsets = vec![0, 102, 354];
        assert_eq!(page_for_offset(&offsets, 0), Some(1));
        assert_eq!(page_for_offset(&offsets, 101), Some(1));
        assert_eq!(page_for_offset(&offsets, 102), Some(2));
        assert_eq!(page_for_offset(&offsets, 353), Some(2));
        assert_eq!(page_for_offset(&offsets, 354), Some(3));
        assert_eq!(page_for_offset(&offsets, 10_000), Some(3));
        assert_eq!(page_for_offset(&offsets, -1), None);
        assert_eq!(page_for_offset(&[], 5), None);
        assert_eq!(page_for_offset(&[0, 2, 12], 2), Some(2));
    }

    #[test]
    fn the_locator_comes_from_lattice_core_and_still_answers() {
        let mut meta = serde_json::Map::new();
        assert_eq!(citation_locator(&meta), "");
        meta.insert("page".into(), Value::from(3));
        meta.insert("page_end".into(), Value::from(5));
        assert_eq!(citation_locator(&meta), "p.3–5");
    }
}
