//! Agent profiles — matching the loop to the model driving it.
//!
//! A port of `latticeai.core.agent_profiles`, extended in v12.0.0. **Three**
//! dials now:
//!
//! * `standard` — the model can hold a tool-call contract. Unchanged.
//! * `compact` — the 1–4B local models that struggle with one: a shorter
//!   transcript window, an earlier escalation to naming valid tools, the
//!   direct-path fallback, and (v12.0.0) a forced `{"thoughts": "` prefix on
//!   the executor completion so the reply *starts* inside the object.
//! * `guided` — the model cannot hold one at all. The loop stops asking for a
//!   JSON object and asks a numbered question instead
//!   ([`crate::kernel::agentloop::guided`]): pick an action by number, then
//!   answer one argument per turn. The harness assembles the action struct, so
//!   there is no JSON for the model to get wrong.
//!
//! ## How a profile is chosen (v12.0.0)
//!
//! [`profile_for_model`] is still the *prior*, and it is still a regex over the
//! model id — but it is no longer the answer. The answer is
//! [`crate::kernel::probe`]: two tiny deterministic completions that **measure**
//! whether this model can emit a clean action object and read a numbered menu,
//! cached per model id and harness version. The regex is what answers when the
//! probe cannot run at all (no model id, no worker, probing switched off).
//!
//! `LATTICEAI_AGENT_PROFILE` pins any of the three names and outranks both.

use std::sync::OnceLock;

use fancy_regex::Regex;

/// How hard the loop should work to keep a given model on contract.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AgentProfile {
    pub name: &'static str,
    /// Executor transcript window (steps kept in full).
    pub transcript_window: usize,
    /// Format slips tolerated before the run stops retrying.
    pub parse_failure_budget: u32,
    /// Slip count at which the correction hint starts naming valid tool names.
    pub escalate_after: u32,
    /// Whether an exhausted JSON loop may fall back to writing planned files.
    pub direct_path_fallback: bool,
    /// Per-step result slice in the executor transcript (v11.9.0).
    ///
    /// The transcript *window* alone was never the whole context: four steps
    /// carrying 700 characters of tool output each is still 3k characters of
    /// prompt before the plan, the written-files hint and the conversation are
    /// added. A 2B model at step five was being handed 6–10k tokens and losing
    /// the action contract inside them.
    pub result_chars: usize,
    /// Drop the written-files hint and the recent conversation from the
    /// executor prompt (v11.9.0). Both are *nice to have* framing for a model
    /// that can hold a contract, and both are pure context pressure for one
    /// that cannot — the plan and the transcript already carry the facts.
    pub lean_context: bool,
    /// Bounded regenerations the direct-path fallback may spend on content that
    /// would otherwise have to be repaired (v11.9.0, ported from the deleted
    /// `file_generation.orchestration`). Zero means "write what you got".
    pub regeneration_retries: u32,
    /// **Decompose each step into micro-turns instead of asking for JSON**
    /// (v12.0.0). The `guided` dial, and the only field that changes which code
    /// path runs rather than how hard it tries: see
    /// [`crate::kernel::agentloop::guided`].
    pub decomposed: bool,
    /// Start the executor completion inside the object, with
    /// [`EXECUTE_JSON_PREFIX`] (v12.0.0).
    ///
    /// Structure by construction at the cheapest possible price: a model whose
    /// reply is *forced* to begin `{"thoughts": "` cannot open with prose, a
    /// markdown fence or a `<|channel|>` frame, which between them are most of
    /// what the repair chain exists to undo. Off for `standard` — a model that
    /// holds the contract needs no help and the prefix would only take the
    /// no-behaviour-change side away from it.
    pub forced_json_prefix: bool,
    /// Micro-turns one guided step may spend before the step gives up
    /// (v12.0.0). Zero everywhere the guided path does not run.
    pub micro_turn_cap: u32,
}

pub const STANDARD: AgentProfile = AgentProfile {
    name: "standard",
    transcript_window: 8,
    parse_failure_budget: 3,
    escalate_after: 2,
    direct_path_fallback: false,
    result_chars: 700,
    lean_context: false,
    regeneration_retries: 0,
    decomposed: false,
    forced_json_prefix: false,
    micro_turn_cap: 0,
};

