//! **Measuring** the model instead of guessing at its name (v12.0.0).
//!
//! [`crate::kernel::profile`] chose a loop dial with a regex over the model id.
//! That is a guess dressed as a rule, and it was wrong in both directions: an
//! id that names no size (`gpt-oss`, `mystery-model`, every fine-tune anybody
//! publishes) got `standard` however small it was, and a 26B mixture of experts
//! whose id happens to say `a4b` nearly got the tiny-model loop. Worse, size is
//! only a proxy for the thing that actually matters — *can this model emit a
//! tool call we can parse* — and that varies between two models of the same
//! size, between quantizations of one model, and between chat templates.
//!
//! So the loop asks. Two completions, both tiny, both fixed:
//!
//! 1. **the action turn** — a toy write task whose content contains a newline
//!    and a quote, i.e. the two characters that break weak models inside a JSON
//!    string. Scored by feeding the reply to the loop's *own*
//!    [`crate::parse::action::extract_action_details`]: clean, repaired, or not
//!    an action at all.
//! 2. **the menu turn** — three numbered options and a question with one right
//!    answer. Scored by [`crate::kernel::agentloop::guided`]'s own choice
//!    parser. This measures the only thing `guided` needs a model to do.
//!
//! The verdict maps straight onto the dials: a reply the parser took without
//! repair is `standard`; one it had to repair is `compact`; anything else is
//! `guided`, and the menu answer decides whether we say so with confidence or
//! merely as the safest remaining option.
//!
//! ## What this module refuses to do
//!
//! * **Name a model.** There is no list of ids anywhere here and there must
//!   never be. The whole point is that a model published tomorrow is measured
//!   the same way as one published last year.
//! * **Spend a run's budget twice.** A verdict is cached on disk under the
//!   model id *and the harness version*, so a probe is paid once per model per
//!   release. Bumping the crate version re-probes, because a changed prompt or
//!   parser can change the answer.
//! * **Fail a run.** Every failure — unreachable worker, unreadable cache,
//!   unwritable directory — falls back to [`profile_for_model`]'s prior. A
//!   measurement that cannot be taken is not an error; it is a guess, and the
//!   guess is what we had before.

use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use crate::kernel::profile::{
    profile_for_model, profile_named, AgentProfile, PROFILE_OVERRIDE_ENV,
};
use crate::parse::action::extract_action_details;
use crate::surface::worker::{Completion, WorkerClient};

/// Switch the probe off without pinning a dial: `0` / `false` / `off`.
pub const PROBE_ENV: &str = "LATTICEAI_AGENT_PROBE";

/// The cache file, under the data directory.
pub const CACHE_FILE: &str = "agent_profile_probe.json";

/// Tokens the action turn may spend. The expected reply is ~90 characters; the
/// ceiling exists so a model that starts narrating is cut off rather than
/// billed for a paragraph.
const ACTION_TOKENS: u32 = 160;

/// Tokens the menu turn may spend. One digit.
const MENU_TOKENS: u32 = 8;

/// The toy task. Deliberately contains the two characters that break a weak
/// model inside a JSON string — a newline and a double quote — because a probe
/// that only asks for `{"action": "final"}` measures nothing a real step needs.
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

/// Three options, one right answer, no ambiguity — the guided menu in
/// miniature. The answer is 2.
const MENU_PROBE_CONTEXT: &str = concat!(
    "Choose one option and reply with ONLY its number. No words.\n\n",
    "1. read a file\n",
    "2. write a file\n",
    "3. stop and finish\n\n",
    "Which option creates a new file?"
);

const MENU_PROBE_MESSAGE: &str = "Answer with one number.";

/// The right answer to [`MENU_PROBE_CONTEXT`].
const MENU_ANSWER: usize = 2;

/// Where a probe verdict is cached, and whether probing may run at all.
///
/// Constructed by the caller, never from the environment inside the kernel: a
/// decision module that reads `$HOME` is a decision module that behaves
/// differently in a test than in production, which is the one thing the
/// kernel's invariants forbid. [`ProbeConfig::from_env`] exists for the surface
/// to call.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProbeConfig {
    /// Directory the verdict cache lives in.
    pub cache_dir: PathBuf,
}

impl ProbeConfig {
    /// `LATTICEAI_DATA_DIR`, or `$HOME/.ltcai` — [`crate::kernel::runs::default_runs_dir`]'s rule,
    /// so a run's paused-run records, its staged proposals and its probe
    /// verdicts all land in one place an operator can point somewhere else.
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

    /// A config over an explicit directory.
    pub fn new(cache_dir: impl Into<PathBuf>) -> Self {
        Self {
            cache_dir: cache_dir.into(),
        }
    }

    fn cache_path(&self) -> PathBuf {
        self.cache_dir.join(CACHE_FILE)
    }
}

/// How the action turn came back.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ActionScore {
    /// One object, parsed with no repair — the contract held.
    Clean,
    /// An object the repair chain had to rescue — the contract nearly held.
    Repaired,
    /// Not an action at all.
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

/// What one probe measured.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProbeVerdict {
    pub profile: AgentProfile,
    pub action: ActionScore,
    /// Whether the menu turn picked the right number.
    pub menu_ok: bool,
    /// Where the verdict came from, for the trace: `probe` or `cache`.
    pub source: &'static str,
}

