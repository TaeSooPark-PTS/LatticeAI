//! Agent profiles — matching the loop to the model driving it.
//!
//! A port of `latticeai.core.agent_profiles`. Two dials, selected from the model
//! id: `standard` for models that can hold a tool-call contract, `compact` for
//! the 1–4B local models that cannot — a shorter transcript window, an earlier
//! escalation to naming valid tools, and the direct-path fallback.
//!
//! Selection never guesses: a model id that names no size gets `standard`,
//! which is the no-behaviour-change choice.

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
}

pub const STANDARD: AgentProfile = AgentProfile {
    name: "standard",
    transcript_window: 8,
    parse_failure_budget: 3,
    escalate_after: 2,
    direct_path_fallback: false,
};

pub const COMPACT: AgentProfile = AgentProfile {
    name: "compact",
    transcript_window: 4,
    parse_failure_budget: 4,
    escalate_after: 1,
    direct_path_fallback: true,
};

/// At or below this parameter count, the compact profile applies.
pub const COMPACT_MAX_PARAMS_B: f64 = 4.0;

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

/// Parameter count in billions parsed from a model id, or `None`.
///
/// The quantization suffix (`4bit`, `8bit`) is removed first: it is not a
/// parameter count, and reading it as one would mislabel every quantized model.
pub fn model_size_b(model_id: &str) -> Option<f64> {
    let text = quant_pattern().replace_all(model_id, " ");
    size_pattern()
        .captures_iter(&text)
        .filter_map(|captures| captures.ok())
        .filter_map(|captures| captures.get(1)?.as_str().parse::<f64>().ok())
        .fold(None, |smallest: Option<f64>, size| {
            Some(smallest.map_or(size, |current| current.min(size)))
        })
}

/// Pick the loop profile for a model: explicit override → size → `standard`.
pub fn profile_for_model(model_id: Option<&str>) -> AgentProfile {
    let override_name = std::env::var("LATTICEAI_AGENT_PROFILE")
        .unwrap_or_default()
        .trim()
        .to_lowercase();
    match override_name.as_str() {
        "standard" => return STANDARD,
        "compact" => return COMPACT,
        // An unrecognised override falls through to the heuristic.
        _ => {}
    }
    match model_size_b(model_id.unwrap_or("")) {
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
}
