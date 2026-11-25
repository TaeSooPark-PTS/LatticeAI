use rusqlite::Transaction;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::db::CoreError;
use crate::pytext::safe_loads;

use super::pyaux::json_of;
use super::types::EdgeSpec;
use super::GraphWriter;

type V2LifecycleRow = (
    Option<String>,
    Option<String>,
    Option<String>,
    Option<String>,
);

fn default_lifecycle_version() -> u32 {
    1
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum KnowledgeLifecycleState {
    #[default]
    Active,
    Superseded,
    Deprecated,
    Disputed,
    PendingVerification,
}

impl KnowledgeLifecycleState {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Active => "active",
            Self::Superseded => "superseded",
            Self::Deprecated => "deprecated",
            Self::Disputed => "disputed",
            Self::PendingVerification => "pending_verification",
        }
    }

    pub fn parse(raw: &str) -> Option<Self> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "active" => Some(Self::Active),
            "superseded" => Some(Self::Superseded),
            "deprecated" => Some(Self::Deprecated),
            "disputed" => Some(Self::Disputed),
            "pending_verification" | "pending-verification" | "pending" => {
                Some(Self::PendingVerification)
            }
            _ => None,
        }
    }

    pub fn is_active(&self) -> bool {
        matches!(self, Self::Active)
    }

    pub fn is_superseded_or_deprecated(&self) -> bool {
        matches!(self, Self::Superseded | Self::Deprecated)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LifecycleHistoryEntry {
    pub timestamp: String,
    pub state: KnowledgeLifecycleState,
    #[serde(default)]
    pub actor: Option<String>,
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default = "default_lifecycle_version")]
    pub version: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct KnowledgeLifecycleMetadata {
    #[serde(default)]
    pub state: KnowledgeLifecycleState,
    #[serde(default = "default_lifecycle_version")]
    pub version: u32,
    #[serde(default)]
    pub effective_from: Option<String>,
    #[serde(default)]
    pub valid_until: Option<String>,
    #[serde(default)]
    pub deprecated_at: Option<String>,
    #[serde(default)]
    pub authority: Option<String>,
    #[serde(default)]
    pub approver: Option<String>,
    #[serde(default)]
    pub superseded_by: Option<String>,
    #[serde(default)]
    pub supersedes: Vec<String>,
    #[serde(default)]
    pub conflicts_with: Vec<String>,
    #[serde(default)]
    pub evidence_node_ids: Vec<String>,
    #[serde(default)]
    pub history: Vec<LifecycleHistoryEntry>,
}

impl Default for KnowledgeLifecycleMetadata {
    fn default() -> Self {
        Self {
            state: KnowledgeLifecycleState::Active,
            version: default_lifecycle_version(),
            effective_from: None,
            valid_until: None,
            deprecated_at: None,
            authority: None,
            approver: None,
            superseded_by: None,
            supersedes: Vec::new(),
            conflicts_with: Vec::new(),
            evidence_node_ids: Vec::new(),
            history: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct KnowledgeLifecycleUpdate {
    #[serde(default)]
    pub state: Option<KnowledgeLifecycleState>,
    #[serde(default)]
    pub version: Option<u32>,
    #[serde(default)]
    pub effective_from: Option<String>,
    #[serde(default)]
    pub valid_until: Option<String>,
    #[serde(default)]
    pub deprecated_at: Option<String>,
    #[serde(default)]
    pub authority: Option<String>,
    #[serde(default)]
    pub approver: Option<String>,
    #[serde(default)]
    pub superseded_by: Option<String>,
    #[serde(default)]
    pub supersedes: Vec<String>,
    #[serde(default)]
    pub conflicts_with: Vec<String>,
    #[serde(default)]
    pub evidence_node_ids: Vec<String>,
    #[serde(default)]
    pub actor: Option<String>,
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default)]
    pub bump_version: bool,
}

pub fn read_lifecycle_from_metadata(
    metadata: &Map<String, Value>,
    superseded_by: Option<&str>,
    valid_from: Option<&str>,
    valid_to: Option<&str>,
) -> KnowledgeLifecycleMetadata {
    let mut lifecycle = metadata
        .get("lifecycle")
        .and_then(|raw| serde_json::from_value::<KnowledgeLifecycleMetadata>(raw.clone()).ok())
        .unwrap_or_default();

    let clean_sup = superseded_by.map(str::trim).filter(|s| !s.is_empty());
    if lifecycle.superseded_by.is_none() {
        if let Some(sup) = clean_sup {
            lifecycle.superseded_by = Some(sup.to_string());
        }
    }
    if lifecycle.superseded_by.is_some() && lifecycle.state == KnowledgeLifecycleState::Active {
        lifecycle.state = KnowledgeLifecycleState::Superseded;
    }
    if lifecycle.effective_from.is_none() {
        lifecycle.effective_from = valid_from
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string);
    }
    if lifecycle.valid_until.is_none() {
        lifecycle.valid_until = valid_to
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string);
    }
    if lifecycle.version == 0 {
        lifecycle.version = 1;
    }
    lifecycle
}

