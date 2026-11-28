use std::collections::BTreeSet;
use std::future::Future;
use std::pin::Pin;

use lattice_core::worker::WorkerSeamClient;
use serde_json::{json, Value};

use super::self_model_write::{fact_id, node_type_for, normalize};

pub const SELF_MODEL_LLM_REFINER_ENV: &str = "LATTICEAI_SELF_MODEL_LLM_REFINER";
const MAX_REFINED_CANDIDATES: usize = 5;

pub fn refiner_enabled_from(raw: Option<&str>) -> bool {
    raw.is_some_and(|v| {
        matches!(
            v.trim().to_ascii_lowercase().as_str(),
            "1" | "true" | "yes" | "on"
        )
    })
}

pub fn refiner_enabled() -> bool {
    let raw = std::env::var(SELF_MODEL_LLM_REFINER_ENV).ok();
    refiner_enabled_from(raw.as_deref())
}

pub fn build_refiner_prompt(text: &str, deterministic: &[Value]) -> String {
    let existing: Vec<String> = deterministic
        .iter()
        .filter_map(|item| {
            let kind = item.get("kind").and_then(Value::as_str)?;
            let fact = item.get("text").and_then(Value::as_str)?;
            Some(format!("- [{kind}] {fact}"))
        })
        .collect();
    let known_block = if existing.is_empty() {
        "(none)".to_string()
    } else {
        existing.join("\n")
    };
    format!(
        "Extract implicit long-term user profile facts (traits, preferences, habits, decisions, relationships) \
that were missed by rule-based extraction.\n\n\
Allowed kinds: trait, preference, habit, decision, relationship.\n\
Already extracted:\n{known_block}\n\n\
Input text:\n{}\n\n\
Return ONLY a JSON array of up to {MAX_REFINED_CANDIDATES} new items like \
[{{\"kind\":\"preference\",\"text\":\"...\",\"confidence\":0.75}}]. Return [] if none.",
        text.trim()
    )
}

fn strip_wrappers(raw: &str) -> String {
    let mut text = raw.trim().to_string();
    while let Some(start) = text.find("<think>") {
        let Some(end_rel) = text[start..].find("</think>") else {
            text.truncate(start);
            break;
        };
        let end = start + end_rel + "</think>".len();
        text.replace_range(start..end, " ");
    }
    let trimmed = text.trim();
    if let Some(after_fence) = trimmed.strip_prefix("```") {
        let without_lang = after_fence
            .split_once('\n')
            .map(|(_, rest)| rest)
            .unwrap_or(after_fence);
        if let Some(before_end) = without_lang.rsplit_once("```") {
            return before_end.0.trim().to_string();
        }
        return without_lang.trim().to_string();
    }
    trimmed.to_string()
}

fn slice_json_array(text: &str) -> Option<&str> {
    let start = text.find('[')?;
    let end = text.rfind(']')?;
    (end >= start).then_some(&text[start..=end])
}

pub fn parse_refined_candidates(raw: &str, source: &str, deterministic: &[Value]) -> Vec<Value> {
    let cleaned = strip_wrappers(raw);
    let Some(slice) = slice_json_array(&cleaned) else {
        return Vec::new();
    };
    let Ok(Value::Array(items)) = serde_json::from_str::<Value>(slice) else {
        return Vec::new();
    };
    let mut seen: BTreeSet<String> = deterministic
        .iter()
        .filter_map(|item| item.get("id").and_then(Value::as_str).map(str::to_string))
        .collect();
    let mut refined = Vec::new();
    for item in items {
        if refined.len() >= MAX_REFINED_CANDIDATES {
            break;
        }
        let kind = item
            .get("kind")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase();
        if node_type_for(&kind).is_none() {
            continue;
        }
        let text = normalize(item.get("text").and_then(Value::as_str).unwrap_or_default());
        if text.chars().count() < 2 {
            continue;
        }
        let id = fact_id(&kind, &text);
        if !seen.insert(id.clone()) {
            continue;
        }
        let confidence = item
            .get("confidence")
            .and_then(Value::as_f64)
            .unwrap_or(0.7)
            .clamp(0.5, 0.95);
        refined.push(json!({
            "id": id,
            "kind": kind,
            "text": text,
            "confidence": confidence,
            "signal": "llm_refiner",
            "origin": "llm_refiner",
            "source": source,
        }));
    }
    refined
}

pub type CompletionFn<'a> = &'a mut (dyn FnMut(String) -> Pin<Box<dyn Future<Output = Result<String, String>> + Send>>
             + Send
             + Sync);

pub async fn collect_candidates_with(
    text: &str,
    source: &str,
    enabled: bool,
    complete: Option<CompletionFn<'_>>,
) -> (Vec<Value>, bool) {
    let mut candidates = super::self_model_write::extract(text, source);
    if !enabled || text.trim().is_empty() {
        return (candidates, false);
    }
    let Some(complete) = complete else {
        return (candidates, false);
    };
    let prompt = build_refiner_prompt(text, &candidates);
    let Ok(raw) = complete(prompt).await else {
        return (candidates, false);
    };
    let extra = parse_refined_candidates(&raw, source, &candidates);
    let used = !extra.is_empty();
    candidates.extend(extra);
    (candidates, used)
}

