use std::sync::OnceLock;

use fancy_regex::Regex;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AgentProfile {
    pub name: &'static str,
    pub transcript_window: usize,
    pub parse_failure_budget: u32,
    pub escalate_after: u32,
    pub direct_path_fallback: bool,
    pub result_chars: usize,
    pub lean_context: bool,
    pub regeneration_retries: u32,
    pub decomposed: bool,
    pub forced_json_prefix: bool,
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

pub const ALL_PROFILES: [AgentProfile; 3] = [STANDARD, COMPACT, GUIDED];

pub const EXECUTE_JSON_PREFIX: &str = "{\"thoughts\": \"";

pub fn weakness(profile: AgentProfile) -> u8 {
    match profile.name {
        "guided" => 2,
        "compact" => 1,
        _ => 0,
    }
}

pub fn weaker_of(left: AgentProfile, right: AgentProfile) -> AgentProfile {
    if weakness(right) > weakness(left) {
        right
    } else {
        left
    }
}

pub fn profile_named(name: &str) -> Option<AgentProfile> {
    let name = name.trim().to_lowercase();
    ALL_PROFILES
        .into_iter()
        .find(|profile| profile.name == name)
}

pub const COMPACT_EXECUTE_TEMPERATURE: f64 = 0.1;
pub const STANDARD_EXECUTE_TEMPERATURE: f64 = 0.2;

pub fn execute_temperature(profile: AgentProfile) -> f64 {
    if profile.name == COMPACT.name {
        COMPACT_EXECUTE_TEMPERATURE
    } else {
        STANDARD_EXECUTE_TEMPERATURE
    }
}

pub const COMPACT_MAX_PARAMS_B: f64 = 4.0;

pub const GUIDED_MAX_PARAMS_B: f64 = 1.0;

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

pub fn model_size_b(model_id: &str) -> Option<f64> {
    let text = quant_pattern().replace_all(model_id, " ");
    smallest_match(size_pattern(), &text)
        .or_else(|| smallest_match(effective_size_pattern(), &text))
}

pub fn profile_for_model(model_id: Option<&str>) -> AgentProfile {
    profile_with_override(
        model_id,
        &std::env::var(PROFILE_OVERRIDE_ENV).unwrap_or_default(),
    )
}

pub fn profile_with_override(model_id: Option<&str>, override_name: &str) -> AgentProfile {
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
        assert_eq!(model_size_b("mlx-community/gemma-4-e2b-it-4bit"), Some(2.0));
        assert_eq!(model_size_b("mlx-community/gemma-4-e4b-it-4bit"), Some(4.0));
        assert_eq!(model_size_b("gemma-4-e2b-it-8bit"), Some(2.0));
        assert_eq!(model_size_b("Gemma-4-E2B-it"), Some(2.0));
    }

    #[test]
    fn an_active_parameter_marker_never_shrinks_a_mixture_of_experts() {
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
        assert_eq!(
            (
                STANDARD.forced_json_prefix,
                COMPACT.forced_json_prefix,
                GUIDED.forced_json_prefix,
            ),
            (false, true, false)
        );
        assert_eq!(EXECUTE_JSON_PREFIX, "{\"thoughts\": \"");
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
        assert_eq!(profile_for_model(Some("qwen2.5-1.5b")).name, "compact");
        assert_eq!(GUIDED_MAX_PARAMS_B, 1.0);
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