fn push_unique(target: &mut Vec<String>, incoming: &[String]) {
    for item in incoming {
        let trimmed = item.trim();
        if !trimmed.is_empty() && !target.iter().any(|existing| existing == trimmed) {
            target.push(trimmed.to_string());
        }
    }
}

pub(crate) fn update_lifecycle_in_txn(
    writer: &GraphWriter,
    txn: &Transaction<'_>,
    node_id: &str,
    update: &KnowledgeLifecycleUpdate,
) -> Result<Option<KnowledgeLifecycleMetadata>, CoreError> {
    let node_id = node_id.trim();
    if node_id.is_empty() {
        return Ok(None);
    }
    let legacy_row: Option<String> = txn
        .query_row(
            "SELECT metadata_json FROM nodes WHERE id=?",
            rusqlite::params![node_id],
            |row| row.get(0),
        )
        .ok();
    let v2_row: Option<V2LifecycleRow> = txn
        .query_row(
            "SELECT attrs, superseded_by, valid_from, valid_to FROM nodes_v2 WHERE id=?",
            rusqlite::params![node_id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .ok();

    if legacy_row.is_none() && v2_row.is_none() {
        return Ok(None);
    }

    let mut legacy_meta = safe_loads(legacy_row.as_deref());
    let (v2_attrs_raw, v2_sup, v2_from, v2_to) = v2_row.clone().unwrap_or((None, None, None, None));
    let mut v2_attrs = safe_loads(v2_attrs_raw.as_deref());

    let mut lifecycle = if legacy_meta.contains_key("lifecycle") {
        read_lifecycle_from_metadata(
            &legacy_meta,
            v2_sup.as_deref(),
            v2_from.as_deref(),
            v2_to.as_deref(),
        )
    } else {
        read_lifecycle_from_metadata(
            &v2_attrs,
            v2_sup.as_deref(),
            v2_from.as_deref(),
            v2_to.as_deref(),
        )
    };

    let now = writer.clock.now_iso();
    let previous_state = lifecycle.state;

    if let Some(state) = update.state {
        lifecycle.state = state;
    }
    if let Some(sup) = update
        .superseded_by
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        lifecycle.superseded_by = Some(sup.to_string());
        if update.state.is_none() {
            lifecycle.state = KnowledgeLifecycleState::Superseded;
        }
    }
    if let Some(effective_from) = update
        .effective_from
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        lifecycle.effective_from = Some(effective_from.to_string());
    }
    if let Some(valid_until) = update
        .valid_until
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        lifecycle.valid_until = Some(valid_until.to_string());
    }
    if let Some(deprecated_at) = update
        .deprecated_at
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        lifecycle.deprecated_at = Some(deprecated_at.to_string());
    } else if matches!(
        lifecycle.state,
        KnowledgeLifecycleState::Deprecated | KnowledgeLifecycleState::Superseded
    ) && lifecycle.deprecated_at.is_none()
    {
        lifecycle.deprecated_at = Some(now.clone());
    }
    if let Some(authority) = update
        .authority
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        lifecycle.authority = Some(authority.to_string());
    }
    if let Some(approver) = update
        .approver
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        lifecycle.approver = Some(approver.to_string());
    }
    push_unique(&mut lifecycle.supersedes, &update.supersedes);
    push_unique(&mut lifecycle.conflicts_with, &update.conflicts_with);
    push_unique(&mut lifecycle.evidence_node_ids, &update.evidence_node_ids);

    if let Some(explicit_version) = update.version.filter(|v| *v > 0) {
        lifecycle.version = explicit_version;
    } else if update.bump_version || lifecycle.state != previous_state {
        lifecycle.version = lifecycle.version.saturating_add(1).max(1);
    }

    if lifecycle.state != previous_state
        || update.reason.is_some()
        || update.actor.is_some()
        || update.bump_version
        || lifecycle.history.is_empty()
    {
        lifecycle.history.push(LifecycleHistoryEntry {
            timestamp: now.clone(),
            state: lifecycle.state,
            actor: update.actor.clone().or_else(|| update.approver.clone()),
            reason: update.reason.clone(),
            version: lifecycle.version,
        });
    }

    let lifecycle_val = serde_json::to_value(&lifecycle).unwrap_or_else(|_| json!({}));
    if legacy_row.is_some() {
        legacy_meta.insert("lifecycle".into(), lifecycle_val.clone());
        txn.execute(
            "UPDATE nodes SET metadata_json=?, updated_at=? WHERE id=?",
            rusqlite::params![json_of(&legacy_meta), now, node_id],
        )?;
    }
    if v2_row.is_some() {
        v2_attrs.insert("lifecycle".into(), lifecycle_val);
        txn.execute(
            "UPDATE nodes_v2 SET attrs=?, superseded_by=?, valid_from=?, valid_to=?, updated_at=? WHERE id=?",
            rusqlite::params![
                json_of(&v2_attrs),
                lifecycle.superseded_by,
                lifecycle.effective_from,
                lifecycle.valid_until,
                now,
                node_id,
            ],
        )?;
    }

    for old_id in &update.supersedes {
        let old_id = old_id.trim();
        if old_id.is_empty() || old_id == node_id {
            continue;
        }
        if writer.node_exists(txn, old_id)? {
            writer.upsert_edge(
                txn,
                &EdgeSpec {
                    from_node: node_id.to_string(),
                    to_node: old_id.to_string(),
                    edge_type: "SUPERSEDES".into(),
                    weight: 1.0,
                    metadata: Map::new(),
                    legacy_label: None,
                },
            )?;
        }
    }
    for conflict_id in &update.conflicts_with {
        let conflict_id = conflict_id.trim();
        if conflict_id.is_empty() || conflict_id == node_id {
            continue;
        }
        if writer.node_exists(txn, conflict_id)? {
            writer.upsert_edge(
                txn,
                &EdgeSpec {
                    from_node: node_id.to_string(),
                    to_node: conflict_id.to_string(),
                    edge_type: "CONFLICTS_WITH".into(),
                    weight: 1.0,
                    metadata: Map::new(),
                    legacy_label: None,
                },
            )?;
        }
    }

    Ok(Some(lifecycle))
}

