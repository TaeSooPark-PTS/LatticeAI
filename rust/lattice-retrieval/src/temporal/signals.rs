use std::cmp::Ordering;
use std::collections::BTreeSet;

use lattice_core::correction_signals::{
    active_correction_signals, ActiveCorrection, CitationVerdict,
};
use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::{json, Value};

use super::plan::TemporalPlan;
use super::stages::{own_id, owner_id, score_of, set_score, updated_epoch};
use super::trust::{node_trust, TrustAssessment, TrustTier};

pub const WRONG_SOURCE_MULTIPLIER: f64 = 0.25;
pub const USEFUL_MULTIPLIER: f64 = 1.1;
pub const TRUST_ORDER: &str = "feedback_demotion_then_trust_tier_then_updated_at_desc";

pub fn verdict_multiplier(verdict: CitationVerdict) -> f64 {
    match verdict {
        CitationVerdict::WrongSource => WRONG_SOURCE_MULTIPLIER,
        CitationVerdict::Useful => USEFUL_MULTIPLIER,
    }
}

fn feedback_json(record: &ActiveCorrection, verdict: CitationVerdict) -> Value {
    json!({
        "verdict": verdict.as_str(),
        "correction_id": record.correction_id,
        "multiplier": verdict_multiplier(verdict),
    })
}

fn newest_first(a: &Value, b: &Value) -> Ordering {
    match (updated_epoch(a), updated_epoch(b)) {
        (Some(left), Some(right)) => right.partial_cmp(&left).unwrap_or(Ordering::Equal),
        (Some(_), None) => Ordering::Less,
        (None, Some(_)) => Ordering::Greater,
        (None, None) => Ordering::Equal,
    }
}

impl TemporalPlan {
    fn lookup<'a, T>(
        &self,
        map: &'a std::collections::HashMap<String, T>,
        item: &Value,
    ) -> Option<&'a T> {
        map.get(&own_id(item)).or_else(|| map.get(&owner_id(item)))
    }

    pub fn trust_of(&self, item: &Value) -> Option<&TrustAssessment> {
        self.lookup(&self.trust, item)
    }

    pub fn tier_of(&self, item: &Value) -> TrustTier {
        self.trust_of(item)
            .map_or(TrustTier::Normal, |found| found.tier)
    }

    pub fn verdict_of(&self, item: &Value) -> Option<CitationVerdict> {
        self.lookup(&self.feedback, item)
            .and_then(|record| record.verdict)
    }

    pub fn is_demoted(&self, item: &Value) -> bool {
        self.verdict_of(item) == Some(CitationVerdict::WrongSource)
    }

    pub fn annotate_signals(
        &mut self,
        conn: &Connection,
        items: &mut [Value],
    ) -> Result<(), CoreError> {
        let ids: Vec<String> = items
            .iter()
            .flat_map(|item| [own_id(item), owner_id(item)])
            .filter(|id| !id.is_empty())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        let corrections =
            active_correction_signals(conn, &ids, self.ranking.scope.allowed_workspaces.as_ref())?;
        self.trust = node_trust(conn, &ids, &corrections)?;
        self.feedback = corrections.citation;
        let mut tiers = [0usize; 3];
        let (mut demoted, mut boosted) = (0usize, 0usize);
        for item in items.iter_mut() {
            let feedback = self
                .lookup(&self.feedback, item)
                .and_then(|record| record.verdict.map(|verdict| feedback_json(record, verdict)));
            let verdict = self.verdict_of(item);
            let trust = self.trust_of(item).map(TrustAssessment::as_json);
            tiers[self.tier_of(item) as usize] += 1;
            if let Some(verdict) = verdict {
                let rescored = score_of(item) * verdict_multiplier(verdict);
                set_score(item, rescored);
                match verdict {
                    CitationVerdict::WrongSource => demoted += 1,
                    CitationVerdict::Useful => boosted += 1,
                }
            }
            if let Some(object) = item.as_object_mut() {
                if let Some(block) = feedback {
                    object.insert("feedback".into(), block);
                }
                if let Some(block) = trust {
                    object.insert("trust".into(), block);
                }
            }
        }
        if tiers[0] + tiers[2] > 0 {
            self.report.insert(
                "trust".into(),
                json!({
                    "verified": tiers[0],
                    "normal": tiers[1],
                    "draft": tiers[2],
                    "applies_to": "sort_newest",
                }),
            );
        }
        if demoted + boosted > 0 {
            self.report.insert(
                "feedback".into(),
                json!({
                    "source": "knowledge_corrections",
                    "wrong_source": demoted,
                    "useful": boosted,
                    "wrong_source_multiplier": WRONG_SOURCE_MULTIPLIER,
                    "useful_multiplier": USEFUL_MULTIPLIER,
                }),
            );
        }
        Ok(())
    }

    pub fn demote_flagged(&self, matches: &mut [Value]) {
        matches.sort_by_key(|item| self.is_demoted(item));
    }

    pub fn order_newest(&self, matches: &mut [Value]) {
        matches.sort_by(|a, b| {
            self.is_demoted(a)
                .cmp(&self.is_demoted(b))
                .then_with(|| self.tier_of(a).cmp(&self.tier_of(b)))
                .then_with(|| newest_first(a, b))
        });
    }
}