pub const COMPACT: AgentProfile = AgentProfile {
    name: "compact",
    transcript_window: 4,
    parse_failure_budget: 4,
    escalate_after: 1,
    direct_path_fallback: true,
    result_chars: 200,
    lean_context: true,
    regeneration_retries: 1,
    decomposed: false,
    forced_json_prefix: true,
    micro_turn_cap: 0,
};

/// The decomposed dial: no JSON is ever asked for (v12.0.0).
///
/// Every other number is `compact`'s or tighter. The transcript window is two
/// steps because a guided turn re-reads the context on *every* micro-turn, so
/// context pressure is multiplied rather than paid once per step; the parse
/// budget counts menu turns that produced no choice at all, which is a much
/// rarer event than a malformed object.
pub const GUIDED: AgentProfile = AgentProfile {
    name: "guided",
    transcript_window: 2,
    parse_failure_budget: 3,
    escalate_after: 1,
    direct_path_fallback: true,
    result_chars: 160,
    lean_context: true,
    regeneration_retries: 1,
    decomposed: true,
    forced_json_prefix: false,
    micro_turn_cap: 3,
};

/// The three dials, weakest last — the order [`weaker_of`] compares on.
pub const ALL_PROFILES: [AgentProfile; 3] = [STANDARD, COMPACT, GUIDED];

/// The completion prefix [`AgentProfile::forced_json_prefix`] sends.
///
/// It is the opening of the one shape the executor parser wants, and the shape
/// [`crate::prompts`]' worked example shows — so a prefilled reply and a copied
/// example are the same bytes.
pub const EXECUTE_JSON_PREFIX: &str = "{\"thoughts\": \"";

/// How weak a dial is: `standard` 0, `compact` 1, `guided` 2.
pub fn weakness(profile: AgentProfile) -> u8 {
    match profile.name {
        "guided" => 2,
        "compact" => 1,
        _ => 0,
    }
}

/// The weaker (more forgiving) of two dials.
///
/// Used where two answers disagree and the *safe* one is the one that assumes
/// less of the model — a wrong `guided` costs micro-turns, a wrong `standard`
/// costs the run.
pub fn weaker_of(left: AgentProfile, right: AgentProfile) -> AgentProfile {
    if weakness(right) > weakness(left) {
        right
    } else {
        left
    }
}

/// The dial this name spells, or `None` — the one place a profile name is read.
pub fn profile_named(name: &str) -> Option<AgentProfile> {
    let name = name.trim().to_lowercase();
    ALL_PROFILES
        .into_iter()
        .find(|profile| profile.name == name)
}

/// Low temperature is what makes a 2B emit stable JSON.
pub const COMPACT_EXECUTE_TEMPERATURE: f64 = 0.1;
/// A capable model can use a little entropy; EXECUTE still stays well below chat default.
pub const STANDARD_EXECUTE_TEMPERATURE: f64 = 0.2;

/// The EXECUTE-phase sampler temperature for this profile.
///
/// PLAN and VERIFY keep their own hardcoded temps; only the executor call
/// reads this. The request body's `temperature` is not the execute default.
pub fn execute_temperature(profile: AgentProfile) -> f64 {
    if profile.name == COMPACT.name {
        COMPACT_EXECUTE_TEMPERATURE
    } else {
        STANDARD_EXECUTE_TEMPERATURE
    }
}

/// At or below this parameter count, the compact profile applies.
pub const COMPACT_MAX_PARAMS_B: f64 = 4.0;

/// At or below this parameter count, the *prior* is `guided` (v12.0.0).
///
/// One billion, and it is a **prior**, not a verdict: a 0.5B model asked for a
/// JSON tool call produces prose about a JSON tool call, and if we cannot
/// measure this one ([`crate::kernel::probe`]) the honest default is the dial
/// that never asks. A probe that runs replaces this outright — including
/// upward, for the rare small model that really can hold the contract.
pub const GUIDED_MAX_PARAMS_B: f64 = 1.0;

/// The environment variable that pins a profile regardless of model size.
pub const PROFILE_OVERRIDE_ENV: &str = "LATTICEAI_AGENT_PROFILE";

