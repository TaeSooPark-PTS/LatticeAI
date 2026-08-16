//! `/agent/eval` — static skill-schema cases, no model required.

use std::path::{Path, PathBuf};

use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use serde_json::{json, Value};

use super::AgentsState;
use lattice_auth::response::json_response;

use crate::mcp::{detail, missing_fields, parse_json_object, require_admin};

pub(crate) async fn agent_eval(
    State(state): State<AgentsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    if let Err(r) = require_admin(&state.auth, &headers) {
        return r;
    }
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    if !parsed
        .as_object()
        .map(|o| o.contains_key("skill"))
        .unwrap_or(false)
    {
        return missing_fields(&parsed, &["skill"]);
    }
    let skill = parsed.get("skill").and_then(Value::as_str).unwrap_or("");
    if !skill_name_ok(skill) {
        return detail(StatusCode::BAD_REQUEST, "Invalid skill name.");
    }
    let skill_dir = resolve_skill_dir(&state.skills_root, skill);
    let schema_path = skill_dir.join("schema.json");
    let schema_text = match std::fs::read_to_string(&schema_path) {
        Ok(text) => text,
        Err(_) => {
            return detail(
                StatusCode::NOT_FOUND,
                &format!("Skill '{skill}' not found or missing schema.json"),
            );
        }
    };
    let schema: Value = match serde_json::from_str(&schema_text) {
        Ok(value) => value,
        Err(err) => {
            return detail(
                StatusCode::BAD_REQUEST,
                &format!("Skill '{skill}' schema.json is not JSON: {err}"),
            );
        }
    };
    let examples = std::fs::read_to_string(skill_dir.join("examples.md")).unwrap_or_default();
    let evals = schema
        .get("evals")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let input_schema = schema.get("input").cloned().unwrap_or(json!({}));
    let mut cases = Vec::new();
    let mut pass = 0u64;
    let mut fail = 0u64;
    let mut requires_model = 0u64;
    for case in evals {
        let id = case
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or("unnamed")
            .to_string();
        let input = case.get("input").cloned().unwrap_or(json!({}));
        let criteria = case
            .get("pass_criteria")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let verdict = eval_case(&input_schema, &input, &criteria, &examples);
        match verdict.verdict.as_str() {
            "pass" => pass += 1,
            "fail" => fail += 1,
            _ => requires_model += 1,
        }
        cases.push(json!({
            "id": id,
            "verdict": verdict.verdict,
            "detail": verdict.detail,
            "pass_criteria": criteria,
        }));
    }
    let body = json!({
        "skill": skill,
        "schema": schema_path.display().to_string(),
        "cases": cases,
        "summary": {
            "total": pass + fail + requires_model,
            "pass": pass,
            "fail": fail,
            "requires_model": requires_model,
        },
    });
    json_response(
        StatusCode::OK,
        &serde_json::to_string(&body).unwrap_or_else(|_| "{}".into()),
        None,
    )
}

fn resolve_skill_dir(root: &Path, skill: &str) -> PathBuf {
    root.join(skill)
}

struct CaseVerdict {
    verdict: String,
    detail: String,
}

fn eval_case(input_schema: &Value, input: &Value, criteria: &str, examples: &str) -> CaseVerdict {
    let schema_error = schema_input_error(input_schema, input);
    let wants_invalid = criteria.contains("INVALID_INPUT")
        || criteria.contains("success == false") && !criteria.contains("success == true");
    if let Some(error) = schema_error {
        if wants_invalid {
            return CaseVerdict {
                verdict: "pass".into(),
                detail: format!("input fails schema: {error}"),
            };
        }
        return CaseVerdict {
            verdict: "fail".into(),
            detail: format!("input is not valid against schema.json: {error}"),
        };
    }
    if wants_invalid {
        return CaseVerdict {
            verdict: "fail".into(),
            detail: "input satisfies schema.json; INVALID_INPUT was not produced".into(),
        };
    }
    if criteria_needs_model(criteria) {
        return CaseVerdict {
            verdict: "requires_model".into(),
            detail: "pass_criteria inspects a model-produced result and was not executed".into(),
        };
    }
    if criteria.contains("FILE_NOT_FOUND") {
        let path = input
            .get("path")
            .or_else(|| input.get("target"))
            .and_then(Value::as_str)
            .unwrap_or("");
        if !path.is_empty() && !Path::new(path).exists() {
            return CaseVerdict {
                verdict: "pass".into(),
                detail: format!("path does not exist: {path}"),
            };
        }
        return CaseVerdict {
            verdict: "fail".into(),
            detail: "path exists or was not supplied; FILE_NOT_FOUND is not statically true".into(),
        };
    }
    if criteria.contains("UNSUPPORTED_FORMAT") || criteria.contains("BINARY_FILE") {
        if examples_mention_input(examples, input) {
            return CaseVerdict {
                verdict: "pass".into(),
                detail: "input matches a documented static failure example".into(),
            };
        }
        return CaseVerdict {
            verdict: "requires_model".into(),
            detail: "format/binary criteria cannot be confirmed without executing the skill".into(),
        };
    }
    CaseVerdict {
        verdict: "requires_model".into(),
        detail: "pass_criteria is not statically checkable".into(),
    }
}

