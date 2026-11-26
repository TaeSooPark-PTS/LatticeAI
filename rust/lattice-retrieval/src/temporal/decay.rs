use lattice_core::pytext::{parse_iso, recency_score, round6};
use serde_json::{json, Value};

use crate::policy::RECENCY_HALF_LIFE_DAYS;

pub const HALF_LIFE_ENV: &str = "LATTICEAI_RECENCY_HALF_LIFE_DAYS";
pub const DECAY_FLOOR_ENV: &str = "LATTICEAI_RECENCY_DECAY_FLOOR";
pub const DEFAULT_HALF_LIFE_DAYS: f64 = 60.0;
pub const MIN_HALF_LIFE_DAYS: f64 = 30.0;
pub const MAX_HALF_LIFE_DAYS: f64 = 90.0;
pub const DEFAULT_DECAY_FLOOR: f64 = 0.8;
pub const RECENCY_CLASS_FLOOR: f64 = 0.5;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct RecencyDecay {
    pub half_life_days: f64,
    pub floor: f64,
}

impl Default for RecencyDecay {
    fn default() -> Self {
        Self {
            half_life_days: DEFAULT_HALF_LIFE_DAYS,
            floor: DEFAULT_DECAY_FLOOR,
        }
    }
}

impl RecencyDecay {
    pub fn from_env() -> Self {
        Self::from_lookup(|key| std::env::var(key).ok())
    }

    pub fn from_lookup(lookup: impl Fn(&str) -> Option<String>) -> Self {
        let number = |key: &str| {
            lookup(key)
                .and_then(|raw| raw.trim().parse::<f64>().ok())
                .filter(|value| value.is_finite())
        };
        Self {
            half_life_days: number(HALF_LIFE_ENV)
                .unwrap_or(DEFAULT_HALF_LIFE_DAYS)
                .clamp(MIN_HALF_LIFE_DAYS, MAX_HALF_LIFE_DAYS),
            floor: number(DECAY_FLOOR_ENV)
                .unwrap_or(DEFAULT_DECAY_FLOOR)
                .clamp(0.0, 1.0),
        }
    }

    pub fn for_class(&self, recency_class: bool) -> (f64, f64) {
        if recency_class {
            (RECENCY_HALF_LIFE_DAYS, RECENCY_CLASS_FLOOR.min(self.floor))
        } else {
            (self.half_life_days, self.floor)
        }
    }

    pub fn multiplier(&self, updated_at: Option<&str>, now_secs: f64, recency_class: bool) -> f64 {
        if parse_iso(updated_at).is_none() {
            return 1.0;
        }
        let (half_life, floor) = self.for_class(recency_class);
        round6(floor + (1.0 - floor) * recency_score(updated_at, now_secs, half_life))
    }

    pub fn as_json(&self, recency_class: bool) -> Value {
        let (half_life, floor) = self.for_class(recency_class);
        json!({
            "always_on": true,
            "half_life_days": half_life,
            "floor": floor,
            "configured_half_life_days": self.half_life_days,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn half_life_is_clamped_to_the_thirty_to_ninety_day_band() {
        let lookup = |pairs: &'static [(&'static str, &'static str)]| {
            move |key: &str| {
                pairs
                    .iter()
                    .find(|(k, _)| *k == key)
                    .map(|(_, v)| v.to_string())
            }
        };
        assert_eq!(RecencyDecay::from_lookup(|_| None), RecencyDecay::default());
        assert_eq!(
            RecencyDecay::from_lookup(lookup(&[(HALF_LIFE_ENV, "7")])).half_life_days,
            30.0
        );
        assert_eq!(
            RecencyDecay::from_lookup(lookup(&[(HALF_LIFE_ENV, "365")])).half_life_days,
            90.0
        );
        let tuned =
            RecencyDecay::from_lookup(lookup(&[(HALF_LIFE_ENV, "45"), (DECAY_FLOOR_ENV, "2")]));
        assert_eq!((tuned.half_life_days, tuned.floor), (45.0, 1.0));
        assert_eq!(
            RecencyDecay::from_lookup(lookup(&[(HALF_LIFE_ENV, "NaN")])).half_life_days,
            60.0
        );
    }

    #[test]
    fn the_multiplier_halves_toward_the_floor_once_per_half_life() {
        let decay = RecencyDecay::default();
        let now = parse_iso(Some("2026-12-01T00:00:00")).unwrap();
        assert_eq!(
            decay.multiplier(Some("2026-12-01T00:00:00"), now, false),
            1.0
        );
        assert_eq!(
            decay.multiplier(Some("2026-10-02T00:00:00"), now, false),
            0.9
        );
        assert_eq!(decay.multiplier(None, now, false), 1.0);
        assert_eq!(decay.multiplier(Some("not a date"), now, false), 1.0);
        assert_eq!(
            decay.multiplier(Some("2026-11-17T00:00:00"), now, true),
            0.75
        );
        assert_eq!(decay.as_json(true)["half_life_days"], 14.0);
        assert_eq!(decay.as_json(false)["floor"], 0.8);
    }
}