async fn request_worker_llm(seam: &WorkerSeamClient, prompt: String) -> Result<String, String> {
    let body = json!({
        "message": "Extract missed user profile facts as a strict JSON array.",
        "context": prompt,
        "max_tokens": 384,
        "temperature": 0.0,
        "mode": "chat",
    });
    let stream = seam
        .stream_sse("/worker/llm/stream", &body)
        .await
        .map_err(|err| err.to_string())?;
    let mut response = stream.into_response();
    let mut collected = String::new();
    let mut buffer = String::new();
    while let Some(chunk) = response.chunk().await.map_err(|err| err.to_string())? {
        buffer.push_str(&String::from_utf8_lossy(&chunk));
        while let Some(pos) = buffer.find('\n') {
            let line = buffer[..pos].trim().to_string();
            buffer.drain(..=pos);
            let Some(payload) = line.strip_prefix("data:") else {
                continue;
            };
            let payload = payload.trim();
            if payload == "[DONE]" || payload.is_empty() {
                continue;
            }
            if let Ok(val) = serde_json::from_str::<Value>(payload) {
                if let Some(piece) = val.get("text").and_then(Value::as_str) {
                    collected.push_str(piece);
                }
            }
        }
    }
    Ok(collected)
}

pub async fn collect_candidates(
    seam: Option<&WorkerSeamClient>,
    text: &str,
    source: &str,
    enabled: bool,
) -> (Vec<Value>, bool) {
    match seam {
        Some(client) if enabled && !text.trim().is_empty() => {
            let owned = client.clone();
            let mut caller =
                |prompt: String| -> Pin<Box<dyn Future<Output = Result<String, String>> + Send>> {
                    let c = owned.clone();
                    Box::pin(async move { request_worker_llm(&c, prompt).await })
                };
            collect_candidates_with(text, source, true, Some(&mut caller)).await
        }
        _ => collect_candidates_with(text, source, false, None).await,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn refiner_flag_defaults_off_and_accepts_truthy_values() {
        assert!(!refiner_enabled_from(None));
        assert!(!refiner_enabled_from(Some("")));
        assert!(!refiner_enabled_from(Some("0")));
        assert!(refiner_enabled_from(Some("1")));
        assert!(refiner_enabled_from(Some("true")));
    }

    #[test]
    fn parse_refined_candidates_strips_think_and_fences_filters_invalid_and_dedups() {
        let deterministic =
            super::super::self_model_write::extract("I prefer concise answers.", "chat");
        let raw = "<think>analyzing</think>\n```json\n[\
            {\"kind\": \"preference\", \"text\": \"concise answers\", \"confidence\": 0.9},\
            {\"kind\": \"habit\", \"text\": \"writes RFCs before coding\", \"confidence\": 0.8},\
            {\"kind\": \"bogus_kind\", \"text\": \"ignored\", \"confidence\": 0.9},\
            {\"kind\": \"decision\", \"text\": \"use SQLite WAL locally\", \"confidence\": 1.5}\
        ]\n```";
        let refined = parse_refined_candidates(raw, "chat", &deterministic);
        assert_eq!(refined.len(), 2);
        assert_eq!(refined[0]["kind"], json!("habit"));
        assert_eq!(refined[0]["origin"], json!("llm_refiner"));
        assert_eq!(refined[1]["kind"], json!("decision"));
        assert_eq!(refined[1]["confidence"], json!(0.95));
    }

    #[tokio::test]
    async fn collect_candidates_with_handles_off_valid_and_malformed_llm_outputs() {
        let text = "I prefer dark mode. Actually I always review diffs before committing.";
        let (off, used_off) = collect_candidates_with(text, "chat", false, None).await;
        assert!(!used_off);
        assert!(off.iter().any(|c| c["kind"] == "preference"));

        let mut ok_fn =
            |_prompt: String| -> Pin<Box<dyn Future<Output = Result<String, String>> + Send>> {
                Box::pin(async {
                    Ok("[{\"kind\":\"decision\",\"text\":\"keep local-first architecture\",\"confidence\":0.85}]".to_string())
                })
            };
        let (refined, used_on) =
            collect_candidates_with(text, "chat", true, Some(&mut ok_fn)).await;
        assert!(used_on);
        assert!(refined
            .iter()
            .any(|c| c["text"] == "keep local-first architecture" && c["origin"] == "llm_refiner"));

        let mut bad_fn =
            |_prompt: String| -> Pin<Box<dyn Future<Output = Result<String, String>> + Send>> {
                Box::pin(async { Ok("not valid json at all".to_string()) })
            };
        let (fallback, used_bad) =
            collect_candidates_with(text, "chat", true, Some(&mut bad_fn)).await;
        assert!(!used_bad);
        assert_eq!(fallback.len(), off.len());
    }
}