fn criteria_needs_model(criteria: &str) -> bool {
    let lower = criteria.to_ascii_lowercase();
    lower.contains("success == true")
        || lower.contains("score")
        || lower.contains("len(")
        || lower.contains("keywords")
        || lower.contains("results")
        || lower.contains("issues")
}

fn schema_input_error(schema: &Value, input: &Value) -> Option<String> {
    let obj = match input.as_object() {
        Some(obj) => obj,
        None => return Some("input is not an object".into()),
    };
    let required = schema
        .get("required")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    for field in required {
        let name = field.as_str()?;
        match obj.get(name) {
            None => return Some(format!("missing required field '{name}'")),
            Some(Value::String(text)) if text.trim().is_empty() => {
                return Some(format!("required field '{name}' is empty"));
            }
            Some(Value::Null) => return Some(format!("required field '{name}' is null")),
            _ => {}
        }
    }
    if let Some(properties) = schema.get("properties").and_then(Value::as_object) {
        for (name, spec) in properties {
            let Some(value) = obj.get(name) else {
                continue;
            };
            if let Some(expected) = spec.get("type").and_then(Value::as_str) {
                if !json_type_matches(value, expected) {
                    return Some(format!("field '{name}' is not {expected}"));
                }
            }
            if let Some(allowed) = spec.get("enum").and_then(Value::as_array) {
                if value.is_string() && !allowed.iter().any(|item| item == value) {
                    return Some(format!("field '{name}' is not an allowed enum value"));
                }
                if let Some(items) = value.as_array() {
                    for item in items {
                        if !allowed.iter().any(|choice| choice == item) {
                            return Some(format!(
                                "field '{name}' contains a value outside its enum"
                            ));
                        }
                    }
                }
            }
        }
    }
    None
}

fn json_type_matches(value: &Value, expected: &str) -> bool {
    match expected {
        "string" => value.is_string(),
        "integer" => value.is_i64() || value.is_u64(),
        "number" => value.is_number(),
        "boolean" => matches!(value, Value::Bool(_)),
        "array" => value.is_array(),
        "object" => value.is_object(),
        _ => true,
    }
}

fn examples_mention_input(examples: &str, input: &Value) -> bool {
    let Ok(needle) = serde_json::to_string(input) else {
        return false;
    };
    examples.contains(&needle) || examples.contains(&needle.replace(' ', ""))
}

fn skill_name_ok(skill: &str) -> bool {
    let mut chars = skill.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    if !first.is_ascii_alphanumeric() {
        return false;
    }
    let rest: String = chars.collect();
    if rest.len() > 63 {
        return false;
    }
    rest.chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '_' || c == '-')
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_required_field_is_a_static_invalid_input_pass() {
        let schema = json!({
            "required": ["target"],
            "properties": {"target": {"type": "string"}}
        });
        let verdict = eval_case(
            &schema,
            &json!({"target": ""}),
            "error == INVALID_INPUT",
            "",
        );
        assert_eq!(verdict.verdict, "pass");
    }

    #[test]
    fn success_score_criteria_require_a_model() {
        let schema = json!({
            "required": ["target"],
            "properties": {"target": {"type": "string"}}
        });
        let verdict = eval_case(
            &schema,
            &json!({"target": "def foo():\n  return 1\n"}),
            "success == true and score < 80",
            "",
        );
        assert_eq!(verdict.verdict, "requires_model");
    }

    #[test]
    fn missing_required_field_fails_when_criteria_expect_success() {
        let schema = json!({"required": ["path"], "properties": {"path": {"type": "string"}}});
        let verdict = eval_case(&schema, &json!({}), "success == true", "");
        assert_eq!(verdict.verdict, "fail");
    }

    #[test]
    fn skill_name_rejects_path_escape() {
        assert!(!skill_name_ok("../etc"));
        assert!(!skill_name_ok(""));
        assert!(skill_name_ok("code_review"));
    }
}
