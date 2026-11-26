#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TokenBudget {
    pub min: usize,
    pub target: usize,
    pub max: usize,
    pub overlap: usize,
}

impl TokenBudget {
    pub const FLAT: Self = Self {
        min: 300,
        target: 400,
        max: 500,
        overlap: 48,
    };
    pub const CHILD: Self = Self {
        min: 200,
        target: 250,
        max: 300,
        overlap: 30,
    };
    pub const PARENT: Self = Self {
        min: 800,
        target: 1000,
        max: 1200,
        overlap: 0,
    };

    pub fn content_cap(&self) -> usize {
        self.max.saturating_sub(self.overlap).max(1)
    }

    pub fn content_target(&self) -> usize {
        self.target.min(self.content_cap()).max(1)
    }

    pub fn overlap_ratio(&self) -> f64 {
        self.overlap as f64 / self.target.max(1) as f64
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChunkMode {
    ParentChild,
    Flat,
}

impl ChunkMode {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::ParentChild => "parent_child",
            Self::Flat => "flat",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ChunkingConfig {
    pub mode: ChunkMode,
    pub flat: TokenBudget,
    pub child: TokenBudget,
    pub parent: TokenBudget,
    pub contextual: bool,
    pub ast: bool,
}

impl Default for ChunkingConfig {
    fn default() -> Self {
        Self {
            mode: ChunkMode::ParentChild,
            flat: TokenBudget::FLAT,
            child: TokenBudget::CHILD,
            parent: TokenBudget::PARENT,
            contextual: true,
            ast: true,
        }
    }
}

fn switched_off(value: Option<String>) -> bool {
    value
        .map(|raw| raw.trim().to_ascii_lowercase())
        .is_some_and(|raw| matches!(raw.as_str(), "0" | "false" | "off" | "no"))
}

impl ChunkingConfig {
    pub const MODE_ENV: &'static str = "LATTICEAI_CHUNK_MODE";
    pub const CONTEXTUAL_ENV: &'static str = "LATTICEAI_CONTEXTUAL_CHUNKS";
    pub const AST_ENV: &'static str = "LATTICEAI_AST_CHUNKS";

    pub fn from_env() -> Self {
        Self::from_lookup(|key| std::env::var(key).ok())
    }

    pub fn from_lookup(lookup: impl Fn(&str) -> Option<String>) -> Self {
        let mode = match lookup(Self::MODE_ENV)
            .map(|raw| raw.trim().to_ascii_lowercase())
            .as_deref()
        {
            Some("flat") => ChunkMode::Flat,
            _ => ChunkMode::ParentChild,
        };
        Self {
            mode,
            contextual: !switched_off(lookup(Self::CONTEXTUAL_ENV)),
            ast: !switched_off(lookup(Self::AST_ENV)),
            ..Self::default()
        }
    }

    pub fn retrieval_budget(&self) -> TokenBudget {
        match self.mode {
            ChunkMode::ParentChild => self.child,
            ChunkMode::Flat => self.flat,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_budgets_hold_the_planned_ranges_and_overlap_ratios() {
        for budget in [TokenBudget::FLAT, TokenBudget::CHILD] {
            let ratio = budget.overlap_ratio();
            assert!((0.10..=0.15).contains(&ratio), "{budget:?} {ratio}");
            assert!(budget.content_cap() >= budget.min);
            assert!(budget.content_target() <= budget.content_cap());
        }
        assert_eq!((TokenBudget::FLAT.min, TokenBudget::FLAT.max), (300, 500));
        assert_eq!((TokenBudget::CHILD.min, TokenBudget::CHILD.max), (200, 300));
        assert_eq!(
            (TokenBudget::PARENT.min, TokenBudget::PARENT.max),
            (800, 1200)
        );
        assert_eq!(TokenBudget::PARENT.content_cap(), 1200);
    }

    #[test]
    fn the_environment_selects_the_mode_and_switches() {
        let config = ChunkingConfig::from_lookup(|_| None);
        assert_eq!(config, ChunkingConfig::default());
        assert_eq!(config.retrieval_budget(), TokenBudget::CHILD);
        assert_eq!(config.mode.as_str(), "parent_child");
        let flat = ChunkingConfig::from_lookup(|key| match key {
            ChunkingConfig::MODE_ENV => Some(" FLAT ".into()),
            ChunkingConfig::CONTEXTUAL_ENV => Some("off".into()),
            ChunkingConfig::AST_ENV => Some("0".into()),
            _ => None,
        });
        assert_eq!(flat.mode, ChunkMode::Flat);
        assert_eq!(flat.mode.as_str(), "flat");
        assert!(!flat.contextual);
        assert!(!flat.ast);
        assert_eq!(flat.retrieval_budget(), TokenBudget::FLAT);
        let on = ChunkingConfig::from_lookup(|_| Some("1".into()));
        assert!(on.contextual && on.ast);
        assert_eq!(on.mode, ChunkMode::ParentChild);
    }
}
