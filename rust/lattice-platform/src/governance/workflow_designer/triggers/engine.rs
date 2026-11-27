use std::collections::HashSet;
use std::future::Future;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Map, Value};
use tokio::task::JoinHandle;

use crate::governance::review_queue::{self, GovernanceState, REVIEW_ITEM_CREATED_EVENT};

use super::super::store::WorkflowStore;
use super::super::time::{json_hash, now_epoch, now_iso};
use super::super::DEFAULT_WORKSPACE_ID;
use super::spec::{
    automation_of, decide_arming, decide_event, decide_interval, iso_from_epoch, Automation,
    SlotDecision, TriggerKind,
};

const TRIGGERS_KEY: &str = "workflow_triggers";
const MIN_TICK_SECONDS: f64 = 1.0;

#[derive(Debug, Clone)]
pub struct BrainEventPayload {
    pub node_id: String,
    pub title: String,
    pub source_type: String,
    pub workspace_id: String,
    pub owner: Option<String>,
    pub visibility: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct TriggerTickReport {
    pub fired: usize,
    pub skipped: usize,
    pub armed_changes: usize,
    pub run_ids: Vec<String>,
    pub review_ids: Vec<String>,
}

struct DueAutomation {
    automation: Automation,
    slot_at: f64,
    missed: u64,
}

pub struct TriggerEngine {
    store: Arc<WorkflowStore>,
    governance: Option<GovernanceState>,
    tick_seconds: f64,
    running: AtomicBool,
    in_flight: Mutex<HashSet<String>>,
}

impl TriggerEngine {
    pub(crate) fn new(store: Arc<WorkflowStore>, tick_seconds: f64) -> Self {
        Self {
            store,
            governance: None,
            tick_seconds: tick_seconds.max(MIN_TICK_SECONDS),
            running: AtomicBool::new(true),
            in_flight: Mutex::new(HashSet::new()),
        }
    }

    pub(crate) fn with_governance(mut self, governance: GovernanceState) -> Self {
        self.governance = Some(governance);
        self
    }

    pub fn is_running(&self) -> bool {
        self.running.load(Ordering::SeqCst)
    }

    pub fn set_running(&self, running: bool) {
        self.running.store(running, Ordering::SeqCst);
    }

    pub fn sync_armed(&self, now: f64) -> usize {
        let mut changes = 0usize;
        let _ = self.store.os.mutate(|doc| {
            let workflows = doc
                .get("workflows")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            let mut states = trigger_state_map(doc);
            let mut active_ids = HashSet::new();
            for workflow in &workflows {
                let Some(auto) = automation_of(workflow) else {
                    continue;
                };
                active_ids.insert(auto.id.clone());
                let entry = states
                    .entry(auto.id.clone())
                    .or_insert_with(|| Value::Object(Map::new()));
                let Some(state) = entry.as_object_mut() else {
                    continue;
                };
                let decision = match auto.kind {
                    TriggerKind::Interval => match auto.interval_seconds {
                        Some(interval) if auto.enabled => {
                            let armed = state.get("armed").and_then(Value::as_bool) == Some(true);
                            let same = state.get("interval_seconds").and_then(Value::as_f64)
                                == Some(interval);
                            if !armed
                                || !same
                                || state
                                    .get("next_run_epoch")
                                    .and_then(Value::as_f64)
                                    .is_none()
                            {
                                decide_interval(state, true, interval, now)
                            } else {
                                SlotDecision::Idle
                            }
                        }
                        _ => decide_arming(state, false, now),
                    },
                    TriggerKind::BrainEvent => decide_arming(state, auto.enabled, now),
                };
                if decision.writes() {
                    changes += 1;
                }
            }
            let stale: Vec<String> = states
                .keys()
                .filter(|id| !active_ids.contains(*id))
                .cloned()
                .collect();
            for id in stale {
                states.remove(&id);
                changes += 1;
            }
            if let Some(root) = doc.as_object_mut() {
                root.insert(TRIGGERS_KEY.into(), Value::Object(states));
            }
            Ok(())
        });
        changes
    }