fn size_pattern() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)(?<![a-z0-9.])(\d+(?:\.\d+)?)\s*b(?![a-z0-9])")
            .expect("ported pattern must compile")
    })
}

fn quant_pattern() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\d+\s*bit").expect("ported pattern must compile"))
}

/// `e2b` / `e4b` — an **effective** parameter count (v11.9.0).
///
/// Gemma 4's MatFormer releases name what the model costs to run, not what it
/// weighs: `gemma-4-e2b-it-4bit` is the 8GB-tier default and behaves like a 2B
/// model, but the plain-size pattern rejects `2b` because a letter precedes it.
/// The result was the worst possible answer — the smallest recommended local
/// model got the `standard` profile, so the one loop that has a direct-path
/// fallback never ran for the one model that needs it.
///
/// Only `e` is honoured. Active-parameter markers use `a`/`A`
/// (`gemma-4-26b-a4b-it-4bit`, `Qwen3.6-35B-A3B`) and naming *those* as sizes
/// would classify a 26B MoE as compact, which is the opposite mistake and a
/// worse one — a big model would be handed the tiny-model prompt and the
/// direct-path fallback.
fn effective_size_pattern() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)(?<![a-z0-9.])e(\d+(?:\.\d+)?)\s*b(?![a-z0-9])")
            .expect("ported pattern must compile")
    })
}

fn smallest_match(pattern: &Regex, text: &str) -> Option<f64> {
    pattern
        .captures_iter(text)
        .filter_map(|captures| captures.ok())
        .filter_map(|captures| captures.get(1)?.as_str().parse::<f64>().ok())
        .fold(None, |smallest: Option<f64>, size| {
            Some(smallest.map_or(size, |current| current.min(size)))
        })
}

/// Parameter count in billions parsed from a model id, or `None`.
///
/// The quantization suffix (`4bit`, `8bit`) is removed first: it is not a
/// parameter count, and reading it as one would mislabel every quantized model.
///
/// A **plain** size token always decides. Only when the id names none is the
/// `e`-prefixed effective size read, so an id that carries both — a
/// hypothetical `…-26b-e2b-…` — is judged by the weight it actually loads
/// rather than by the cheaper number beside it.
pub fn model_size_b(model_id: &str) -> Option<f64> {
    let text = quant_pattern().replace_all(model_id, " ");
    smallest_match(size_pattern(), &text)
        .or_else(|| smallest_match(effective_size_pattern(), &text))
}

/// Pick the loop profile for a model: explicit override → size → `standard`.
pub fn profile_for_model(model_id: Option<&str>) -> AgentProfile {
    profile_with_override(
        model_id,
        &std::env::var(PROFILE_OVERRIDE_ENV).unwrap_or_default(),
    )
}

