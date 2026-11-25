use serde_json::Value;

use crate::kernel::agentloop::guided;
use crate::parse::pystr::is_truthy;

fn value_as_i64(value: &Value) -> Option<i64> {
    value
        .as_i64()
        .or_else(|| value.as_f64().map(|f| f.round() as i64))
        .or_else(|| {
            value
                .as_str()
                .and_then(|s| s.trim().parse::<f64>().ok())
                .map(|f| f.round() as i64)
        })
}

fn element_center_coords(elem: &Value) -> Option<(i64, i64)> {
    let obj = elem.as_object()?;
    if let (Some(cx), Some(cy)) = (
        obj.get("center_x").and_then(value_as_i64),
        obj.get("center_y").and_then(value_as_i64),
    ) {
        return Some((cx, cy));
    }
    if let Some(center) = obj.get("center") {
        if let (Some(cx), Some(cy)) = (
            center.get("x").and_then(value_as_i64),
            center.get("y").and_then(value_as_i64),
        ) {
            return Some((cx, cy));
        }
        if let Some(arr) = center.as_array() {
            if arr.len() >= 2 {
                if let (Some(cx), Some(cy)) = (value_as_i64(&arr[0]), value_as_i64(&arr[1])) {
                    return Some((cx, cy));
                }
            }
        }
    }
    for key in ["bounds", "bbox", "rect", "frame"] {
        let Some(box_val) = obj.get(key) else {
            continue;
        };
        if let Some(bobj) = box_val.as_object() {
            if let (Some(x), Some(y), Some(w), Some(h)) = (
                bobj.get("x").and_then(value_as_i64),
                bobj.get("y").and_then(value_as_i64),
                bobj.get("width")
                    .or_else(|| bobj.get("w"))
                    .and_then(value_as_i64),
                bobj.get("height")
                    .or_else(|| bobj.get("h"))
                    .and_then(value_as_i64),
            ) {
                return Some((x + w / 2, y + h / 2));
            }
            if let (Some(x1), Some(y1), Some(x2), Some(y2)) = (
                bobj.get("x1")
                    .or_else(|| bobj.get("left"))
                    .and_then(value_as_i64),
                bobj.get("y1")
                    .or_else(|| bobj.get("top"))
                    .and_then(value_as_i64),
                bobj.get("x2")
                    .or_else(|| bobj.get("right"))
                    .and_then(value_as_i64),
                bobj.get("y2")
                    .or_else(|| bobj.get("bottom"))
                    .and_then(value_as_i64),
            ) {
                return Some(((x1 + x2) / 2, (y1 + y2) / 2));
            }
        }
        if let Some(arr) = box_val.as_array() {
            if arr.len() >= 4 {
                if let (Some(a), Some(b), Some(c), Some(d)) = (
                    value_as_i64(&arr[0]),
                    value_as_i64(&arr[1]),
                    value_as_i64(&arr[2]),
                    value_as_i64(&arr[3]),
                ) {
                    return Some(((a + c) / 2, (b + d) / 2));
                }
            }
        }
    }
    if let (Some(x), Some(y), Some(w), Some(h)) = (
        obj.get("x").and_then(value_as_i64),
        obj.get("y").and_then(value_as_i64),
        obj.get("width")
            .or_else(|| obj.get("w"))
            .and_then(value_as_i64),
        obj.get("height")
            .or_else(|| obj.get("h"))
            .and_then(value_as_i64),
    ) {
        return Some((x + w / 2, y + h / 2));
    }
    if let (Some(x1), Some(y1), Some(x2), Some(y2)) = (
        obj.get("x1").and_then(value_as_i64),
        obj.get("y1").and_then(value_as_i64),
        obj.get("x2").and_then(value_as_i64),
        obj.get("y2").and_then(value_as_i64),
    ) {
        return Some(((x1 + x2) / 2, (y1 + y2) / 2));
    }
    if let (Some(x), Some(y)) = (
        obj.get("x").and_then(value_as_i64),
        obj.get("y").and_then(value_as_i64),
    ) {
        return Some((x, y));
    }
    None
}

