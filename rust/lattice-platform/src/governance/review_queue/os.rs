use serde_json::Value;

use super::DEFAULT_WORKSPACE_ID;

pub(crate) fn scoped(record: &Value, workspace_id: Option<&str>) -> bool {
    let Some(wanted) = workspace_id else {
        return true;
    };
    let stored = record
        .get("workspace_id")
        .and_then(Value::as_str)
        .unwrap_or(DEFAULT_WORKSPACE_ID);
    stored == wanted
}
