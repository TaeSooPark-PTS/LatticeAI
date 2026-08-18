//! `normalize_plan` — the minimal plan schema, enforced before execution.
//!
//! Seven rules in a fixed order (`latticeai.core.agent_helpers.normalize_plan`).
//! Order is the contract, not an implementation detail: manifest rewriting runs
//! *after* junk-step filtering and *before* the single-file heuristic, so a plan
//! that was emptied by filtering still reaches the manifest, and a manifest
//! request never falls through to the one-file fallback.
//!
//! Each applied repair is named in `fixes`, verbatim, because the loop trace and
//! the weak-model harness read those labels.

use serde_json::{json, Map, Value};

use crate::parse::inference::{infer_file_target, infer_project_manifest};
use crate::parse::pystr::{char_slice, is_truthy, py_str};

/// Plan actions that count as "this plan only writes files".
const FILE_CREATE_PLAN_ACTIONS: [&str; 2] = ["generate_file", "write_file"];

/// A normalised plan plus the repairs that were needed.
pub type Normalized = (Map<String, Value>, Vec<String>);

/// `str(path or "")`, then the lowercased suffix from the last `.`.
fn extension(path: Option<&Value>) -> String {
    let text = match path {
        Some(value) if is_truthy(value) => py_str(value),
        _ => String::new(),
    };
    match text.rfind('.') {
        Some(dot) => text[dot..].to_lowercase(),
        None => String::new(),
    }
}

fn step_path(step: &Value) -> Option<&Value> {
    step.get("args")
        .filter(|args| is_truthy(args))
        .and_then(|args| args.get("path"))
}

/// True when a pure file-writing plan fails to cover the manifest's file types.
fn plan_misses_manifest(steps: &[Value], manifest: &Value) -> bool {
    let all_file_creates = steps.iter().all(|step| {
        step.get("action")
            .and_then(Value::as_str)
            .is_some_and(|action| FILE_CREATE_PLAN_ACTIONS.contains(&action))
    });
    if !all_file_creates {
        return false;
    }
    let planned: Vec<String> = steps
        .iter()
        .map(|step| extension(step_path(step)))
        .collect();
    manifest["files"]
        .as_array()
        .map(|files| {
            files
                .iter()
                .any(|spec| !planned.contains(&extension(spec.get("path"))))
        })
        .unwrap_or(false)
}

/// `int(value or 0)` — Python's coercion, including which inputs raise.
fn python_int(value: Option<&Value>) -> Result<i64, ()> {
    let Some(value) = value.filter(|value| is_truthy(value)) else {
        return Ok(0);
    };
    match value {
        Value::Bool(_) => Ok(1),
        Value::Number(number) => match number.as_i64() {
            Some(exact) => Ok(exact),
            // `int(3.7) == 3`: truncation toward zero, not rounding.
            None => number.as_f64().map(|float| float as i64).ok_or(()),
        },
        Value::String(text) => {
            let cleaned: String = text.trim().chars().filter(|c| *c != '_').collect();
            cleaned.parse::<i64>().map_err(|_| ())
        }
        // `int([1])` / `int({'a': 1})` raise TypeError, which the caller records.
        _ => Err(()),
    }
}