    pub fn armed_rows(&self, workspace_id: Option<&str>) -> Vec<Value> {
        let doc = self.store.os.load_state();
        let workflows = doc
            .get("workflows")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let states = trigger_state_map(&doc);
        let mut rows = Vec::new();
        for workflow in &workflows {
            let Some(auto) = automation_of(workflow) else {
                continue;
            };
            if !auto.enabled {
                continue;
            }
            if let Some(scope) = workspace_id {
                if auto.workspace_id != scope {
                    continue;
                }
            }
            let state = states
                .get(&auto.id)
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            let blocker = auto.blocker();
            rows.push(json!({
                "workflow_id": auto.id,
                "name": auto.name,
                "kind": auto.kind.ui_kind(),
                "trigger": auto.kind.as_str(),
                "workspace_id": auto.workspace_id,
                "enabled": auto.enabled,
                "local_only": auto.local_only,
                "interval_seconds": auto.interval_seconds,
                "next_run_at": state.get("next_run_at").cloned().unwrap_or(Value::Null),
                "last_run_at": state.get("last_run_at").cloned().unwrap_or(Value::Null),
                "last_run_id": state.get("last_run_id").cloned().unwrap_or(Value::Null),
                "last_review_id": state.get("last_review_id").cloned().unwrap_or(Value::Null),
                "last_status": state.get("last_status").cloned().unwrap_or(Value::Null),
                "skip_reason": state.get("skip_reason").cloned().unwrap_or(Value::Null),
                "blocked": blocker.is_some(),
                "blocker_reason": blocker.as_ref().map(|(code, _)| json!(code)).unwrap_or(Value::Null),
                "recipe_id": auto.recipe_id,
            }));
        }
        rows
    }

    pub fn tick_once(&self, now: f64) -> TriggerTickReport {
        let mut report = TriggerTickReport::default();
        let mut due: Vec<DueAutomation> = Vec::new();

        let _ = self.store.os.mutate(|doc| {
            let workflows = doc
                .get("workflows")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            let mut states = trigger_state_map(doc);
            for workflow in &workflows {
                let Some(auto) = automation_of(workflow) else {
                    continue;
                };
                let entry = states
                    .entry(auto.id.clone())
                    .or_insert_with(|| Value::Object(Map::new()));
                let Some(state) = entry.as_object_mut() else {
                    continue;
                };
                match auto.kind {
                    TriggerKind::BrainEvent => {
                        if decide_arming(state, auto.enabled, now).writes() {
                            report.armed_changes += 1;
                        }
                    }
                    TriggerKind::Interval => {
                        if auto.enabled {
                            if let Some((code, detail)) = auto.blocker() {
                                record_skip(state, code, &detail, now);
                                report.skipped += 1;
                                continue;
                            }
                        }
                        let interval = auto
                            .interval_seconds
                            .unwrap_or(super::spec::MIN_INTERVAL_SECONDS);
                        let decision = decide_interval(state, auto.enabled, interval, now);
                        match decision {
                            SlotDecision::Armed | SlotDecision::Disarmed => {
                                report.armed_changes += 1;
                            }
                            SlotDecision::Due { slot_at, missed } => {
                                if self.try_claim_in_flight(&auto.id) {
                                    due.push(DueAutomation {
                                        automation: auto,
                                        slot_at,
                                        missed,
                                    });
                                } else {
                                    record_skip(
                                        state,
                                        "already_running",
                                        "previous trigger run is still active",
                                        now,
                                    );
                                    report.skipped += 1;
                                }
                            }
                            SlotDecision::Idle => {}
                        }
                    }
                }
            }
            if let Some(root) = doc.as_object_mut() {
                root.insert(TRIGGERS_KEY.into(), Value::Object(states));
            }
            Ok(())
        });

        for item in due {
            let (run_id, review_id) = self.execute_due(&item, None);
            self.release_in_flight(&item.automation.id);
            report.fired += 1;
            report.run_ids.push(run_id);
            if let Some(rev) = review_id {
                report.review_ids.push(rev);
            }
        }
        report
    }

