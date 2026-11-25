use serde_json::Value;
use std::collections::HashMap;

pub fn py_round(value: f64) -> i64 {
    if !value.is_finite() {
        return 0;
    }
    value.round_ties_even() as i64
}

pub fn round_to(value: f64, digits: usize) -> f64 {
    lattice_core::pytext::round_to(value, digits)
}

pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().map(|v| v != 0.0).unwrap_or(true),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

pub fn py_str(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::String(text) => text.clone(),
        Value::Number(number) => py_number(number),
        other => other.to_string(),
    }
}

fn py_number(number: &serde_json::Number) -> String {
    if number.is_i64() || number.is_u64() {
        return number.to_string();
    }
    match number.as_f64() {
        Some(value) if value.fract() == 0.0 && value.abs() < 1e16 => format!("{value:.1}"),
        Some(value) => format!("{value}"),
        None => number.to_string(),
    }
}

pub fn text_of(value: Option<&Value>) -> String {
    match value {
        Some(value) if truthy(value) => py_str(value),
        _ => String::new(),
    }
}

pub fn field_text(record: &Value, key: &str) -> String {
    text_of(record.get(key))
}

pub fn head(text: &str, n: usize) -> String {
    lattice_core::pytext::truncate_chars(text, n)
}

pub fn parse_ts(value: Option<&Value>) -> Option<f64> {
    parse_ts_str(&text_of(value))
}

pub fn parse_ts_str(raw: &str) -> Option<f64> {
    let text = lattice_core::pytext::strip(raw);
    if text.is_empty() {
        return None;
    }
    let text = text.replace('Z', "+00:00");
    let (naive, offset) = split_offset(&text);
    let base = lattice_core::parse_iso(Some(&naive))?;
    Some(base - offset)
}

fn split_offset(text: &str) -> (String, f64) {
    let chars: Vec<char> = text.chars().collect();
    for index in (11..chars.len()).rev() {
        let c = chars[index];
        if c != '+' && c != '-' {
            continue;
        }
        let sign = if c == '-' { -1.0 } else { 1.0 };
        let tail: String = chars[index + 1..].iter().collect();
        let Some(seconds) = offset_seconds(&tail) else {
            break;
        };
        let naive: String = chars[..index].iter().collect();
        return (naive, sign * seconds);
    }
    (text.to_string(), 0.0)
}

fn offset_seconds(tail: &str) -> Option<f64> {
    let digits: String = tail.chars().filter(|c| *c != ':').collect();
    if digits.len() != 2 && digits.len() != 4 && digits.len() != 6 {
        return None;
    }
    if !digits.chars().all(|c| c.is_ascii_digit()) {
        return None;
    }
    let hours: f64 = digits[..2].parse().ok()?;
    let minutes: f64 = if digits.len() >= 4 {
        digits[2..4].parse().ok()?
    } else {
        0.0
    };
    let seconds: f64 = if digits.len() == 6 {
        digits[4..6].parse().ok()?
    } else {
        0.0
    };
    Some(hours * 3600.0 + minutes * 60.0 + seconds)
}

pub fn local_epoch(raw: &str) -> Option<f64> {
    let text = lattice_core::pytext::strip(raw);
    if text.is_empty() {
        return None;
    }
    let text = text.replace('Z', "+00:00");
    let (naive, offset) = split_offset(&text);
    let base = lattice_core::parse_iso(Some(&naive))?;
    if naive.len() != text.len() {
        return Some(base - offset);
    }
    Some(base - local_offset_at(base))
}

fn local_offset_at(naive: f64) -> f64 {
    lattice_core::graph_write::clock::local_offset_for_naive_wall(naive)
}

pub fn word_tokens(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut current = String::new();
    for c in text.chars() {
        if c == '_' || c.is_alphanumeric() {
            current.push(c);
        } else if !current.is_empty() {
            out.push(std::mem::take(&mut current));
        }
    }
    if !current.is_empty() {
        out.push(current);
    }
    out
}

