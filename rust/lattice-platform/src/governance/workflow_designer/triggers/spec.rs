use serde_json::{json, Map, Value};

use super::super::time::utc_parts;
use super::super::DEFAULT_WORKSPACE_ID;

pub const MIN_INTERVAL_SECONDS: f64 = 60.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TriggerKind {
    Interval,
    BrainEvent,
}

impl TriggerKind {
    pub fn as_str(self) -> &'static str {
        match self {
            TriggerKind::Interval => "interval",
            TriggerKind::BrainEvent => "brain_event",
        }
    }

    pub fn ui_kind(self) -> &'static str {
        match self {
            TriggerKind::Interval => "schedule",
            TriggerKind::BrainEvent => "brain_event",
        }
    }
}

#[derive(Debug, Clone)]
pub(crate) struct Automation {
    pub id: String,
    pub name: String,
    pub workspace_id: String,
    pub user_email: Option<String>,
    pub kind: TriggerKind,
    pub interval_seconds: Option<f64>,
    pub enabled: bool,
    pub local_only: bool,
    pub prompt: Option<String>,
    pub unsupported: Vec<String>,
    pub recipe_id: Option<String>,
    pub event_filter: BrainEventFilter,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub(crate) enum OwnerScope {
    #[default]
    SelfOnly,
    Workspace,
}

#[derive(Debug, Clone, Default)]
pub(crate) struct BrainEventFilter {
    pub owner_scope: OwnerScope,
    pub source_types: Vec<String>,
    pub title_contains: Option<String>,
}

const SHARED_VISIBILITIES: &[&str] = &["workspace", "shared", "organization", "org", "public"];

fn normalized(value: Option<&str>) -> Option<String> {
    value
        .map(|text| text.trim().to_lowercase())
        .filter(|text| !text.is_empty())
}

impl BrainEventFilter {
    pub(crate) fn from_config(config: &Map<String, Value>) -> Self {
        let owner_scope = match config.get("owner_scope").and_then(Value::as_str) {
            Some(scope) if scope.trim() == "workspace" => OwnerScope::Workspace,
            _ => OwnerScope::SelfOnly,
        };
        let source_types = config
            .get("source_types")
            .and_then(Value::as_array)
            .map(|rows| {
                rows.iter()
                    .filter_map(|row| normalized(row.as_str()))
                    .collect()
            })
            .unwrap_or_default();
        let title_contains = normalized(config.get("title_contains").and_then(Value::as_str));
        Self {
            owner_scope,
            source_types,
            title_contains,
        }
    }

    pub(crate) fn admits(
        &self,
        workflow_owner: Option<&str>,
        item_owner: Option<&str>,
        visibility: Option<&str>,
        source_type: &str,
        title: &str,
    ) -> bool {
        let workflow_owner = normalized(workflow_owner);
        let item_owner = normalized(item_owner);
        let same_owner = workflow_owner == item_owner;
        let owner_ok = match self.owner_scope {
            OwnerScope::SelfOnly => same_owner,
            OwnerScope::Workspace => {
                same_owner
                    || normalized(visibility)
                        .is_some_and(|value| SHARED_VISIBILITIES.contains(&value.as_str()))
            }
        };
        if !owner_ok {
            return false;
        }
        if !self.source_types.is_empty() {
            let Some(kind) = normalized(Some(source_type)) else {
                return false;
            };
            if !self.source_types.contains(&kind) {
                return false;
            }
        }
        match &self.title_contains {
            Some(needle) => title.to_lowercase().contains(needle.as_str()),
            None => true,
        }
    }
}

pub(crate) fn brain_event_config_errors(node: &Value) -> Vec<String> {
    let config = config_of(Some(node));
    if config.get("trigger").and_then(Value::as_str) != Some("brain_event") {
        return Vec::new();
    }
    let id = node.get("id").and_then(Value::as_str).unwrap_or("");
    let mut errors = Vec::new();
    if let Some(scope) = config.get("owner_scope") {
        if !matches!(scope.as_str(), Some("self" | "workspace")) {
            errors.push(format!(
                "trigger '{id}': owner_scope must be 'self' or 'workspace'"
            ));
        }
    }
    if let Some(sources) = config.get("source_types") {
        let valid = sources.as_array().is_some_and(|rows| {
            rows.iter()
                .all(|row| row.as_str().is_some_and(|text| !text.trim().is_empty()))
        });
        if !valid {
            errors.push(format!(
                "trigger '{id}': source_types must be a list of non-empty strings"
            ));
        }
    }
    if let Some(title) = config.get("title_contains") {
        if !title.is_string() {
            errors.push(format!("trigger '{id}': title_contains must be a string"));
        }
    }
    errors
}

impl Automation {
    pub(crate) fn blocker(&self) -> Option<(&'static str, String)> {
        if self.kind == TriggerKind::Interval && self.interval_seconds.is_none() {
            return Some((
                "invalid_interval",
                format!(
                    "interval_seconds must be a number of at least {MIN_INTERVAL_SECONDS} seconds"
                ),
            ));
        }
        if !self.unsupported.is_empty() {
            return Some((
                "requires_manual_run",
                format!(
                    "unattended runs only draft for review; these steps need a person: {}",
                    self.unsupported.join(", ")
                ),
            ));
        }
        if self.prompt.is_none() {
            return Some((
                "nothing_to_draft",
                "the workflow has no agent step with a prompt to draft from".into(),
            ));
        }
        None
    }
}

fn node_of<'a>(nodes: &'a [Value], kind: &str) -> Option<&'a Value> {
    nodes
        .iter()
        .find(|node| node.get("type").and_then(Value::as_str) == Some(kind))
}