    pub fn dispatch_brain_event(&self, event: &BrainEventPayload, now: f64) -> TriggerTickReport {
        let mut report = TriggerTickReport::default();
        let mut due: Vec<DueAutomation> = Vec::new();
        let event_workspace = if event.workspace_id.trim().is_empty() {
            DEFAULT_WORKSPACE_ID
        } else {
            event.workspace_id.as_str()
        };

        let _ = self.store.os.mutate(|doc| {
            let workflows = doc
                .get("workflows")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            let mut states = trigger_state_map(doc);
            for workflow in &workflows {
                let Some(auto) = automation_of(workflow) else {
                    continue;
                };
                if auto.kind != TriggerKind::BrainEvent || auto.workspace_id != event_workspace {
                    continue;
                }
                if !auto.event_filter.admits(
                    auto.user_email.as_deref(),
                    event.owner.as_deref(),
                    event.visibility.as_deref(),
                    &event.source_type,
                    &event.title,
                ) {
                    continue;
                }
                let entry = states
                    .entry(auto.id.clone())
                    .or_insert_with(|| Value::Object(Map::new()));
                let Some(state) = entry.as_object_mut() else {
                    continue;
                };
                if !auto.enabled {
                    if decide_event(state, false, now).writes() {
                        report.armed_changes += 1;
                    }
                    continue;
                }
                if let Some((code, detail)) = auto.blocker() {
                    record_skip(state, code, &detail, now);
                    report.skipped += 1;
                    continue;
                }
                if let SlotDecision::Due { slot_at, missed } = decide_event(state, true, now) {
                    if self.try_claim_in_flight(&auto.id) {
                        due.push(DueAutomation {
                            automation: auto,
                            slot_at,
                            missed,
                        });
                    } else {
                        record_skip(
                            state,
                            "already_running",
                            "previous trigger run is still active",
                            now,
                        );
                        report.skipped += 1;
                    }
                }
            }
            if let Some(root) = doc.as_object_mut() {
                root.insert(TRIGGERS_KEY.into(), Value::Object(states));
            }
            Ok(())
        });

        for item in due {
            let (run_id, review_id) = self.execute_due(&item, Some(event));
            self.release_in_flight(&item.automation.id);
            report.fired += 1;
            report.run_ids.push(run_id);
            if let Some(rev) = review_id {
                report.review_ids.push(rev);
            }
        }
        report
    }

    pub fn spawn_loop<F>(self: Arc<Self>, shutdown: F) -> JoinHandle<()>
    where
        F: Future<Output = ()> + Send + 'static,
    {
        self.set_running(true);
        let period = Duration::from_secs_f64(self.tick_seconds.max(MIN_TICK_SECONDS));
        tokio::spawn(async move {
            let mut interval = tokio::time::interval(period);
            interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
            tokio::pin!(shutdown);
            loop {
                tokio::select! {
                    _ = &mut shutdown => {
                        self.set_running(false);
                        break;
                    }
                    _ = interval.tick() => {
                        let engine = Arc::clone(&self);
                        let _ = tokio::task::spawn_blocking(move || {
                            engine.tick_once(now_epoch())
                        }).await;
                    }
                }
            }
        })
    }

    fn try_claim_in_flight(&self, workflow_id: &str) -> bool {
        let mut guard = self.in_flight.lock().unwrap_or_else(|e| e.into_inner());
        guard.insert(workflow_id.to_string())
    }

    fn release_in_flight(&self, workflow_id: &str) {
        let mut guard = self.in_flight.lock().unwrap_or_else(|e| e.into_inner());
        guard.remove(workflow_id);
    }