impl GraphWriter {
    pub fn get_node_lifecycle(
        &self,
        node_id: &str,
    ) -> Result<Option<KnowledgeLifecycleMetadata>, CoreError> {
        let node_id = node_id.trim().to_string();
        if node_id.is_empty() {
            return Ok(None);
        }
        self.store.with_read_conn(|conn| {
            let legacy: Option<String> = conn
                .query_row(
                    "SELECT metadata_json FROM nodes WHERE id=?",
                    rusqlite::params![node_id],
                    |row| row.get(0),
                )
                .ok();
            let v2: Option<V2LifecycleRow> = conn
                .query_row(
                    "SELECT attrs, superseded_by, valid_from, valid_to FROM nodes_v2 WHERE id=?",
                    rusqlite::params![node_id],
                    |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
                )
                .ok();
            if legacy.is_none() && v2.is_none() {
                return Ok(None);
            }
            let legacy_meta = safe_loads(legacy.as_deref());
            let (attrs_raw, sup, from, to) = v2.unwrap_or((None, None, None, None));
            let v2_meta = safe_loads(attrs_raw.as_deref());
            let meta =
                if legacy_meta.contains_key("lifecycle") || !v2_meta.contains_key("lifecycle") {
                    legacy_meta
                } else {
                    v2_meta
                };
            Ok(Some(read_lifecycle_from_metadata(
                &meta,
                sup.as_deref(),
                from.as_deref(),
                to.as_deref(),
            )))
        })
    }

    pub fn set_node_lifecycle(
        &self,
        node_id: &str,
        update: &KnowledgeLifecycleUpdate,
    ) -> Result<KnowledgeLifecycleMetadata, CoreError> {
        let node_id = node_id.trim().to_string();
        if node_id.is_empty() {
            return Err(CoreError::InvalidRequest("node_id required".to_string()));
        }
        let updated = self
            .store
            .with_write_txn(|txn| update_lifecycle_in_txn(self, txn, &node_id, update))?;
        updated.ok_or_else(|| CoreError::InvalidRequest(format!("node not found: {node_id}")))
    }

    pub fn mark_disputed(
        &self,
        node_id: &str,
        conflicting_node_id: Option<&str>,
        actor: Option<&str>,
        reason: Option<&str>,
    ) -> Result<KnowledgeLifecycleMetadata, CoreError> {
        let mut conflicts = Vec::new();
        if let Some(other) = conflicting_node_id.map(str::trim).filter(|s| !s.is_empty()) {
            conflicts.push(other.to_string());
        }
        let update = KnowledgeLifecycleUpdate {
            state: Some(KnowledgeLifecycleState::Disputed),
            conflicts_with: conflicts,
            actor: actor.map(str::to_string),
            reason: reason.map(str::to_string),
            ..Default::default()
        };
        let result = self.set_node_lifecycle(node_id, &update)?;
        if let Some(other) = conflicting_node_id
            .map(str::trim)
            .filter(|s| !s.is_empty() && *s != node_id.trim())
        {
            let _ = self.set_node_lifecycle(
                other,
                &KnowledgeLifecycleUpdate {
                    state: Some(KnowledgeLifecycleState::Disputed),
                    conflicts_with: vec![node_id.trim().to_string()],
                    actor: actor.map(str::to_string),
                    reason: reason.map(str::to_string),
                    ..Default::default()
                },
            );
        }
        Ok(result)
    }

    pub fn verify_node(
        &self,
        node_id: &str,
        approver: &str,
        authority: Option<&str>,
    ) -> Result<KnowledgeLifecycleMetadata, CoreError> {
        let approver = approver.trim();
        if approver.is_empty() {
            return Err(CoreError::InvalidRequest("approver required".to_string()));
        }
        self.set_node_lifecycle(
            node_id,
            &KnowledgeLifecycleUpdate {
                state: Some(KnowledgeLifecycleState::Active),
                approver: Some(approver.to_string()),
                authority: authority.map(str::to_string),
                actor: Some(approver.to_string()),
                reason: Some("verified".to_string()),
                ..Default::default()
            },
        )
    }
}