fn config_of(node: Option<&Value>) -> Map<String, Value> {
    node.and_then(|node| node.get("config"))
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default()
}

fn text(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .map(str::to_string)
}

pub(crate) fn automation_of(workflow: &Value) -> Option<Automation> {
    let nodes = workflow.get("nodes").and_then(Value::as_array)?;
    let trigger = config_of(node_of(nodes, "trigger"));
    let kind = match trigger.get("trigger").and_then(Value::as_str) {
        Some("interval") => TriggerKind::Interval,
        Some("brain_event") => TriggerKind::BrainEvent,
        _ => return None,
    };
    let metadata = workflow
        .get("metadata")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let flag = trigger.get("enabled").and_then(Value::as_bool);
    let state = metadata.get("automation_state").and_then(Value::as_str);
    let enabled = match (flag, state) {
        (Some(false), _) => false,
        (_, Some(state)) => state == "enabled",
        (Some(true), None) => true,
        (None, None) => false,
    };
    let agent_node = node_of(nodes, "agent");
    let agent = config_of(agent_node);
    let local_only = match (
        trigger.get("local_only").and_then(Value::as_bool),
        agent.get("local_only").and_then(Value::as_bool),
    ) {
        (_, Some(true)) | (Some(true), _) => true,
        (Some(false), _) | (_, Some(false)) => false,
        (None, None) => true,
    };
    let interval_seconds = trigger
        .get("interval_seconds")
        .and_then(Value::as_f64)
        .filter(|seconds| seconds.is_finite() && *seconds >= MIN_INTERVAL_SECONDS);
    let unsupported = nodes
        .iter()
        .filter_map(|node| node.get("type").and_then(Value::as_str))
        .filter(|kind| !matches!(*kind, "trigger" | "agent" | "output"))
        .map(str::to_string)
        .collect();
    Some(Automation {
        id: text(workflow.get("id"))?,
        name: text(workflow.get("name")).unwrap_or_else(|| "workflow".into()),
        workspace_id: text(workflow.get("workspace_id"))
            .unwrap_or_else(|| DEFAULT_WORKSPACE_ID.to_string()),
        user_email: text(workflow.get("user_email")),
        kind,
        interval_seconds,
        enabled,
        local_only,
        prompt: text(agent.get("prompt")).or_else(|| text(agent.get("goal"))),
        unsupported,
        recipe_id: text(metadata.get("recipe_id")),
        event_filter: BrainEventFilter::from_config(&trigger),
    })
}

pub(crate) fn next_slot_after(anchor: f64, interval: f64, now: f64) -> f64 {
    let steps = ((now - anchor) / interval).floor() + 1.0;
    anchor + steps * interval
}