    fn execute_due(
        &self,
        due: &DueAutomation,
        event: Option<&BrainEventPayload>,
    ) -> (String, Option<String>) {
        let auto = &due.automation;
        let prompt = auto.prompt.as_deref().unwrap_or("");
        let trigger_mode = match auto.kind {
            TriggerKind::Interval => "scheduled",
            TriggerKind::BrainEvent => "event",
        };
        let event_json = event.map(|ev| {
            json!({
                "node_id": ev.node_id,
                "title": ev.title,
                "source_type": ev.source_type,
                "workspace_id": ev.workspace_id,
            })
        });
        let draft_summary = match event {
            Some(ev) => format!(
                "{} triggered by new {} memory '{}' ({}). Draft ready for review.",
                auto.name, ev.source_type, ev.title, ev.node_id
            ),
            None => format!(
                "{} scheduled draft for slot {} (missed: {}). Draft ready for review.",
                auto.name,
                iso_from_epoch(due.slot_at),
                due.missed
            ),
        };
        let now = now_iso();
        let timeline = vec![
            json!({
                "event": "trigger_fired",
                "trigger": auto.kind.as_str(),
                "slot_at": iso_from_epoch(due.slot_at),
                "missed_slots": due.missed,
                "trigger_event": event_json,
                "status": "ok",
                "timestamp": now,
            }),
            json!({
                "event": "step_completed",
                "node": "draft",
                "type": "agent",
                "status": "ok",
                "detail": "draft staged for review queue",
                "result": {
                    "mode": "draft",
                    "local_only": auto.local_only,
                    "prompt": prompt,
                    "summary": draft_summary,
                },
                "timestamp": now,
            }),
            json!({
                "event": "workflow_finished",
                "status": "ok",
                "timestamp": now,
            }),
        ];
        let outputs = json!({
            "trigger": auto.kind.as_str(),
            "mode": trigger_mode,
            "draft": draft_summary,
            "prompt": prompt,
            "local_only": auto.local_only,
            "trigger_event": event_json,
        });
        let run = self.store.record_workflow_run(
            &auto.id,
            &auto.name,
            "ok",
            timeline,
            outputs,
            auto.user_email.as_deref(),
            &auto.workspace_id,
            None,
        );
        let run_id = run
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let _ = self.store.update_workflow_run(
            &run_id,
            &auto.workspace_id,
            &[
                ("mode", json!(trigger_mode)),
                ("execution_mode", json!("triggered")),
                ("completed_at", json!(now_iso())),
            ],
        );

        let payload = json!({
            "workflow_id": auto.id,
            "last_run_id": run_id,
            "trigger": auto.kind.as_str(),
            "prompt": prompt,
            "draft": draft_summary,
            "local_only": auto.local_only,
            "trigger_event": event_json,
        });
        let provenance = json!({
            "workflow_id": auto.id,
            "run_id": run_id,
            "recipe_id": auto.recipe_id,
            "trigger_kind": auto.kind.as_str(),
            "slot_at": iso_from_epoch(due.slot_at),
        });
        let review_id = self.enqueue_review_draft(auto, &draft_summary, payload, provenance);
        self.record_completion(&auto.id, &run_id, review_id.as_deref(), &draft_summary);
        (run_id, review_id)
    }

