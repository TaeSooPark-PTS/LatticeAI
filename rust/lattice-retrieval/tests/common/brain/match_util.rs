use serde_json::Value;

pub const CAPTURE_DATE: &str = "2026-08-14";

pub const CAPTURE_NOON: &str = "2026-08-14T12:00:00";

pub const CAPTURE_END_OF_DAY: &str = "2026-08-14T23:59:59";

pub fn capture_utc_secs() -> f64 {
    let (date, time) = CAPTURE_END_OF_DAY
        .split_once('T')
        .expect("CAPTURE_END_OF_DAY is `date` T `time`");
    let mut ymd = date
        .split('-')
        .map(|part| part.parse::<i64>().expect("int"));
    let (y, m, d) = (
        ymd.next().expect("year"),
        ymd.next().expect("month"),
        ymd.next().expect("day"),
    );
    let mut hms = time
        .split(':')
        .map(|part| part.parse::<i64>().expect("int"));
    let (hh, mm, ss) = (
        hms.next().expect("hour"),
        hms.next().expect("minute"),
        hms.next().expect("second"),
    );
    (days_from_civil(y, m, d) * 86_400 + hh * 3600 + mm * 60 + ss) as f64
}

fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = y - i64::from(m <= 2);
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

pub(crate) fn urlencoding_lite(value: &str) -> String {
    let mut out = String::new();
    for byte in value.as_bytes() {
        match *byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(*byte as char);
            }
            other => out.push_str(&format!("%{other:02X}")),
        }
    }
    out
}

fn string_matches_tokens(expected: &str, got: &str) -> bool {
    if expected == "@any" || expected == "@ts" || expected == "@uuid" || expected == "@today" {
        return true;
    }
    if expected.starts_with('@') && !expected[1..].contains('@') {
        return true;
    }
    let tokens = [
        "@hostname",
        "@datadir",
        "@home",
        "@corpus",
        "@sandbox",
        "@repo",
        "@stamp",
    ];
    if !tokens.iter().any(|token| expected.contains(token)) {
        return expected == got;
    }
    let mut rest = got;
    let mut remain = expected;
    for token in tokens {
        while let Some(idx) = remain.find(token) {
            let (prefix, after) = remain.split_at(idx);
            if !rest.starts_with(prefix) {
                return false;
            }
            rest = &rest[prefix.len()..];
            remain = &after[token.len()..];
            if remain.is_empty() {
                return true;
            }
            let next_lit_len = tokens
                .iter()
                .filter_map(|t| remain.find(t))
                .min()
                .unwrap_or(remain.len());
            let next_lit = &remain[..next_lit_len];
            if next_lit.is_empty() {
                continue;
            }
            if let Some(pos) = rest.find(next_lit) {
                rest = &rest[pos..];
            } else {
                return false;
            }
        }
    }
    rest == remain || remain.is_empty()
}

pub fn matches_token(expected: &Value, got: &Value) -> bool {
    match expected {
        Value::String(token) => match got {
            Value::String(text) => string_matches_tokens(token, text),
            _ => token == "@any" || token.starts_with('@'),
        },
        Value::Object(exp) => {
            let Some(got_obj) = got.as_object() else {
                return false;
            };
            if exp.len() != got_obj.len() && !exp.values().any(|v| v.as_str() == Some("@any")) {
                return exp.keys().all(|k| {
                    got_obj
                        .get(k)
                        .map(|g| matches_token(&exp[k], g))
                        .unwrap_or(false)
                }) && exp.len() <= got_obj.len();
            }
            exp.iter().all(|(k, v)| {
                let Some(g) = got_obj.get(k) else {
                    return false;
                };
                if k == "near_pairs" || k == "review_only_near_pairs" {
                    return match_pair_bag(v, g);
                }
                if (k == "size_bytes"
                    || k == "node_count"
                    || k == "count"
                    || k == "edges"
                    || k == "total_items"
                    || k == "concept_count"
                    || k == "relationship_count"
                    || k == "graph_concepts"
                    || k == "value"
                    || k == "score"
                    || k == "durable_items"
                    || k == "memory_count")
                    && v.is_number()
                    && g.is_number()
                {
                    return true;
                }
                matches_token(v, g)
            })
        }
        Value::Array(exp) => {
            let Some(got_arr) = got.as_array() else {
                return false;
            };
            if exp.len() != got_arr.len() {
                return false;
            }
            exp.iter()
                .zip(got_arr.iter())
                .all(|(e, g)| matches_token(e, g))
        }
        other => other == got,
    }
}

pub(crate) fn first_diff(expected: &Value, got: &Value, path: &str) -> String {
    if matches_token(expected, got) {
        return format!("{path}: (matches)");
    }
    match (expected, got) {
        (Value::Object(a), Value::Object(b)) => {
            for (k, v) in a {
                match b.get(k) {
                    None => return format!("{path}.{k}: missing in got"),
                    Some(g) if !matches_token(v, g) => {
                        return first_diff(v, g, &format!("{path}.{k}"));
                    }
                    _ => {}
                }
            }
            format!("{path}: extra/mismatch")
        }
        (Value::Array(a), Value::Array(b)) => {
            if a.len() != b.len() {
                return format!("{path}: len {} vs {}", a.len(), b.len());
            }
            for (i, (e, g)) in a.iter().zip(b.iter()).enumerate() {
                if !matches_token(e, g) {
                    return first_diff(e, g, &format!("{path}[{i}]"));
                }
            }
            format!("{path}: array mismatch")
        }
        _ => format!("{path}: {expected} vs {got}"),
    }
}

fn match_pair_bag(expected: &Value, got: &Value) -> bool {
    let (Some(exp), Some(got)) = (expected.as_array(), got.as_array()) else {
        return matches_token(expected, got);
    };
    if exp.len() != got.len() {
        return false;
    }
    let mut used = vec![false; got.len()];
    exp.iter().all(|item| {
        got.iter().enumerate().any(|(idx, candidate)| {
            if used[idx] {
                return false;
            }
            if matches_token(item, candidate) {
                used[idx] = true;
                true
            } else {
                false
            }
        })
    })
}