/// Enforce the minimal plan schema so execution never starts adrift.
pub fn normalize_plan(plan: &Value, user_message: &str) -> Normalized {
    let mut fixes: Vec<String> = Vec::new();
    let mut normalized = match plan {
        Value::Object(map) => map.clone(),
        _ => {
            fixes.push("plan_not_object".into());
            Map::new()
        }
    };

    let goal = match normalized.get("goal") {
        Some(value) if is_truthy(value) => py_str(value).trim().to_string(),
        _ => String::new(),
    };
    if goal.is_empty() {
        normalized.insert("goal".into(), json!(user_message));
        fixes.push("goal_defaulted".into());
    }

    let raw_steps = normalized.get("steps").cloned().unwrap_or(Value::Null);
    let mut steps: Vec<Value> = raw_steps
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter(|step| step.is_object() && step.get("action").is_some_and(is_truthy))
                .cloned()
                .collect()
        })
        .unwrap_or_default();
    // `if raw_steps and steps != raw_steps` — a truthy non-list also differs.
    if is_truthy(&raw_steps) && Value::Array(steps.clone()) != raw_steps {
        fixes.push("steps_filtered".into());
    }

    // Manifest-aware planning: for a recognised multi-file project the manifest,
    // not the planner's improvisation, decides the file set — but only when the
    // plan is empty or is a pure file-writing plan that misses part of it.
    if let Some(manifest) = infer_project_manifest(user_message) {
        let manifest_steps: Vec<Value> = manifest["files"]
            .as_array()
            .map(|files| {
                files
                    .iter()
                    .map(|spec| {
                        json!({
                            "action": "write_file",
                            "args": {"path": spec["path"]},
                            "description": spec["brief"],
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();
        if steps.is_empty() {
            steps = manifest_steps;
            fixes.push("manifest_steps".into());
        } else if plan_misses_manifest(&steps, &manifest) {
            steps = manifest_steps;
            fixes.push("manifest_rewrite".into());
        }
    }

    if steps.is_empty() {
        if let Some(inferred) = infer_file_target(user_message) {
            steps = vec![json!({
                "action": "write_file",
                "args": {"path": inferred},
                "description": format!(
                    "Create {inferred} for: {}", char_slice(user_message, 120)
                ),
            })];
            fixes.push("heuristic_file_step".into());
        }
    }
    let step_count = steps.len() as i64;
    normalized.insert("steps".into(), Value::Array(steps));

    let estimated = match python_int(normalized.get("estimated_steps")) {
        Ok(value) => value,
        Err(()) => {
            fixes.push("estimated_steps_invalid".into());
            0
        }
    };
    normalized.insert(
        "estimated_steps".into(),
        json!(estimated.max(1).max(step_count)),
    );
    normalized.insert(
        "requires_approval".into(),
        json!(normalized.get("requires_approval").is_some_and(is_truthy)),
    );
    if !normalized
        .get("rollback_strategy")
        .is_some_and(Value::is_string)
    {
        normalized.insert("rollback_strategy".into(), json!("none"));
    }
    (normalized, fixes)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn normalize(plan: Value, message: &str) -> Normalized {
        normalize_plan(&plan, message)
    }

    #[test]
    fn a_complete_plan_is_left_alone() {
        let plan = json!({
            "goal": "read the notes",
            "steps": [{"action": "read_file", "args": {"path": "a.md"}}],
            "estimated_steps": 1,
            "requires_approval": false,
            "rollback_strategy": "none",
        });
        let (normalized, fixes) = normalize(plan.clone(), "read the notes");
        assert!(fixes.is_empty(), "{fixes:?}");
        assert_eq!(Value::Object(normalized), plan);
    }

    #[test]
    fn a_non_object_plan_becomes_an_empty_one() {
        for junk in [json!(null), json!("a plan"), json!([1, 2]), json!(7)] {
            let (normalized, fixes) = normalize(junk.clone(), "hi");
            assert!(fixes.contains(&"plan_not_object".to_string()), "{junk}");
            assert_eq!(normalized["goal"], "hi");
            assert_eq!(normalized["estimated_steps"], 1);
            assert_eq!(normalized["rollback_strategy"], "none");
            assert_eq!(normalized["requires_approval"], false);
        }
    }

    #[test]
    fn a_blank_goal_defaults_to_the_request() {
        for blank in [json!(""), json!("   "), json!(null), json!(0)] {
            let (normalized, fixes) = normalize(json!({"goal": blank}), "do the thing");
            assert!(fixes.contains(&"goal_defaulted".to_string()));
            assert_eq!(normalized["goal"], "do the thing");
        }
        // A non-blank non-string goal is kept exactly as it arrived.
        let (normalized, fixes) = normalize(json!({"goal": 5}), "x");
        assert!(!fixes.contains(&"goal_defaulted".to_string()));
        assert_eq!(normalized["goal"], 5);
    }

    #[test]
    fn junk_steps_are_filtered_and_the_filtering_is_named() {
        let (normalized, fixes) = normalize(
            json!({"goal": "g", "steps": ["nope", {"no_action": 1}, {"action": ""}, {"action": "read_file"}]}),
            "g",
        );
        assert!(fixes.contains(&"steps_filtered".to_string()));
        assert_eq!(normalized["steps"], json!([{"action": "read_file"}]));
        // A truthy non-list `steps` is also a filtering event.
        let (_, fixes) = normalize(json!({"goal": "g", "steps": "read a file"}), "g");
        assert!(fixes.contains(&"steps_filtered".to_string()));
        // An empty list is falsy: nothing was filtered, so nothing is claimed.
        let (_, fixes) = normalize(json!({"goal": "g", "steps": []}), "g");
        assert!(!fixes.contains(&"steps_filtered".to_string()));
    }

    #[test]
    fn an_empty_plan_for_a_manifest_request_gets_the_manifest() {
        let (normalized, fixes) = normalize(
            json!({"goal": "g", "steps": []}),
            "todo 앱 html css js 만들어줘",
        );
        assert_eq!(fixes, vec!["manifest_steps".to_string()]);
        let paths: Vec<&str> = normalized["steps"]
            .as_array()
            .expect("steps")
            .iter()
            .map(|step| step["args"]["path"].as_str().expect("path"))
            .collect();
        assert_eq!(paths, vec!["index.html", "style.css", "app.js"]);
        assert_eq!(
            normalized["estimated_steps"], 3,
            "the step count is a floor"
        );
    }

    #[test]
    fn a_pure_write_plan_that_misses_a_manifest_type_is_rewritten() {
        let (normalized, fixes) = normalize(
            json!({"goal": "g", "steps": [{"action": "write_file", "args": {"path": "index.html"}}]}),
            "todo 앱 html css js 만들어줘",
        );
        assert!(fixes.contains(&"manifest_rewrite".to_string()));
        assert_eq!(normalized["steps"].as_array().expect("steps").len(), 3);
    }

    #[test]
    fn a_plan_that_already_covers_every_type_is_untouched() {
        let steps = json!([
            {"action": "write_file", "args": {"path": "page.HTML"}},
            {"action": "write_file", "args": {"path": "a.css"}},
            {"action": "generate_file", "args": {"path": "b.js"}},
        ]);
        let (normalized, fixes) = normalize(
            json!({"goal": "g", "steps": steps.clone()}),
            "todo 앱 html css js 만들어줘",
        );
        assert!(
            !fixes.iter().any(|fix| fix.starts_with("manifest")),
            "{fixes:?}"
        );
        assert_eq!(normalized["steps"], steps);
    }

    #[test]
    fn a_plan_with_a_non_write_step_reflects_real_intent_and_survives() {
        let steps = json!([
            {"action": "read_file", "args": {"path": "spec.md"}},
            {"action": "write_file", "args": {"path": "index.html"}},
        ]);
        let (normalized, fixes) = normalize(
            json!({"goal": "g", "steps": steps.clone()}),
            "todo 앱 html css js 만들어줘",
        );
        assert!(!fixes.iter().any(|fix| fix.starts_with("manifest")));
        assert_eq!(normalized["steps"], steps);
    }

    #[test]
    fn an_empty_plan_for_a_single_file_request_gets_one_heuristic_step() {
        let (normalized, fixes) = normalize(json!({}), "html 파일 만들어줘");
        assert!(fixes.contains(&"heuristic_file_step".to_string()));
        assert_eq!(
            normalized["steps"],
            json!([{
                "action": "write_file",
                "args": {"path": "generated_page.html"},
                "description": "Create generated_page.html for: html 파일 만들어줘",
            }])
        );
    }

    #[test]
    fn the_heuristic_description_cuts_the_request_at_120_characters() {
        let message = format!("html 파일 만들어줘 {}", "가".repeat(200));
        let (normalized, _) = normalize(json!({}), &message);
        let description = normalized["steps"][0]["description"]
            .as_str()
            .expect("description");
        let tail = description.trim_start_matches("Create generated_page.html for: ");
        assert_eq!(tail.chars().count(), 120);
    }

    #[test]
    fn estimated_steps_coerces_the_way_int_does() {
        for (raw, expected) in [
            (json!(4), 4),
            (json!("4"), 4),
            (json!(3.7), 3),
            (json!(true), 1),
            (json!(null), 1),
            (json!(0), 1),
            (json!(-5), 1),
        ] {
            let (normalized, fixes) = normalize(json!({"goal": "g", "estimated_steps": raw}), "g");
            assert_eq!(normalized["estimated_steps"], expected);
            assert!(!fixes.contains(&"estimated_steps_invalid".to_string()));
        }
        for junk in [json!("many"), json!([3]), json!({"n": 3}), json!("3.5")] {
            let (normalized, fixes) = normalize(json!({"goal": "g", "estimated_steps": junk}), "g");
            assert!(fixes.contains(&"estimated_steps_invalid".to_string()));
            assert_eq!(normalized["estimated_steps"], 1);
        }
    }

    #[test]
    fn the_two_trailing_fields_are_coerced_not_validated() {
        let (normalized, _) = normalize(
            json!({"goal": "g", "requires_approval": "yes", "rollback_strategy": 7}),
            "g",
        );
        assert_eq!(normalized["requires_approval"], true);
        assert_eq!(normalized["rollback_strategy"], "none");
        let (normalized, _) = normalize(
            json!({"goal": "g", "requires_approval": [], "rollback_strategy": "git"}),
            "g",
        );
        assert_eq!(normalized["requires_approval"], false);
        assert_eq!(normalized["rollback_strategy"], "git");
    }
}
