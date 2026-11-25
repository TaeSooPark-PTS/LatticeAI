use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use crate::kernel::profile::{
    profile_for_model, profile_named, AgentProfile, PROFILE_OVERRIDE_ENV,
};
use crate::parse::action::extract_action_details;
use crate::seam::worker::{Completion, WorkerClient};

pub const PROBE_ENV: &str = "LATTICEAI_AGENT_PROBE";

pub const CACHE_FILE: &str = "agent_profile_probe.json";

const ACTION_TOKENS: u32 = 160;

const MENU_TOKENS: u32 = 8;

const GROUNDING_TOKENS: u32 = 32;

const REASONING_MARKERS: &[&str] = &[
    "<think>",
    "<thinking>",
    "<reasoning>",
    "<|channel|>",
    "<|channel>",
    "thinking process:",
];

const GROUNDING_PROBE_CONTEXT: &str = concat!(
    "Read these two facts and answer with ONLY the current port number:\n",
    "- [SUPERSEDED - do not use] service_port = 4040\n",
    "- [ACTIVE] service_port = 8443"
);

const GROUNDING_PROBE_MESSAGE: &str = "What is the current service_port?";
const GROUNDING_ACTIVE_VALUE: &str = "8443";
const GROUNDING_SUPERSEDED_VALUE: &str = "4040";

const ACTION_PROBE_CONTEXT: &str = concat!(
    "Reply with EXACTLY ONE JSON object and nothing else — no prose, no markdown fence.\n\n",
    "The object is: {\"thoughts\": \"<one short line>\", \"action\": \"<name>\", ",
    "\"args\": {<that action's arguments>}}\n\n",
    "Task: create the file probe.txt containing these two lines:\n",
    "hello\n",
    "she said \"hi\"\n\n",
    "Use action write_file with args.path and args.content. ",
    "Escape newlines as \\n and quotes as \\\"."
);

const ACTION_PROBE_MESSAGE: &str = "Emit the write_file action for this task.";

const MENU_PROBE_CONTEXT: &str = concat!(
    "Choose one option and reply with ONLY its number. No words.\n\n",
    "1. read a file\n",
    "2. write a file\n",
    "3. stop and finish\n\n",
    "Which option creates a new file?"
);

const MENU_PROBE_MESSAGE: &str = "Answer with one number.";

const MENU_ANSWER: usize = 2;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProbeConfig {
    pub cache_dir: PathBuf,
}

impl ProbeConfig {
    pub fn from_env() -> Self {
        let configured = std::env::var("LATTICEAI_DATA_DIR").unwrap_or_default();
        let base = if configured.trim().is_empty() {
            std::env::var_os("HOME")
                .map(PathBuf::from)
                .filter(|home| !home.as_os_str().is_empty())
                .map_or_else(|| PathBuf::from(".ltcai"), |home| home.join(".ltcai"))
        } else {
            PathBuf::from(configured.trim())
        };
        Self { cache_dir: base }
    }

    pub fn new(cache_dir: impl Into<PathBuf>) -> Self {
        Self {
            cache_dir: cache_dir.into(),
        }
    }

