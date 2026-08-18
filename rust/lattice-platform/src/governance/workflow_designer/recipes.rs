//! Built-in Brain automation recipes.

use lattice_auth::pyjson::OrderedMap;
use serde_json::{json, Map, Value};

// ── recipes ──────────────────────────────────────────────────────────────────

#[derive(Clone, Copy)]
pub(crate) struct Recipe {
    pub(crate) id: &'static str,
    pub(crate) name: &'static str,
    pub(crate) summary: &'static str,
    pub(crate) user_value: &'static str,
    pub(crate) cadence: &'static str,
    pub(crate) trigger: &'static str,
    pub(crate) interval_seconds: Option<u64>,
    pub(crate) prompt: &'static str,
    pub(crate) creates: &'static [&'static str],
}

pub(crate) const RECIPES: &[Recipe] = &[
    Recipe {
        id: "daily-memory-digest",
        name: "Daily Memory Digest",
        summary: "Collects the day's new memories into a short review draft.",
        user_value: "Users see what the Brain kept today without searching through chats.",
        cadence: "daily",
        trigger: "interval",
        interval_seconds: Some(86_400),
        prompt: "Review today's new Brain memories and draft a concise digest with important decisions, unresolved questions, and suggested next actions. Do not contact external services.",
        creates: &["memory digest", "decision summary", "next-action suggestions"],
    },
    Recipe {
        id: "weekly-project-review",
        name: "Weekly Project Review",
        summary: "Turns project context into a weekly checkpoint draft.",
        user_value: "Users can restart a project without explaining the week again.",
        cadence: "weekly",
        trigger: "interval",
        interval_seconds: Some(604_800),
        prompt: "Review this workspace's recent memories, workflow runs, and decisions. Draft a project checkpoint with progress, risks, blockers, and next steps. Keep it local and ask before any external action.",
        creates: &["project checkpoint", "risk list", "next-week plan"],
    },
    Recipe {
        id: "follow-up-radar",
        name: "Follow-up Radar",
        summary: "Looks for follow-up candidates when new knowledge enters the Brain.",
        user_value: "Users get gentle reminders for loose ends without a noisy task system.",
        cadence: "when new memory is saved",
        trigger: "brain_event",
        interval_seconds: None,
        prompt: "Inspect the new Brain memory for follow-up signals such as decisions, promises, deadlines, unresolved questions, or 'later' language. Return suggestions only; do not create tasks without approval.",
        creates: &[
            "follow-up suggestions",
            "open-question list",
            "approval-ready task drafts",
        ],
    },
];

pub(crate) fn recipe_as_dict(recipe: &Recipe) -> OrderedMap {
    let mut trigger = OrderedMap::new();
    trigger.insert("trigger", json!(recipe.trigger));
    if let Some(seconds) = recipe.interval_seconds {
        trigger.insert("interval_seconds", json!(seconds));
    }
    let mut consent = OrderedMap::new();
    consent.insert("default_state", json!("draft_disabled"));
    consent.insert("local_only", json!(true));
    consent.insert("external_actions", json!(false));
    consent.insert("requires_user_enable", json!(true));
    consent.insert("review_before_run", json!(true));
    let mut body = OrderedMap::new();
    body.insert("id", json!(recipe.id));
    body.insert("name", json!(recipe.name));
    body.insert("summary", json!(recipe.summary));
    body.insert("user_value", json!(recipe.user_value));
    body.insert("cadence", json!(recipe.cadence));
    body.insert(
        "trigger",
        serde_json::to_value(&trigger).unwrap_or(json!({})),
    );
    body.insert("creates", json!(recipe.creates));
    body.insert(
        "consent",
        serde_json::to_value(&consent).unwrap_or(json!({})),
    );
    body
}

pub(crate) fn build_recipe_workflow(
    recipe: &Recipe,
    enabled: bool,
) -> (String, Vec<Value>, OrderedMap) {
    let mut trigger_config = Map::new();
    trigger_config.insert("trigger".into(), json!(recipe.trigger));
    if let Some(seconds) = recipe.interval_seconds {
        trigger_config.insert("interval_seconds".into(), json!(seconds));
    }
    trigger_config.insert("enabled".into(), json!(enabled));
    trigger_config.insert("review_queue".into(), json!(true));
    trigger_config.insert("consent_required".into(), json!(true));
    trigger_config.insert("local_only".into(), json!(true));
    trigger_config.insert("external_actions".into(), json!(false));
    let trigger_name = if recipe.trigger == "interval" {
        "User-enabled schedule"
    } else {
        "New Brain memory"
    };
    let nodes = vec![
        json!({
            "id": "trigger",
            "type": "trigger",
            "name": trigger_name,
            "config": trigger_config,
            "next": "draft",
        }),
        json!({
            "id": "draft",
            "type": "agent",
            "name": "Draft Brain review",
            "config": {
                "agent": "agent:planner",
                "goal": recipe.prompt,
                "prompt": recipe.prompt,
                "roles": ["researcher", "planner", "executor", "reviewer"],
                "mode": "draft",
                "local_only": true,
                "external_actions": false,
                "requires_review": true,
            },
            "next": "output",
        }),
        json!({
            "id": "output",
            "type": "output",
            "name": "Review before saving",
            "config": {
                "value": "Draft ready for review. Save, edit, or discard it before it becomes durable memory.",
            },
            "next": null,
        }),
    ];
    let mut metadata = OrderedMap::new();
    metadata.insert("created_from", json!("brain_automation_recipe"));
    metadata.insert("recipe_id", json!(recipe.id));
    metadata.insert("recipe_summary", json!(recipe.summary));
    metadata.insert("recipe_user_value", json!(recipe.user_value));
    metadata.insert(
        "automation_state",
        json!(if enabled { "enabled" } else { "draft_disabled" }),
    );
    metadata.insert("local_only", json!(true));
    metadata.insert("external_actions", json!(false));
    metadata.insert("requires_user_enable", json!(!enabled));
    metadata.insert("creates", json!(recipe.creates));
    (recipe.name.to_string(), nodes, metadata)
}

pub(crate) fn find_installed_recipe<'a>(
    workflows: &'a [Value],
    recipe_id: &str,
) -> Option<&'a Value> {
    workflows.iter().find(|workflow| {
        let metadata = workflow.get("metadata").and_then(Value::as_object);
        metadata
            .map(|meta| {
                meta.get("created_from").and_then(Value::as_str) == Some("brain_automation_recipe")
                    && meta.get("recipe_id").and_then(Value::as_str) == Some(recipe_id)
            })
            .unwrap_or(false)
    })
}