impl ProbeVerdict {
    /// The stored form. Only the profile name is authoritative on read — the
    /// scores are kept so an operator can see *why* a model was demoted.
    fn to_value(&self, harness: &str) -> Value {
        json!({
            "profile": self.profile.name,
            "action": self.action.as_str(),
            "menu_ok": self.menu_ok,
            "harness": harness,
        })
    }

    /// The trace/telemetry shape.
    pub fn to_detail(&self) -> Value {
        json!({
            "profile": self.profile.name,
            "action": self.action.as_str(),
            "menu_ok": self.menu_ok,
            "source": self.source,
        })
    }
}

/// Score one action reply with the loop's own parser.
pub fn score_action(reply: &str) -> ActionScore {
    match extract_action_details(reply) {
        Ok((action, repairs)) => {
            // A parsed object that is not the action asked for is not a model
            // holding the contract; it is a model that guessed a shape.
            if action.get("action").and_then(Value::as_str) != Some("write_file") {
                return ActionScore::Failed;
            }
            if repairs.is_empty() {
                ActionScore::Clean
            } else {
                ActionScore::Repaired
            }
        }
        Err(_) => ActionScore::Failed,
    }
}

/// The dial two scores earn.
///
/// Stated as a table because it is a policy, not a formula:
///
/// | action reply | menu | dial       |
/// |--------------|------|------------|
/// | clean        | any  | `standard` |
/// | repaired     | any  | `compact`  |
/// | failed       | any  | `guided`   |
///
/// The menu answer deliberately does **not** move the dial. It measures the
/// floor — whether `guided` is even viable — and a model that fails it has no
/// better option than `guided` anyway, so letting it change the choice could
/// only ever promote a model that had already failed the action turn.
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
        source: "probe",
    }
}

/// The environment's answer, when it has one.
///
/// `LATTICEAI_AGENT_PROFILE` pins a dial and outranks everything; `false` /
/// `0` / `off` in [`PROBE_ENV`] switches measuring off without pinning, which
/// is the "just use the prior" setting.
pub fn env_override() -> Option<AgentProfile> {
    profile_named(&std::env::var(PROFILE_OVERRIDE_ENV).unwrap_or_default())
}

/// Whether probing is switched on in this process.
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

/// The cache key: the model, and the harness that judged it.
fn cache_key(model_id: &str, harness: &str) -> String {
    format!("{harness}|{model_id}")
}

/// Read a cached verdict for this model and harness.
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
        source: "cache",
    })
}

/// Store a verdict. Best effort: a cache that cannot be written costs a probe
/// next run, and that is never worth failing a run over.
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

/// Temp file + rename, as [`crate::kernel::proposals`]' store does.
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

/// Measure `model_id`, or read the answer we already have.
///
/// `harness` is this crate's version, so a release that changes the prompts or
/// the parser re-measures rather than trusting a verdict about different code.
///
/// Returns `None` when no measurement was possible at all — no model id,
/// probing switched off, or a worker that could not answer. The caller then
/// falls back to [`profile_for_model`], which is exactly the guess this module
/// replaces when it *can* run.
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
    // A model that is not loaded answers `generate_as`'s own "No model." — that
    // is an absent measurement, not a failed one, and recording `guided` for it
    // would poison the cache for a model nobody has run yet.
    if action_reply.trim().is_empty() || action_reply.trim() == "No model." {
        return None;
    }
    let action = score_action(&action_reply);
    // **Asked the way the loop asks it** (v12.0.0). The menu turn is scored
    // with the loop's own parser, and it must be *sent* with the loop's own
    // instrument for the same reason: a probe that omits
    // `MENU_ANSWER_PREFIX` measures a question this harness no longer puts to
    // any model, and records `menu_ok: false` for a model that answers the real
    // menu perfectly well. The forcing prefix carries no digit, so it cannot
    // turn a non-answer into the right one.
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
    let verdict = verdict_for(action, menu_ok);
    store(config, model_id, harness, &verdict);
    Some(verdict)
}

/// The dial to use when nothing could be measured.
pub fn fallback_profile(model_id: Option<&str>) -> AgentProfile {
    profile_for_model(model_id)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernel::profile::{COMPACT, GUIDED, STANDARD};

    /// The probe must ask the way the loop asks (v12.0.0). Scoring with the
    /// loop's parser while sending a question the loop no longer sends measures
    /// a harness that does not exist: four live runs on a reasoning-tuned model
    /// were recorded `menu_ok: false` against an unprefixed eight-token turn the
    /// guided dial had already stopped using.
    #[test]
    fn the_menu_turn_is_asked_with_the_loops_own_instrument() {
        assert_eq!(MENU_TOKENS, crate::kernel::agentloop::guided::MENU_TOKENS);
        // The forcing prefix carries no digit, so it cannot turn a non-answer
        // into the probe's right answer.
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
        // Fenced, or wrapped in prose: the chain rescues it, and that is a
        // `compact` model, not a `standard` one.
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
        // A parsed object naming a different action is a guess, not a contract.
        assert_eq!(
            score_action(r#"{"action": "final", "message": "done"}"#),
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

        // A different harness version is a different question.
        assert_eq!(cached(&config, "some/model", "12.1.0"), None);
        // And so is a different model.
        assert_eq!(cached(&config, "other/model", "12.0.0"), None);

        // A second verdict does not lose the first.
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
        // No model id is no question.
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
}