pub(crate) fn iso_from_epoch(epoch: f64) -> String {
    let (year, month, day, hour, min, sec) = utc_parts(epoch.max(0.0) as u64);
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{min:02}:{sec:02}Z")
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) enum SlotDecision {
    Idle,
    Armed,
    Disarmed,
    Due { slot_at: f64, missed: u64 },
}

impl SlotDecision {
    pub(crate) fn writes(self) -> bool {
        !matches!(self, SlotDecision::Idle)
    }
}

fn epoch(state: &Map<String, Value>, key: &str) -> Option<f64> {
    state.get(key).and_then(Value::as_f64)
}

fn arm(state: &mut Map<String, Value>, interval: Option<f64>, now: f64) {
    state.insert("armed".into(), json!(true));
    state.insert("anchor_epoch".into(), json!(now));
    match interval {
        Some(interval) => {
            state.insert("interval_seconds".into(), json!(interval));
            state.insert("next_run_epoch".into(), json!(now + interval));
            state.insert("next_run_at".into(), json!(iso_from_epoch(now + interval)));
        }
        None => {
            state.insert("interval_seconds".into(), Value::Null);
            state.insert("next_run_epoch".into(), Value::Null);
            state.insert("next_run_at".into(), Value::Null);
        }
    }
}

pub(crate) fn decide_interval(
    state: &mut Map<String, Value>,
    enabled: bool,
    interval: f64,
    now: f64,
) -> SlotDecision {
    let armed = state.get("armed").and_then(Value::as_bool) == Some(true);
    if !enabled {
        if !armed {
            return SlotDecision::Idle;
        }
        state.insert("armed".into(), json!(false));
        state.insert("next_run_epoch".into(), Value::Null);
        state.insert("next_run_at".into(), Value::Null);
        return SlotDecision::Disarmed;
    }
    let same_interval = epoch(state, "interval_seconds") == Some(interval);
    let (Some(next), Some(anchor)) = (epoch(state, "next_run_epoch"), epoch(state, "anchor_epoch"))
    else {
        arm(state, Some(interval), now);
        return SlotDecision::Armed;
    };
    if !armed || !same_interval {
        arm(state, Some(interval), now);
        return SlotDecision::Armed;
    }
    if now < next {
        return SlotDecision::Idle;
    }
    let following = next_slot_after(anchor, interval, now);
    let missed = ((now - next) / interval).floor().max(0.0) as u64;
    state.insert("next_run_epoch".into(), json!(following));
    state.insert("next_run_at".into(), json!(iso_from_epoch(following)));
    claim(state, now, Some(next), missed);
    SlotDecision::Due {
        slot_at: next,
        missed,
    }
}

pub(crate) fn decide_event(
    state: &mut Map<String, Value>,
    enabled: bool,
    now: f64,
) -> SlotDecision {
    let armed = state.get("armed").and_then(Value::as_bool) == Some(true);
    if !enabled {
        if !armed {
            return SlotDecision::Idle;
        }
        state.insert("armed".into(), json!(false));
        return SlotDecision::Disarmed;
    }
    if !armed {
        arm(state, None, now);
    }
    claim(state, now, None, 0);
    SlotDecision::Due {
        slot_at: now,
        missed: 0,
    }
}

pub(crate) fn decide_arming(
    state: &mut Map<String, Value>,
    enabled: bool,
    now: f64,
) -> SlotDecision {
    let armed = state.get("armed").and_then(Value::as_bool) == Some(true);
    match (enabled, armed) {
        (true, false) => {
            arm(state, None, now);
            SlotDecision::Armed
        }
        (false, true) => {
            state.insert("armed".into(), json!(false));
            SlotDecision::Disarmed
        }
        _ => SlotDecision::Idle,
    }
}

fn claim(state: &mut Map<String, Value>, now: f64, slot: Option<f64>, missed: u64) {
    state.insert("last_run_epoch".into(), json!(now));
    state.insert("last_run_at".into(), json!(iso_from_epoch(now)));
    state.insert(
        "last_slot_at".into(),
        slot.map(|slot| json!(iso_from_epoch(slot)))
            .unwrap_or(Value::Null),
    );
    state.insert("missed_slots".into(), json!(missed));
    state.insert("last_status".into(), json!("running"));
    state.insert("skip_reason".into(), Value::Null);
}