/// [`profile_for_model`] with the override supplied instead of read.
///
/// Python's `profile_for_model` already took its environment as an argument;
/// this is the same seam, and it is what the parity golden drives — a fixture
/// that had to mutate process-global environment state to check the override
/// rows would be a test that breaks whenever the suite runs in parallel.
pub fn profile_with_override(model_id: Option<&str>, override_name: &str) -> AgentProfile {
    // An unrecognised override falls through to the heuristic.
    if let Some(pinned) = profile_named(override_name) {
        return pinned;
    }
    match model_size_b(model_id.unwrap_or("")) {
        Some(size) if size <= GUIDED_MAX_PARAMS_B => GUIDED,
        Some(size) if size <= COMPACT_MAX_PARAMS_B => COMPACT,
        _ => STANDARD,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_size_in_the_id_is_read_and_the_quantization_is_not() {
        assert_eq!(model_size_b("gemma-3-4b-it-4bit"), Some(4.0));
        assert_eq!(model_size_b("qwen2.5-1.5b"), Some(1.5));
        assert_eq!(model_size_b("llama-3.2-3B"), Some(3.0));
        // `7` is preceded by `x`, which the ASCII lookbehind rejects — the same
        // answer Python gives, cross-checked.
        assert_eq!(model_size_b("mixtral-8x7b"), None);
        assert_eq!(
            model_size_b("some-model-8bit"),
            None,
            "quantization is not a size"
        );
        assert_eq!(model_size_b("gpt-oss"), None);
        assert_eq!(model_size_b(""), None);
    }

    #[test]
    fn the_smallest_named_size_decides() {
        assert_eq!(model_size_b("moe-2b-of-70b"), Some(2.0));
    }

    #[test]
    fn an_e_prefixed_effective_size_is_a_size() {
        // The 8GB-tier recommended default, and its 16GB sibling.
        assert_eq!(model_size_b("mlx-community/gemma-4-e2b-it-4bit"), Some(2.0));
        assert_eq!(model_size_b("mlx-community/gemma-4-e4b-it-4bit"), Some(4.0));
        assert_eq!(model_size_b("gemma-4-e2b-it-8bit"), Some(2.0));
        // Case and the `E2B` spelling are the same token.
        assert_eq!(model_size_b("Gemma-4-E2B-it"), Some(2.0));
    }

    #[test]
    fn an_active_parameter_marker_never_shrinks_a_mixture_of_experts() {
        // `a4b` / `A3B` are *active* parameters per token, not the model's
        // size. Reading them as sizes would hand a 26B/35B model the compact
        // loop — the failure this whole dial exists to avoid, inverted.
        assert_eq!(model_size_b("gemma-4-26b-a4b-it-4bit"), Some(26.0));
        assert_eq!(model_size_b("Qwen3.6-35B-A3B-4bit"), Some(35.0));
        assert_eq!(
            model_size_b("some-moe-a3b"),
            None,
            "a marker alone is not a size"
        );
        assert_eq!(
            profile_for_model(Some("gemma-4-26b-a4b-it-4bit")).name,
            "standard"
        );
        assert_eq!(
            profile_for_model(Some("Qwen3.6-35B-A3B-4bit")).name,
            "standard"
        );
    }

    #[test]
    fn a_plain_size_outranks_an_effective_one_in_the_same_id() {
        // Weight decides what has to be held in memory and what the model can
        // hold in its head; the effective count only decides what a step costs.
        assert_eq!(model_size_b("gemma-4-26b-e2b-it-4bit"), Some(26.0));
        assert_eq!(
            profile_for_model(Some("gemma-4-26b-e2b-it-4bit")).name,
            "standard"
        );
    }

    #[test]
    fn the_gemma_four_effective_sizes_reach_the_compact_loop() {
        for model_id in [
            "mlx-community/gemma-4-e2b-it-4bit",
            "mlx-community/gemma-4-e4b-it-4bit",
        ] {
            let profile = profile_for_model(Some(model_id));
            assert_eq!(profile.name, "compact", "{model_id}");
            assert!(
                profile.direct_path_fallback,
                "{model_id}: the fallback is the whole point"
            );
        }
    }

    #[test]
    fn small_models_get_the_compact_loop_and_unknown_ones_do_not() {
        assert_eq!(
            profile_for_model(Some("gemma-3-4b-it-4bit")).name,
            "compact"
        );
        assert_eq!(profile_for_model(Some("qwen2.5-1.5b")).name, "compact");
        assert_eq!(profile_for_model(Some("llama-3.3-70b")).name, "standard");
        assert_eq!(profile_for_model(Some("mystery-model")).name, "standard");
        assert_eq!(profile_for_model(None).name, "standard");
    }

    #[test]
    fn the_two_profiles_carry_the_python_dial_values() {
        // Compared as records so a changed field shows up as a diff rather than
        // as a `assert!(constant)` clippy refuses to accept.
        assert_eq!(
            (
                STANDARD.transcript_window,
                STANDARD.parse_failure_budget,
                STANDARD.escalate_after,
                STANDARD.direct_path_fallback,
            ),
            (8, 3, 2, false)
        );
        assert_eq!(
            (
                COMPACT.transcript_window,
                COMPACT.parse_failure_budget,
                COMPACT.escalate_after,
                COMPACT.direct_path_fallback,
            ),
            (4, 4, 1, true)
        );
        assert_eq!(COMPACT_MAX_PARAMS_B, 4.0);
    }

    #[test]
    fn the_guided_dial_is_the_only_decomposed_one_and_the_weakest() {
        // Compared as records, as the dial test below is and for the same
        // reason: a changed field shows up as a diff, and clippy will not take
        // an `assert!` over a constant.
        assert_eq!(
            (STANDARD.decomposed, COMPACT.decomposed, GUIDED.decomposed,),
            (false, false, true),
            "guided is the micro-turn dial and the only one"
        );
        assert_eq!(
            (
                STANDARD.micro_turn_cap,
                COMPACT.micro_turn_cap,
                GUIDED.micro_turn_cap,
            ),
            (0, 0, 3),
            "only a guided step may retry a micro-turn"
        );
        assert_eq!(
            (
                STANDARD.direct_path_fallback,
                COMPACT.direct_path_fallback,
                GUIDED.direct_path_fallback,
            ),
            (false, true, true),
            "the last resort still applies under guided"
        );
        assert_eq!(
            (weakness(STANDARD), weakness(COMPACT), weakness(GUIDED),),
            (0, 1, 2)
        );
        assert_eq!(weaker_of(STANDARD, GUIDED).name, "guided");
        assert_eq!(weaker_of(GUIDED, STANDARD).name, "guided");
        assert_eq!(weaker_of(COMPACT, COMPACT).name, "compact");
        for profile in ALL_PROFILES {
            assert_eq!(profile_named(profile.name), Some(profile));
            assert_eq!(
                profile_named(&profile.name.to_uppercase()),
                Some(profile),
                "the override is case-insensitive"
            );
        }
        assert_eq!(profile_named("nonsense"), None);
        assert_eq!(profile_named(""), None);
    }

    #[test]
    fn only_the_dials_that_need_help_are_prefilled() {
        // Structure by construction is for the models that cannot hold it; a
        // `standard` run composes exactly the completion it always did, and
        // guided asks for no JSON at all so a JSON prefix would be a lie.
        assert_eq!(
            (
                STANDARD.forced_json_prefix,
                COMPACT.forced_json_prefix,
                GUIDED.forced_json_prefix,
            ),
            (false, true, false)
        );
        assert_eq!(EXECUTE_JSON_PREFIX, "{\"thoughts\": \"");
        // The prefix must be the opening of a reply the parser accepts.
        let completed = format!("{EXECUTE_JSON_PREFIX}note\", \"action\": \"final\"}}");
        let (action, repairs) =
            crate::parse::action::extract_action_details(&completed).expect("prefilled reply");
        assert_eq!(action["action"], "final");
        assert_eq!(repairs, Vec::<String>::new());
    }

    #[test]
    fn the_size_prior_reaches_guided_only_for_the_smallest_models() {
        assert_eq!(
            profile_for_model(Some("Qwen2.5-0.5B-Instruct-AWQ")).name,
            "guided"
        );
        assert_eq!(profile_for_model(Some("tinyllama-1b")).name, "guided");
        // 1.5B is still a compact model, not a guided one.
        assert_eq!(profile_for_model(Some("qwen2.5-1.5b")).name, "compact");
        assert_eq!(GUIDED_MAX_PARAMS_B, 1.0);
        // And the override still pins any of the three by name.
        assert_eq!(
            profile_with_override(Some("llama-3.3-70b"), "guided").name,
            "guided"
        );
        assert_eq!(
            profile_with_override(Some("qwen-0.5b"), "standard").name,
            "standard"
        );
    }

    #[test]
    fn the_v11_9_context_dials_differ_only_where_they_were_meant_to() {
        assert_eq!(
            (
                STANDARD.result_chars,
                STANDARD.lean_context,
                STANDARD.regeneration_retries,
            ),
            (700, false, 0),
            "standard is the no-behaviour-change side"
        );
        assert_eq!(
            (
                COMPACT.result_chars,
                COMPACT.lean_context,
                COMPACT.regeneration_retries,
            ),
            (200, true, 1)
        );
        assert_eq!(execute_temperature(COMPACT), COMPACT_EXECUTE_TEMPERATURE);
        assert_eq!(execute_temperature(STANDARD), STANDARD_EXECUTE_TEMPERATURE);
        assert_eq!(COMPACT_EXECUTE_TEMPERATURE, 0.1);
        assert_eq!(STANDARD_EXECUTE_TEMPERATURE, 0.2);
    }
}
