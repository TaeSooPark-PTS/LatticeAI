pub mod calendar;
pub mod decay;
pub mod external;
pub mod intent;
pub mod params;
pub mod plan;
mod prefilter;
pub mod run;
mod stages;

pub use decay::RecencyDecay;
pub use external::{rank_candidates, RankedCandidates};
pub use intent::{parse_time_intent, TimeIntent, TimeWindow};
pub use params::{ranking_from_lookup, TEMPORAL_PARAM_KEYS};
pub use plan::TemporalPlan;
pub use run::{prepare_ranked, ranked_hybrid_search, ranked_search, search_text_for, EngineCall};

pub const SORT_TOP_K_ENV: &str = "LATTICEAI_RECENCY_SORT_TOP_K";
pub const MIN_RELEVANCE_ENV: &str = "LATTICEAI_RELEVANCE_MIN_RATIO";
pub const DEFAULT_SORT_TOP_K: usize = 12;
pub const MIN_SORT_TOP_K: usize = 10;
pub const MAX_SORT_TOP_K: usize = 15;
pub const DEFAULT_MIN_RELEVANCE_RATIO: f64 = 0.3;
pub const PIPELINE_ORDER: [&str; 6] = [
    "temporal_prefilter",
    "fusion_with_recency_decay",
    "superseded_policy",
    "rerank",
    "relevance_gate",
    "sort",
];

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum SortBy {
    #[default]
    Auto,
    Relevance,
    Newest,
}

impl SortBy {
    pub fn parse(raw: Option<&str>) -> Result<Self, String> {
        match raw
            .map(|value| value.trim().to_ascii_lowercase())
            .as_deref()
        {
            None | Some("") | Some("auto") => Ok(Self::Auto),
            Some("relevance") | Some("score") => Ok(Self::Relevance),
            Some("newest") | Some("recent") | Some("updated_at") => Ok(Self::Newest),
            Some(other) => Err(format!(
                "sort_by must be one of relevance, newest, auto (got {other:?})"
            )),
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Auto => "auto",
            Self::Relevance => "relevance",
            Self::Newest => "newest",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct RelevanceGate {
    pub top_k: usize,
    pub min_ratio: f64,
}

impl Default for RelevanceGate {
    fn default() -> Self {
        Self {
            top_k: DEFAULT_SORT_TOP_K,
            min_ratio: DEFAULT_MIN_RELEVANCE_RATIO,
        }
    }
}

impl RelevanceGate {
    pub fn from_lookup(lookup: impl Fn(&str) -> Option<String>) -> Self {
        let defaults = Self::default();
        Self {
            top_k: lookup(SORT_TOP_K_ENV)
                .and_then(|raw| raw.trim().parse::<usize>().ok())
                .unwrap_or(defaults.top_k)
                .clamp(MIN_SORT_TOP_K, MAX_SORT_TOP_K),
            min_ratio: lookup(MIN_RELEVANCE_ENV)
                .and_then(|raw| raw.trim().parse::<f64>().ok())
                .filter(|value| value.is_finite())
                .unwrap_or(defaults.min_ratio)
                .clamp(0.0, 1.0),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct TemporalRanking {
    pub sort_by: SortBy,
    pub include_historical: bool,
    pub since: Option<String>,
    pub until: Option<String>,
    pub as_of: Option<String>,
    pub scope: crate::service::Scope,
    pub parse_intent: bool,
    pub decay: RecencyDecay,
    pub gate: RelevanceGate,
}

impl Default for TemporalRanking {
    fn default() -> Self {
        Self {
            sort_by: SortBy::Auto,
            include_historical: false,
            since: None,
            until: None,
            as_of: None,
            scope: crate::service::Scope::default(),
            parse_intent: true,
            decay: RecencyDecay::default(),
            gate: RelevanceGate::default(),
        }
    }
}

impl TemporalRanking {
    pub fn from_env() -> Self {
        Self::from_lookup(|key| std::env::var(key).ok())
    }

    pub fn from_lookup(lookup: impl Fn(&str) -> Option<String> + Copy) -> Self {
        Self {
            decay: RecencyDecay::from_lookup(lookup),
            gate: RelevanceGate::from_lookup(lookup),
            ..Self::default()
        }
    }

    pub fn with_request(
        mut self,
        sort_by: SortBy,
        include_historical: bool,
        since: Option<String>,
        until: Option<String>,
    ) -> Self {
        self.sort_by = sort_by;
        self.include_historical = include_historical;
        self.since = since.filter(|value| !value.trim().is_empty());
        self.until = until.filter(|value| !value.trim().is_empty());
        self
    }

    pub fn with_as_of(mut self, as_of: Option<String>) -> Self {
        if let Some(value) = as_of.filter(|value| !value.trim().is_empty()) {
            self.as_of = Some(value);
        }
        self
    }

    pub fn with_scope(mut self, scope: crate::service::Scope) -> Self {
        self.scope = scope;
        self
    }
}

#[cfg(test)]
mod as_of_tests;
#[cfg(test)]
mod tests;