pub fn normalised_words(text: &str) -> String {
    text.split(lattice_core::pytext::is_py_space)
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

#[derive(Debug, Clone, Default)]
pub struct Counter {
    order: Vec<String>,
    index: HashMap<String, usize>,
    counts: Vec<i64>,
}

impl Counter {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn bump(&mut self, key: &str) {
        match self.index.get(key) {
            Some(at) => self.counts[*at] += 1,
            None => {
                self.index.insert(key.to_string(), self.order.len());
                self.order.push(key.to_string());
                self.counts.push(1);
            }
        }
    }

    pub fn get(&self, key: &str) -> i64 {
        self.index.get(key).map(|at| self.counts[*at]).unwrap_or(0)
    }

    pub fn items(&self) -> Vec<(String, i64)> {
        self.order
            .iter()
            .cloned()
            .zip(self.counts.iter().copied())
            .collect()
    }

    pub fn ranked(&self) -> Vec<(String, i64)> {
        let mut items = self.items();
        items.sort_by_key(|item| std::cmp::Reverse(item.1));
        items
    }

    pub fn is_empty(&self) -> bool {
        self.order.is_empty()
    }
}

pub fn dumps_flat_list(items: &[Value]) -> String {
    let parts: Vec<String> = items
        .iter()
        .map(|item| serde_json::to_string(item).unwrap_or_else(|_| "null".to_string()))
        .collect();
    format!("[{}]", parts.join(", "))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_is_the_bankers_rule_python_uses() {
        assert_eq!(py_round(0.5), 0);
        assert_eq!(py_round(1.5), 2);
        assert_eq!(py_round(2.5), 2);
        assert_eq!(py_round(-0.5), 0);
        assert_eq!(py_round(98.4375), 98);
        assert_eq!(py_round(f64::NAN), 0);
        assert_eq!(round_to(0.984_375 * 100.0, 2), 98.44);
    }

    #[test]
    fn truthiness_is_pythons_not_a_null_check() {
        assert!(!truthy(&serde_json::json!(0)));
        assert!(!truthy(&serde_json::json!(0.0)));
        assert!(!truthy(&serde_json::json!("")));
        assert!(!truthy(&serde_json::json!([])));
        assert!(!truthy(&serde_json::json!({})));
        assert!(!truthy(&Value::Null));
        assert!(truthy(&serde_json::json!(1)));
        assert!(truthy(&serde_json::json!({"a": 1})));
        assert_eq!(text_of(Some(&serde_json::json!(0))), "");
        assert_eq!(text_of(Some(&serde_json::json!("x"))), "x");
        assert_eq!(text_of(None), "");
        assert_eq!(py_str(&Value::Null), "None");
        assert_eq!(py_str(&serde_json::json!(true)), "True");
        assert_eq!(py_str(&serde_json::json!(false)), "False");
        assert_eq!(py_str(&serde_json::json!(5)), "5");
        assert_eq!(py_str(&serde_json::json!(5.0)), "5.0");
        assert_eq!(py_str(&serde_json::json!(0.25)), "0.25");
        assert_eq!(py_str(&serde_json::json!([1])), "[1]");
        assert_eq!(field_text(&serde_json::json!({"t": "a"}), "t"), "a");
        assert_eq!(head("한글abcd", 3), "한글a");
    }

    #[test]
    fn a_naive_stamp_reads_as_utc_and_an_offset_is_honoured() {
        let naive = parse_ts_str("2026-08-14T00:00:00").expect("naive");
        assert_eq!(naive, 1_786_665_600.0);
        assert_eq!(parse_ts_str("2026-08-14T00:00:00Z"), Some(naive));
        assert_eq!(parse_ts_str("2026-08-14T00:00:00+00:00"), Some(naive));
        assert_eq!(parse_ts_str("2026-08-14T09:00:00+09:00"), Some(naive));
        assert_eq!(parse_ts_str("2026-08-13T15:00:00-09:00"), Some(naive));
        assert_eq!(parse_ts_str("   "), None);
        assert_eq!(parse_ts_str("not a date"), None);
        assert_eq!(parse_ts_str("2026-08-14T00:00:00+bad"), None);
        assert_eq!(parse_ts(Some(&serde_json::json!(0))), None);
        assert_eq!(local_epoch("2026-08-14T00:00:00+00:00"), Some(naive));
        assert!(local_epoch("2026-08-14T00:00:00").is_some());
        assert_eq!(local_epoch(""), None);
    }

    #[test]
    fn tokens_and_normalisation_follow_the_python_expressions() {
        assert_eq!(word_tokens("a-b c_d 한글1"), vec!["a", "b", "c_d", "한글1"]);
        assert!(word_tokens("---").is_empty());
        assert_eq!(normalised_words("  A  b\tc "), "A b c");
        assert_eq!(
            dumps_flat_list(&[serde_json::json!("한"), Value::Null]),
            "[\"한\", null]"
        );
    }

    #[test]
    fn the_counter_keeps_insertion_order_through_a_stable_ranking() {
        let mut counter = Counter::new();
        assert!(counter.is_empty());
        for key in ["b", "a", "b", "c", "a"] {
            counter.bump(key);
        }
        assert_eq!(counter.get("b"), 2);
        assert_eq!(counter.get("zz"), 0);
        assert_eq!(
            counter.ranked(),
            vec![
                ("b".to_string(), 2),
                ("a".to_string(), 2),
                ("c".to_string(), 1)
            ]
        );
        assert_eq!(counter.items().len(), 3);
        assert!(!counter.is_empty());
    }
}