    fn enqueue_review_draft(
        &self,
        auto: &Automation,
        summary: &str,
        payload: Value,
        provenance: Value,
    ) -> Option<String> {
        if let Some(gov) = &self.governance {
            let item = review_queue::create_review_item(
                gov,
                &auto.name,
                summary,
                "trigger",
                "automation_draft",
                payload,
                provenance,
                auto.user_email.as_deref(),
                Some(&auto.workspace_id),
            );
            return item.get("id").and_then(Value::as_str).map(str::to_string);
        }
        let now = now_iso();
        let mut created_id: Option<String> = None;
        let _ = self.store.os.mutate(|doc| {
            let existing: Vec<String> = doc
                .get("review_items")
                .and_then(Value::as_array)
                .map(|rows| {
                    rows.iter()
                        .filter_map(|row| row.get("id").and_then(Value::as_str).map(str::to_string))
                        .collect()
                })
                .unwrap_or_default();
            let mut item_id = format!(
                "review-{}",
                &json_hash(&json!([
                    auto.name,
                    "trigger",
                    "automation_draft",
                    auto.user_email,
                    now
                ]))[..16]
            );
            let mut seq = 0u32;
            while existing.iter().any(|id| id == &item_id) {
                seq += 1;
                item_id = format!(
                    "review-{}",
                    &json_hash(&json!([
                        auto.name,
                        "trigger",
                        "automation_draft",
                        auto.user_email,
                        now,
                        seq
                    ]))[..16]
                );
            }
            let item = json!({
                "id": item_id,
                "status": "pending",
                "title": auto.name,
                "summary": summary,
                "source": "trigger",
                "kind": "automation_draft",
                "payload": payload,
                "provenance": provenance,
                "snoozed_until": null,
                "user_email": auto.user_email,
                "workspace_id": auto.workspace_id,
                "created_at": now,
                "updated_at": now,
            });
            if let Some(root) = doc.as_object_mut() {
                let list = root.entry("review_items").or_insert_with(|| json!([]));
                if let Some(rows) = list.as_array_mut() {
                    rows.push(item);
                }
            }
            created_id = Some(item_id);
            Ok(())
        });
        if let Some(id) = &created_id {
            self.store.os.record_timeline_event(
                review_queue::REVIEW_TIMELINE_AREA,
                REVIEW_ITEM_CREATED_EVENT,
                json!({
                    "item_id": id,
                    "action": "create",
                    "status": "pending",
                    "source": "trigger",
                    "workspace_id": auto.workspace_id,
                }),
                Some(&auto.workspace_id),
            );
        }
        created_id
    }

    fn record_completion(
        &self,
        workflow_id: &str,
        run_id: &str,
        review_id: Option<&str>,
        summary: &str,
    ) {
        let now = now_iso();
        let _ = self.store.os.mutate(|doc| {
            let mut states = trigger_state_map(doc);
            if let Some(state) = states.get_mut(workflow_id).and_then(Value::as_object_mut) {
                state.insert("last_status".into(), json!("ok"));
                state.insert("last_run_id".into(), json!(run_id));
                state.insert("last_review_id".into(), json!(review_id));
                state.insert("skip_reason".into(), Value::Null);
            }
            if let Some(root) = doc.as_object_mut() {
                root.insert(TRIGGERS_KEY.into(), Value::Object(states));
            }
            if let Some(workflows) = doc.get_mut("workflows").and_then(Value::as_array_mut) {
                for wf in workflows {
                    if wf.get("id").and_then(Value::as_str) == Some(workflow_id) {
                        if let Some(obj) = wf.as_object_mut() {
                            let meta = obj.entry("metadata").or_insert_with(|| json!({}));
                            if let Some(meta_obj) = meta.as_object_mut() {
                                meta_obj.insert(
                                    "last_execution".into(),
                                    json!({
                                        "mode": "triggered",
                                        "status": "ok",
                                        "summary": summary,
                                        "run_id": run_id,
                                        "review_id": review_id,
                                        "at": now,
                                    }),
                                );
                            }
                        }
                        break;
                    }
                }
            }
            Ok(())
        });
    }
}

fn trigger_state_map(doc: &Value) -> Map<String, Value> {
    doc.get(TRIGGERS_KEY)
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default()
}

fn record_skip(state: &mut Map<String, Value>, code: &str, detail: &str, now: f64) {
    state.insert("last_status".into(), json!("skipped"));
    state.insert("skip_reason".into(), json!(code));
    state.insert("skip_detail".into(), json!(detail));
    state.insert("last_checked_at".into(), json!(iso_from_epoch(now)));
}