pub(super) fn find_ui_element_center(transcript: &[Value], needle: &str) -> Option<(i64, i64)> {
    let needle_trimmed = needle.trim();
    if needle_trimmed.is_empty() {
        return None;
    }
    let needle_lower = needle_trimmed.to_lowercase();
    for step in transcript.iter().rev() {
        if step.get("error").is_some_and(is_truthy) {
            continue;
        }
        let Some(action) = step.get("action").and_then(Value::as_str) else {
            continue;
        };
        let bare = action
            .strip_prefix(crate::tools::catalog::MCP_PREFIX)
            .unwrap_or(action);
        if bare != "computer_ui_inspect" {
            continue;
        }
        let Some(result) = step.get("result") else {
            continue;
        };
        let mut elements: Vec<&Value> = Vec::new();
        for key in ["elements", "nodes", "items", "ui_elements", "targets"] {
            if let Some(arr) = result.get(key).and_then(Value::as_array) {
                elements.extend(arr.iter());
            }
        }
        if elements.is_empty() {
            continue;
        }
        let field_str = |elem: &Value, key: &str| -> Option<String> {
            match elem.get(key)? {
                Value::String(s) if !s.trim().is_empty() => Some(s.trim().to_string()),
                Value::Number(n) => Some(n.to_string()),
                _ => None,
            }
        };
        for elem in &elements {
            for key in ["id", "element_id", "uid", "ref"] {
                if field_str(elem, key).is_some_and(|val| val.eq_ignore_ascii_case(needle_trimmed))
                {
                    if let Some(coords) = element_center_coords(elem) {
                        return Some(coords);
                    }
                }
            }
        }
        for elem in &elements {
            for key in [
                "label",
                "title",
                "name",
                "text",
                "value",
                "description",
                "ax_title",
            ] {
                if field_str(elem, key).is_some_and(|val| val.to_lowercase() == needle_lower) {
                    if let Some(coords) = element_center_coords(elem) {
                        return Some(coords);
                    }
                }
            }
        }
        for elem in &elements {
            for key in [
                "label",
                "title",
                "name",
                "text",
                "value",
                "description",
                "id",
                "element_id",
            ] {
                if field_str(elem, key)
                    .is_some_and(|val| val.to_lowercase().contains(&needle_lower))
                {
                    if let Some(coords) = element_center_coords(elem) {
                        return Some(coords);
                    }
                }
            }
        }
    }
    None
}

pub(in crate::kernel::agentloop) fn find_request_ui_element_center(
    transcript: &[Value],
    request: &str,
) -> Option<(String, i64, i64)> {
    let req_lower = request.trim().to_lowercase();
    if req_lower.is_empty() {
        return None;
    }
    for step in transcript.iter().rev() {
        if step.get("error").is_some_and(is_truthy) {
            continue;
        }
        let Some(action) = step.get("action").and_then(Value::as_str) else {
            continue;
        };
        let bare = action
            .strip_prefix(crate::tools::catalog::MCP_PREFIX)
            .unwrap_or(action);
        if bare != "computer_ui_inspect" {
            continue;
        };
        let Some(result) = step.get("result") else {
            continue;
        };
        let mut elements: Vec<&Value> = Vec::new();
        for key in ["elements", "nodes", "items", "ui_elements", "targets"] {
            if let Some(arr) = result.get(key).and_then(Value::as_array) {
                elements.extend(arr.iter());
            }
        }
        let mut best: Option<(usize, String, i64, i64)> = None;
        for elem in &elements {
            let Some((cx, cy)) = element_center_coords(elem) else {
                continue;
            };
            for key in [
                "label",
                "title",
                "name",
                "text",
                "description",
                "ax_title",
                "id",
                "element_id",
            ] {
                let Some(val) = elem.get(key).and_then(Value::as_str) else {
                    continue;
                };
                let val_trimmed = val.trim();
                let len = val_trimmed.chars().count();
                if len >= 2
                    && req_lower.contains(&val_trimmed.to_lowercase())
                    && best.as_ref().is_none_or(|(b_len, _, _, _)| len > *b_len)
                {
                    best = Some((len, val_trimmed.to_string(), cx, cy));
                }
            }
        }
        if let Some((_, matched, cx, cy)) = best {
            return Some((matched, cx, cy));
        }
    }
    None
}