    pub fn cache_path(&self) -> PathBuf {
        self.cache_dir.join(CACHE_FILE)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ActionScore {
    Clean,
    Repaired,
    Failed,
}

impl ActionScore {
    fn as_str(self) -> &'static str {
        match self {
            ActionScore::Clean => "clean",
            ActionScore::Repaired => "repaired",
            ActionScore::Failed => "failed",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProbeVerdict {
    pub profile: AgentProfile,
    pub action: ActionScore,
    pub menu_ok: bool,
    pub reasoning_framed: bool,
    pub grounding_ok: bool,
    pub source: &'static str,
}

impl ProbeVerdict {
    fn to_value(&self, harness: &str) -> Value {
        json!({
            "profile": self.profile.name,
            "action": self.action.as_str(),
            "menu_ok": self.menu_ok,
            "reasoning_framed": self.reasoning_framed,
            "grounding_ok": self.grounding_ok,
            "harness": harness,
        })
    }

    pub fn to_detail(&self) -> Value {
        json!({
            "profile": self.profile.name,
            "action": self.action.as_str(),
            "menu_ok": self.menu_ok,
            "reasoning_framed": self.reasoning_framed,
            "grounding_ok": self.grounding_ok,
            "source": self.source,
        })
    }
}

pub fn score_action(reply: &str) -> ActionScore {
    match extract_action_details(reply) {
        Ok((action, repairs)) => {
            if action.get("action").and_then(Value::as_str) != Some("write_file") {
                return ActionScore::Failed;
            }
            let args = action
                .get("args")
                .or_else(|| action.get("params"))
                .filter(|v| v.is_object());
            let path = args
                .and_then(|a| a.get("path").or_else(|| a.get("file_path")))
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim();
            let content = args
                .and_then(|a| a.get("content"))
                .and_then(Value::as_str)
                .unwrap_or("");
            if !path.ends_with("probe.txt") || content.trim().is_empty() {
                return ActionScore::Failed;
            }
            let full_content_ok = content.contains("hello")
                && content.contains('\n')
                && content.contains("she said \"hi\"");
            if repairs.is_empty() && full_content_ok {
                ActionScore::Clean
            } else {
                ActionScore::Repaired
            }
        }
        Err(_) => ActionScore::Failed,
    }
}

pub fn detect_reasoning_framed(reply: &str) -> bool {
    let lower = reply.to_ascii_lowercase();
    REASONING_MARKERS
        .iter()
        .any(|marker| lower.contains(marker))
}

pub fn score_grounding(reply: &str) -> bool {
    reply.contains(GROUNDING_ACTIVE_VALUE) && !reply.contains(GROUNDING_SUPERSEDED_VALUE)
}

pub fn verdict_for(action: ActionScore, menu_ok: bool) -> ProbeVerdict {
    let profile = match action {
        ActionScore::Clean => crate::kernel::profile::STANDARD,
        ActionScore::Repaired => crate::kernel::profile::COMPACT,
        ActionScore::Failed => crate::kernel::profile::GUIDED,
    };
    ProbeVerdict {
        profile,
        action,
        menu_ok,
        reasoning_framed: false,
        grounding_ok: false,
        source: "probe",
    }
}

pub fn env_override() -> Option<AgentProfile> {
    profile_named(&std::env::var(PROFILE_OVERRIDE_ENV).unwrap_or_default())
}

pub fn probing_enabled() -> bool {
    !matches!(
        std::env::var(PROBE_ENV)
            .unwrap_or_default()
            .trim()
            .to_lowercase()
            .as_str(),
        "0" | "false" | "off" | "no"
    )
}

fn cache_key(model_id: &str, harness: &str) -> String {
    format!("{harness}|{model_id}")
}

pub fn cached(config: &ProbeConfig, model_id: &str, harness: &str) -> Option<ProbeVerdict> {
    let text = std::fs::read_to_string(config.cache_path()).ok()?;
    let document: Value = serde_json::from_str(&text).ok()?;
    let entry = document
        .get("verdicts")?
        .get(cache_key(model_id, harness))?;
    let profile = profile_named(entry.get("profile")?.as_str()?)?;
    Some(ProbeVerdict {
        profile,
        action: match entry.get("action").and_then(Value::as_str) {
            Some("clean") => ActionScore::Clean,
            Some("repaired") => ActionScore::Repaired,
            _ => ActionScore::Failed,
        },
        menu_ok: entry
            .get("menu_ok")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        reasoning_framed: entry
            .get("reasoning_framed")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        grounding_ok: entry
            .get("grounding_ok")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        source: "cache",
    })
}

pub fn store(config: &ProbeConfig, model_id: &str, harness: &str, verdict: &ProbeVerdict) {
    let path = config.cache_path();
    let mut document = std::fs::read_to_string(&path)
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .filter(Value::is_object)
        .unwrap_or_else(|| json!({"verdicts": {}}));
    if !document
        .get("verdicts")
        .is_some_and(serde_json::Value::is_object)
    {
        document["verdicts"] = json!({});
    }
    document["verdicts"][cache_key(model_id, harness)] = verdict.to_value(harness);
    let Ok(text) = serde_json::to_string_pretty(&document) else {
        return;
    };
    let _ = atomic_write(&path, &format!("{text}\n"));
}

fn atomic_write(path: &Path, text: &str) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut temp = path.as_os_str().to_os_string();
    temp.push(".tmp");
    let temp = PathBuf::from(temp);
    if let Err(error) = std::fs::write(&temp, text) {
        let _ = std::fs::remove_file(&temp);
        return Err(error);
    }
    if let Err(error) = std::fs::rename(&temp, path) {
        let _ = std::fs::remove_file(&temp);
        return Err(error);
    }
    Ok(())
}

pub async fn measure(
    worker: &WorkerClient,
    config: &ProbeConfig,
    model_id: Option<&str>,
    harness: &str,
) -> Option<ProbeVerdict> {
    let model_id = model_id.map(str::trim).filter(|id| !id.is_empty())?;
    if !probing_enabled() {
        return None;
    }
    if let Some(hit) = cached(config, model_id, harness) {
        return Some(hit);
    }
    let action_reply = worker
        .llm(Completion {
            model_id: Some(model_id),
            message: ACTION_PROBE_MESSAGE,
            context: ACTION_PROBE_CONTEXT,
            max_tokens: ACTION_TOKENS,
            temperature: 0.0,
            stop: &[],
            prefix: "",
        })
        .await
        .ok()?;
    if action_reply.trim().is_empty() || action_reply.trim() == "No model." {
        return None;
    }
    let action = score_action(&action_reply);
    let reasoning_framed = detect_reasoning_framed(&action_reply);
    let menu_reply = worker
        .llm(Completion {
            model_id: Some(model_id),
            message: MENU_PROBE_MESSAGE,
            context: MENU_PROBE_CONTEXT,
            max_tokens: MENU_TOKENS,
            temperature: 0.0,
            stop: &["\n"],
            prefix: crate::prompts::guided::MENU_ANSWER_PREFIX,
        })
        .await
        .unwrap_or_default();
    let menu_ok =
        crate::kernel::agentloop::guided::parse_choice(&menu_reply, 3) == Some(MENU_ANSWER);
    let grounding_reply = worker
        .llm(Completion {
            model_id: Some(model_id),
            message: GROUNDING_PROBE_MESSAGE,
            context: GROUNDING_PROBE_CONTEXT,
            max_tokens: GROUNDING_TOKENS,
            temperature: 0.0,
            stop: &[],
            prefix: "",
        })
        .await
        .unwrap_or_default();
    let grounding_ok = score_grounding(&grounding_reply);
    let mut verdict = verdict_for(action, menu_ok);
    verdict.reasoning_framed = reasoning_framed;
    verdict.grounding_ok = grounding_ok;
    store(config, model_id, harness, &verdict);
    Some(verdict)
}

pub fn fallback_profile(model_id: Option<&str>) -> AgentProfile {
    profile_for_model(model_id)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernel::profile::{COMPACT, GUIDED, STANDARD};

    #[test]
    fn the_menu_turn_is_asked_with_the_loops_own_instrument() {
        assert_eq!(MENU_TOKENS, crate::kernel::agentloop::guided::MENU_TOKENS);
        let prefix = crate::prompts::guided::MENU_ANSWER_PREFIX;
        assert!(!prefix.chars().any(|character| character.is_ascii_digit()));
        assert_eq!(
            crate::kernel::agentloop::guided::parse_choice(prefix, 3),
            None
        );
        assert_eq!(
            crate::kernel::agentloop::guided::parse_choice(&format!("{prefix}{MENU_ANSWER}"), 3),
            Some(MENU_ANSWER)
        );
    }

    #[test]
    fn the_action_turn_is_scored_by_the_loops_own_parser() {
        assert_eq!(
            score_action(
                r#"{"thoughts": "write it", "action": "write_file", "args": {"path": "probe.txt", "content": "hello\nshe said \"hi\"\n"}}"#
            ),
            ActionScore::Clean
        );
        assert_eq!(
            score_action(
                "Sure! Here you go:\n```json\n{\"thoughts\": \"w\", \"action\": \"write_file\", \"args\": {\"path\": \"probe.txt\", \"content\": \"hello\"}}\n```"
            ),
            ActionScore::Repaired
        );
        assert_eq!(
            score_action("I would create probe.txt with the two lines you gave."),
            ActionScore::Failed
        );
        assert_eq!(
            score_action(r#"{"action": "final", "message": "done"}"#),
            ActionScore::Failed
        );
        assert_eq!(
            score_action(
                r#"{"thoughts": "w", "action": "write_file", "args": {"path": "probe.txt", "content": "hello"}}"#
            ),
            ActionScore::Repaired
        );
        assert_eq!(
            score_action(r#"{"thoughts": "w", "action": "write_file", "args": {}}"#),
            ActionScore::Failed
        );
        assert_eq!(score_action(""), ActionScore::Failed);
    }

    #[test]
    fn the_verdict_table_is_the_one_the_doc_prints() {
        for menu_ok in [true, false] {
            assert_eq!(verdict_for(ActionScore::Clean, menu_ok).profile, STANDARD);
            assert_eq!(verdict_for(ActionScore::Repaired, menu_ok).profile, COMPACT);
            assert_eq!(verdict_for(ActionScore::Failed, menu_ok).profile, GUIDED);
        }
        let verdict = verdict_for(ActionScore::Failed, true);
        assert_eq!(verdict.source, "probe");
        assert_eq!(verdict.to_detail()["profile"], json!("guided"));
        assert_eq!(verdict.to_detail()["menu_ok"], json!(true));
    }

    #[test]
    fn a_verdict_round_trips_through_the_cache_and_a_version_bump_misses() {
        let dir = tempfile::tempdir().expect("tempdir");
        let config = ProbeConfig::new(dir.path().join("data"));
        assert_eq!(cached(&config, "some/model", "12.0.0"), None);

        let verdict = verdict_for(ActionScore::Repaired, true);
        store(&config, "some/model", "12.0.0", &verdict);
        let hit = cached(&config, "some/model", "12.0.0").expect("cached");
        assert_eq!(hit.profile, COMPACT);
        assert_eq!(hit.action, ActionScore::Repaired);
        assert!(hit.menu_ok);
        assert_eq!(hit.source, "cache", "a read says where it came from");

        assert_eq!(cached(&config, "some/model", "12.1.0"), None);
        assert_eq!(cached(&config, "other/model", "12.0.0"), None);

        store(
            &config,
            "other/model",
            "12.0.0",
            &verdict_for(ActionScore::Failed, false),
        );
        assert_eq!(
            cached(&config, "some/model", "12.0.0")
                .expect("kept")
                .profile,
            COMPACT
        );
        assert_eq!(
            cached(&config, "other/model", "12.0.0")
                .expect("stored")
                .profile,
            GUIDED
        );
    }

    #[test]
    fn a_corrupt_cache_is_a_miss_and_is_then_overwritten() {
        let dir = tempfile::tempdir().expect("tempdir");
        let config = ProbeConfig::new(dir.path());
        std::fs::write(config.cache_path(), b"not json at all").expect("write");
        assert_eq!(cached(&config, "m", "12.0.0"), None);
        store(
            &config,
            "m",
            "12.0.0",
            &verdict_for(ActionScore::Clean, true),
        );
        assert_eq!(
            cached(&config, "m", "12.0.0").expect("recovered").profile,
            STANDARD
        );
    }

    #[tokio::test]
    async fn an_unreachable_worker_takes_no_measurement_at_all() {
        let dir = tempfile::tempdir().expect("tempdir");
        let config = ProbeConfig::new(dir.path());
        let worker = WorkerClient::new("http://127.0.0.1:1");
        assert_eq!(measure(&worker, &config, Some("m"), "12.0.0").await, None);
        assert!(
            !config.cache_path().exists(),
            "a failed measurement must not be cached as a verdict"
        );
        assert_eq!(measure(&worker, &config, None, "12.0.0").await, None);
        assert_eq!(measure(&worker, &config, Some("  "), "12.0.0").await, None);
    }

    #[test]
    fn the_fallback_is_the_size_prior() {
        assert_eq!(fallback_profile(Some("llama-3.3-70b")).name, "standard");
        assert_eq!(
            fallback_profile(Some("gemma-4-e2b-it-4bit")).name,
            "compact"
        );
        assert_eq!(fallback_profile(Some("qwen2.5-0.5b")).name, "guided");
    }

    #[test]
    fn detects_reasoning_framing_and_scores_grounding_probe() {
        assert!(!detect_reasoning_framed(r#"{"action":"write_file"}"#));
        for marker in [
            "<think>plan</think>",
            "<thinking>plan</thinking>",
            "<reasoning>plan</reasoning>",
            "<|channel|>thought",
            "<|channel>thought",
            "Thinking Process:\n1. step",
        ] {
            assert!(detect_reasoning_framed(marker), "missing {marker}");
        }

        assert!(score_grounding("8443"));
        assert!(score_grounding("The current service_port is 8443."));
        assert!(!score_grounding("4040"));
        assert!(!score_grounding("8443 (previously 4040)"));
        assert!(!score_grounding(""));
    }

    #[test]
    fn older_cache_entries_without_new_fields_still_deserialize() {
        let dir = tempfile::tempdir().expect("tempdir");
        let config = ProbeConfig::new(dir.path());
        std::fs::write(
            config.cache_path(),
            r#"{"verdicts":{"12.0.0|legacy/model":{"profile":"compact","action":"repaired","menu_ok":true,"harness":"12.0.0"}}}"#,
        )
        .expect("write");
        let hit = cached(&config, "legacy/model", "12.0.0").expect("legacy hit");
        assert_eq!(hit.profile, COMPACT);
        assert!(hit.menu_ok);
        assert!(!hit.reasoning_framed);
        assert!(!hit.grounding_ok);

        let mut full = verdict_for(ActionScore::Clean, true);
        full.reasoning_framed = true;
        full.grounding_ok = true;
        assert_eq!(full.to_detail()["reasoning_framed"], json!(true));
        assert_eq!(full.to_detail()["grounding_ok"], json!(true));
        store(&config, "new/model", "12.0.0", &full);
        let round = cached(&config, "new/model", "12.0.0").expect("round trip");
        assert!(round.reasoning_framed);
        assert!(round.grounding_ok);
    }
}