pub(in crate::kernel::agentloop) fn url_from_transcript(transcript: &[Value]) -> Option<String> {
    for step in transcript.iter().rev() {
        if step.get("error").is_some_and(is_truthy) {
            continue;
        }
        let Some(action) = step.get("action").and_then(Value::as_str) else {
            continue;
        };
        let bare = action
            .strip_prefix(crate::tools::catalog::MCP_PREFIX)
            .unwrap_or(action);
        if bare != "web_search" {
            continue;
        }
        let Some(result) = step.get("result") else {
            continue;
        };
        for key in ["results", "items", "hits"] {
            if let Some(arr) = result.get(key).and_then(Value::as_array) {
                for item in arr {
                    if let Some(url) = item.get("url").and_then(Value::as_str) {
                        let trimmed = url.trim();
                        if trimmed.starts_with("http://") || trimmed.starts_with("https://") {
                            return Some(trimmed.to_string());
                        }
                    }
                }
            }
        }
    }
    None
}

#[cfg(test)]
fn cap_lean_action_names(names: Vec<String>, limit: usize) -> Vec<String> {
    cap_lean_action_names_for("", false, names, limit)
}

pub(in crate::kernel::agentloop) fn cap_lean_action_names_for(
    request: &str,
    ui_observed: bool,
    names: Vec<String>,
    limit: usize,
) -> Vec<String> {
    const CORE: [&str; 8] = [
        "write_file",
        "read_file",
        "edit_file",
        "list_dir",
        "mcp.grep",
        "mcp.list_dir",
        "mcp.read_file",
        "mcp.knowledge_search",
    ];
    let domain = guided::domain_priority_tools(request, ui_observed);
    let is_protected = |name: &str| {
        CORE.contains(&name)
            || domain.contains(&name)
            || (!request.trim().is_empty() && guided::named_in(request, name))
    };
    let has_final = names.iter().any(|name| name == "final");
    let present: std::collections::BTreeSet<&str> = names.iter().map(String::as_str).collect();
    let mut kept = Vec::new();
    for name in &names {
        if name == "final" {
            continue;
        }
        if kept.len() + 1 < limit {
            kept.push(name.clone());
        }
    }
    for priority in domain.iter().copied().chain(CORE) {
        if !present.contains(priority) || kept.iter().any(|name| name == priority) {
            continue;
        }
        if let Some(index) = kept.iter().rposition(|name| !is_protected(name.as_str())) {
            kept[index] = priority.to_string();
        } else if kept.len() + 1 < limit {
            kept.push(priority.to_string());
        }
    }
    if has_final {
        kept.push("final".into());
    }
    kept
}

#[cfg(test)]
mod lean_list_tests {
    use super::cap_lean_action_names;

    #[test]
    fn a_long_catalog_keeps_the_head_and_final() {
        let names: Vec<String> = (0..20)
            .map(|index| format!("tool_{index}"))
            .chain(std::iter::once("final".into()))
            .collect();
        let capped = cap_lean_action_names(names, 9);
        assert_eq!(capped.len(), 9);
        assert_eq!(capped[0], "tool_0");
        assert_eq!(capped.last().map(String::as_str), Some("final"));
        assert!(!capped.iter().any(|name| name == "tool_19"));
    }

    #[test]
    fn core_file_tools_survive_an_alphabetical_flood() {
        let mut names: Vec<String> = (0..20).map(|index| format!("computer_{index}")).collect();
        names.push("write_file".into());
        names.push("read_file".into());
        names.push("final".into());
        let capped = cap_lean_action_names(names, 9);
        assert!(capped.iter().any(|name| name == "write_file"), "{capped:?}");
        assert!(capped.iter().any(|name| name == "read_file"), "{capped:?}");
        assert_eq!(capped.last().map(String::as_str), Some("final"));
    }

    #[test]
    fn named_mcp_tools_survive_an_alphabetical_flood() {
        let mut names: Vec<String> = (0..20).map(|index| format!("computer_{index}")).collect();
        names.push("mcp.grep".into());
        names.push("mcp.read_file".into());
        names.push("write_file".into());
        names.push("final".into());
        let capped = cap_lean_action_names(names, 9);
        assert!(capped.iter().any(|name| name == "mcp.grep"), "{capped:?}");
        assert!(
            capped.iter().any(|name| name == "mcp.read_file"),
            "{capped:?}"
        );
        assert!(capped.iter().any(|name| name == "write_file"), "{capped:?}");
        assert_eq!(capped.last().map(String::as_str), Some("final"));
    }
}
