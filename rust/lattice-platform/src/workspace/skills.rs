//! The skill registry: what is installed on this machine, and what is offered.
//!
//! Port of `core/workspace_skills.py` plus `remove_skill_directory` from
//! `core/workspace_os_utils.py`.
//!
//! Installed skills are **machine-global**, not per-workspace
//! (`WorkspaceService.SHARED_GLOBAL_AREAS`): the directory is on disk once and
//! every workspace on this install sees the same one. Enable/disable still
//! records a workspace-scoped timeline event, which is why those two routes
//! take a user but no scope.
//!
//! `list_skill_registry` **writes**: it reconciles the registry with what is
//! actually on disk and saves. That is Python's behaviour and it is load-bearing
//! — a skill installed by unzipping a directory only becomes known to the
//! product when someone lists.

use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use super::pyutil::{now_iso, safe_slug};
use super::store::{StoreError, WorkspaceOsStore};

/// `list_skill_registry(skills_dir, marketplace)`.
pub fn list_skill_registry(
    store: &WorkspaceOsStore,
    skills_dir: Option<&Path>,
    marketplace: &[Value],
) -> Result<Value, StoreError> {
    let scanned = skills_dir.map(scan_directory).unwrap_or_default();

    let registry = store.mutate(|state| {
        let registry = state
            .as_object_mut()
            .expect("state document is an object")
            .entry("skill_registry")
            .or_insert_with(|| json!({}));
        if !registry.is_object() {
            *registry = json!({});
        }
        let registry = registry
            .as_object_mut()
            .expect("skill_registry is an object");
        for skill in &scanned {
            let entry = registry
                .entry(skill.name.clone())
                .or_insert_with(|| json!({}));
            if entry.get("enabled").is_none() {
                entry["enabled"] = json!(true);
            }
            entry["name"] = json!(skill.name);
            entry["description"] = json!(skill.description);
            entry["version"] = json!(skill.version);
            if let Some(schema) = &skill.input_schema {
                entry["input_schema"] = schema.clone();
            }
            entry["installed"] = json!(true);
            entry["install_status"] = keep_or(entry.get("install_status"), "ready");
            entry["validation_status"] = json!("ready");
            entry["source"] = keep_or(entry.get("source"), "local");
            entry["path"] = json!(skill.path);
            entry["updated_at"] = match entry.get("updated_at") {
                Some(Value::String(kept)) if !kept.is_empty() => json!(kept),
                _ => json!(now_iso()),
            };
        }
        Ok(Value::Object(registry.clone()))
    })?;

    let installed: Vec<Value> = scanned
        .iter()
        .filter_map(|skill| registry.get(&skill.name).cloned())
        .collect();
    let available: Vec<Value> = marketplace
        .iter()
        .filter_map(|item| project_marketplace(item, &registry))
        .collect();

    Ok(json!({
        "installed": installed,
        "available": available,
        "registry": registry,
        "total_installed": installed.len(),
        "total_available": available.len(),
    }))
}

fn keep_or(existing: Option<&Value>, fallback: &str) -> Value {
    match existing {
        Some(Value::String(kept)) if !kept.is_empty() => json!(kept),
        _ => json!(fallback),
    }
}

/// One installed skill directory, as the scan reads it.
#[derive(Debug, Clone, PartialEq)]
pub struct InstalledSkill {
    pub name: String,
    pub description: String,
    pub version: String,
    pub path: String,
    /// JSON-Schema object for the skill's `input`, when `schema.json` parsed.
    pub input_schema: Option<Value>,
    pub body: String,
}

/// `for skill_dir in sorted(skills_dir.iterdir())` — a directory with a
/// `SKILL.md` is a skill; anything else is ignored without comment.
pub fn scan_installed_skills(skills_dir: &Path) -> Vec<InstalledSkill> {
    scan_directory(skills_dir)
}

/// Turn a skill's `schema.json` `input` field into a JSON Schema object.
///
/// Skills in this repo use a compact `{required, optional, properties}` shape.
/// Missing or already-standard objects fall back to an empty object schema.
pub fn json_schema_from_skill_input(input: Option<&Value>) -> Value {
    let Some(input) = input else {
        return json!({"type": "object", "properties": {}});
    };
    if input.get("type").and_then(Value::as_str) == Some("object") {
        return input.clone();
    }
    if let Some(properties) = input.get("properties") {
        let required = input.get("required").cloned().unwrap_or(json!([]));
        return json!({
            "type": "object",
            "properties": properties,
            "required": required,
        });
    }
    json!({"type": "object", "properties": {}})
}

/// `for skill_dir in sorted(skills_dir.iterdir())` — a directory with a
/// `SKILL.md` is a skill; anything else is ignored without comment.
fn scan_directory(skills_dir: &Path) -> Vec<InstalledSkill> {
    let Ok(entries) = std::fs::read_dir(skills_dir) else {
        return Vec::new();
    };
    let mut paths: Vec<PathBuf> = entries.flatten().map(|entry| entry.path()).collect();
    paths.sort();
    paths
        .into_iter()
        .filter_map(|path| {
            if !path.is_dir() {
                return None;
            }
            let manifest = path.join("SKILL.md");
            if !manifest.is_file() {
                return None;
            }
            let body = std::fs::read_to_string(&manifest).unwrap_or_default();
            let md_description = body.lines().find_map(|line| {
                line.strip_prefix("description:")
                    .map(|value| value.trim().to_string())
                    .filter(|value| !value.is_empty())
            });
            let schema = std::fs::read_to_string(path.join("schema.json"))
                .ok()
                .and_then(|text| serde_json::from_str::<Value>(&text).ok());
            let version = schema
                .as_ref()
                .and_then(|schema| {
                    schema.get("version").filter(|value| !value.is_null()).map(
                        |value| match value {
                            Value::String(text) => text.clone(),
                            other => other.to_string(),
                        },
                    )
                })
                .filter(|version| !version.is_empty())
                .unwrap_or_else(|| "local".into());
            let schema_description = schema.as_ref().and_then(|schema| {
                schema
                    .get("description")
                    .and_then(Value::as_str)
                    .map(str::trim)
                    .filter(|value| !value.is_empty())
                    .map(str::to_string)
            });
            let input_schema = schema
                .as_ref()
                .map(|schema| json_schema_from_skill_input(schema.get("input")));
            let description = md_description.or(schema_description).unwrap_or_default();
            Some(InstalledSkill {
                name: path.file_name()?.to_string_lossy().into_owned(),
                description,
                version,
                path: path.to_string_lossy().into_owned(),
                input_schema,
                body,
            })
        })
        .collect()
}

/// A marketplace row, overlaid with whatever this install knows about it.
fn project_marketplace(item: &Value, registry: &Value) -> Option<Value> {
    let name = item
        .get("skill")
        .and_then(Value::as_str)
        .or_else(|| item.get("name").and_then(Value::as_str))
        .filter(|value| !value.is_empty())?;
    let state = registry.get(name).cloned().unwrap_or_else(|| json!({}));
    let installed = state
        .get("installed")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let mut merged = match item {
        Value::Object(map) => map.clone(),
        _ => Map::new(),
    };
    merged.insert(
        "enabled".into(),
        json!(state
            .get("enabled")
            .and_then(Value::as_bool)
            .unwrap_or(true)),
    );
    merged.insert("installed".into(), json!(installed));
    merged.insert(
        "install_status".into(),
        first_text(
            &[state.get("install_status")],
            if installed { "ready" } else { "available" },
        ),
    );
    merged.insert(
        "validation_status".into(),
        first_text(
            &[
                state.get("validation_status"),
                item.get("validation_status"),
            ],
            if installed { "ready" } else { "not_installed" },
        ),
    );
    merged.insert(
        "source".into(),
        first_text(
            &[state.get("source"), item.get("source"), item.get("plugin")],
            "marketplace",
        ),
    );
    merged.insert(
        "version".into(),
        first_text(&[state.get("version"), item.get("version")], "remote"),
    );
    Some(Value::Object(merged))
}

/// The `a or b or fallback` chain, with Python's truthiness for strings.
fn first_text(candidates: &[Option<&Value>], fallback: &str) -> Value {
    for candidate in candidates.iter().flatten() {
        if let Value::String(text) = candidate {
            if !text.is_empty() {
                return json!(text);
            }
        }
    }
    json!(fallback)
}

/// `set_skill_enabled`.
pub fn set_enabled(
    store: &WorkspaceOsStore,
    skill: &str,
    enabled: bool,
) -> Result<Value, StoreError> {
    let entry = store.mutate(|state| {
        Ok(update_entry(state, skill, |entry| {
            entry["enabled"] = json!(enabled);
            entry["updated_at"] = json!(now_iso());
        }))
    })?;
    store.record_timeline_event(
        "skills",
        if enabled {
            "skill_enabled"
        } else {
            "skill_disabled"
        },
        json!({"skill": skill}),
        None,
    );
    Ok(entry)
}

/// `mark_skill_installed`.
pub fn mark_installed(
    store: &WorkspaceOsStore,
    skill: &str,
    version: &str,
    metadata: &Value,
) -> Result<Value, StoreError> {
    let metadata = metadata.clone();
    let entry = store.mutate(|state| {
        Ok(update_entry(state, skill, |entry| {
            let source = first_text(
                &[metadata.get("source"), entry.get("source")],
                "marketplace",
            );
            let kept_metadata = entry.get("metadata").cloned();
            entry["installed"] = json!(true);
            entry["enabled"] = json!(entry
                .get("enabled")
                .and_then(Value::as_bool)
                .unwrap_or(true));
            entry["version"] = json!(version);
            entry["install_status"] = json!("ready");
            entry["validation_status"] = json!("ready");
            entry["source"] = source;
            entry["metadata"] = match (&metadata, kept_metadata) {
                (Value::Object(map), _) if !map.is_empty() => metadata.clone(),
                (_, Some(Value::Object(kept))) if !kept.is_empty() => Value::Object(kept),
                _ => json!({}),
            };
            entry["updated_at"] = json!(now_iso());
        }))
    })?;
    store.record_timeline_event(
        "skills",
        "skill_installed",
        json!({"skill": skill, "version": version}),
        None,
    );
    Ok(entry)
}

/// `mark_skill_uninstalled`.
pub fn mark_uninstalled(store: &WorkspaceOsStore, skill: &str) -> Result<Value, StoreError> {
    let entry = store.mutate(|state| {
        Ok(update_entry(state, skill, |entry| {
            entry["installed"] = json!(false);
            entry["enabled"] = json!(false);
            entry["updated_at"] = json!(now_iso());
        }))
    })?;
    store.record_timeline_event("skills", "skill_uninstalled", json!({"skill": skill}), None);
    Ok(entry)
}

/// `state.setdefault("skill_registry", {}).setdefault(skill, {"name": skill})`.
fn update_entry(state: &mut Value, skill: &str, body: impl FnOnce(&mut Value)) -> Value {
    let registry = state
        .as_object_mut()
        .expect("state document is an object")
        .entry("skill_registry")
        .or_insert_with(|| json!({}));
    if !registry.is_object() {
        *registry = json!({});
    }
    let entry = registry
        .as_object_mut()
        .expect("skill_registry is an object")
        .entry(skill.to_string())
        .or_insert_with(|| json!({"name": skill}));
    body(entry);
    entry.clone()
}

/// `remove_skill_directory` — delete an installed skill's directory.
///
/// The slug + prefix check is the containment guard: a `skill` of `../..`
/// slugs to `..-..` and then has to still resolve inside the skills root.
pub fn remove_skill_directory(skills_dir: &Path, skill: &str) -> Result<Value, StoreError> {
    let safe_name = safe_slug(skill);
    let root = skills_dir
        .canonicalize()
        .unwrap_or_else(|_| skills_dir.to_path_buf());
    // Python's `Path.resolve()` still produces a path under the resolved
    // parent when the target does not exist; `canonicalize` does not. Walk
    // the slug's components (so a leftover `..` cannot escape) and fall
    // back to `root / slug` when the directory is already gone.
    let resolved = resolve_skill_target(&root, &safe_name);
    if !resolved.starts_with(&root) {
        return Err(StoreError::Value("invalid skill path".into()));
    }
    if !resolved.is_dir() {
        return Err(StoreError::NotFound(skill.to_string()));
    }
    std::fs::remove_dir_all(&resolved)
        .map_err(|error| StoreError::Value(format!("could not remove skill: {error}")))?;
    Ok(json!({
        "status": "ok",
        "skill": safe_name,
        "removed_path": resolved.to_string_lossy(),
    }))
}

/// `(skills_dir / slug).resolve()` — parent is canonical, missing leaf stays.
fn resolve_skill_target(root: &Path, slug: &str) -> PathBuf {
    let target = root.join(slug);
    if let Ok(canonical) = target.canonicalize() {
        return canonical;
    }
    let mut out = root.to_path_buf();
    for component in Path::new(slug).components() {
        match component {
            std::path::Component::ParentDir => {
                out.pop();
            }
            std::path::Component::CurDir => {}
            std::path::Component::Normal(part) => out.push(part),
            other => out.push(other),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> (tempfile::TempDir, WorkspaceOsStore) {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = WorkspaceOsStore::open(dir.path());
        (dir, store)
    }

    fn write_skill(root: &Path, name: &str, description: &str, version: Option<&str>) {
        let dir = root.join(name);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("SKILL.md"),
            format!("name: {name}\ndescription: {description}\n"),
        )
        .unwrap();
        if let Some(version) = version {
            std::fs::write(
                dir.join("schema.json"),
                format!("{{\"version\": \"{version}\"}}"),
            )
            .unwrap();
        }
    }

    #[test]
    fn an_empty_install_lists_nothing_and_offers_the_marketplace() {
        let (_dir, store) = store();
        let remote = json!({
            "plugin": "fixture-remote", "skill": "fixture-remote-skill",
            "description": "Canned remote skill.",
        });
        let listing = list_skill_registry(&store, None, &[remote]).unwrap();
        assert_eq!(listing["installed"], json!([]));
        assert_eq!(listing["registry"], json!({}));
        assert_eq!(listing["total_installed"], json!(0));
        assert_eq!(listing["total_available"], json!(1));
        let offered = &listing["available"][0];
        assert_eq!(offered["enabled"], json!(true));
        assert_eq!(offered["installed"], json!(false));
        assert_eq!(offered["install_status"], json!("available"));
        assert_eq!(offered["validation_status"], json!("not_installed"));
        assert_eq!(offered["source"], json!("fixture-remote"));
        assert_eq!(offered["version"], json!("remote"));
    }

    #[test]
    fn a_marketplace_row_without_a_name_is_dropped() {
        let (_dir, store) = store();
        let listing =
            list_skill_registry(&store, None, &[json!({"description": "x"}), json!(3)]).unwrap();
        assert_eq!(listing["total_available"], json!(0));
    }

    #[test]
    fn a_schema_json_is_parsed_and_a_missing_one_is_tolerated() {
        let skills = tempfile::tempdir().unwrap();
        write_skill(skills.path(), "with-schema", "from md", Some("1.0.0"));
        std::fs::write(
            skills.path().join("with-schema").join("schema.json"),
            r#"{"version":"1.0.0","description":"from schema","input":{"required":["target"],"properties":{"target":{"type":"string"}}}}"#,
        )
        .unwrap();
        write_skill(skills.path(), "no-schema", "only md", None);
        std::fs::create_dir_all(skills.path().join("bad-schema")).unwrap();
        std::fs::write(
            skills.path().join("bad-schema").join("SKILL.md"),
            "description: broken\n",
        )
        .unwrap();
        std::fs::write(
            skills.path().join("bad-schema").join("schema.json"),
            "not json",
        )
        .unwrap();

        let scanned = scan_installed_skills(skills.path());
        let with = scanned.iter().find(|s| s.name == "with-schema").unwrap();
        assert_eq!(with.description, "from md");
        assert_eq!(
            with.input_schema.as_ref().unwrap()["properties"]["target"]["type"],
            json!("string")
        );
        assert_eq!(
            with.input_schema.as_ref().unwrap()["required"],
            json!(["target"])
        );
        let none = scanned.iter().find(|s| s.name == "no-schema").unwrap();
        assert!(none.input_schema.is_none());
        let bad = scanned.iter().find(|s| s.name == "bad-schema").unwrap();
        assert!(bad.input_schema.is_none());
        assert_eq!(bad.description, "broken");
    }

    #[test]
    fn a_directory_scan_registers_what_is_on_disk() {
        let (_dir, store) = store();
        let skills = tempfile::tempdir().unwrap();
        write_skill(skills.path(), "b-skill", "second", Some("2.0.0"));
        write_skill(skills.path(), "a-skill", "first", None);
        std::fs::write(skills.path().join("loose.txt"), b"x").unwrap();
        std::fs::create_dir_all(skills.path().join("no-manifest")).unwrap();

        let listing = list_skill_registry(&store, Some(skills.path()), &[]).unwrap();
        let installed = listing["installed"].as_array().unwrap();
        assert_eq!(installed.len(), 2);
        // Sorted by path, so `a-skill` comes first.
        assert_eq!(installed[0]["name"], json!("a-skill"));
        assert_eq!(installed[0]["description"], json!("first"));
        assert_eq!(installed[0]["version"], json!("local"));
        assert_eq!(installed[0]["source"], json!("local"));
        assert_eq!(installed[0]["validation_status"], json!("ready"));
        assert_eq!(installed[1]["version"], json!("2.0.0"));
        // The scan persisted: a second call sees the same registry.
        assert_eq!(
            store.load_state()["skill_registry"]["a-skill"]["installed"],
            json!(true)
        );
    }

    #[test]
    fn an_installed_skill_overlays_its_marketplace_row() {
        let (_dir, store) = store();
        let skills = tempfile::tempdir().unwrap();
        write_skill(skills.path(), "shared", "on disk", Some("1.2.3"));
        let listing = list_skill_registry(
            &store,
            Some(skills.path()),
            &[json!({"skill": "shared", "version": "9.9.9"})],
        )
        .unwrap();
        let offered = &listing["available"][0];
        assert_eq!(offered["installed"], json!(true));
        assert_eq!(offered["install_status"], json!("ready"));
        assert_eq!(offered["version"], json!("1.2.3"));
        assert_eq!(offered["source"], json!("local"));
    }

    #[test]
    fn enable_disable_install_and_uninstall_move_the_registry_entry() {
        let (_dir, store) = store();
        let enabled = set_enabled(&store, "alpha", true).unwrap();
        assert_eq!(enabled["name"], json!("alpha"));
        assert_eq!(enabled["enabled"], json!(true));
        assert_eq!(
            set_enabled(&store, "alpha", false).unwrap()["enabled"],
            json!(false)
        );

        let installed = mark_installed(
            &store,
            "alpha",
            "local",
            &json!({"install_result": {"status": "recorded"}}),
        )
        .unwrap();
        assert_eq!(installed["installed"], json!(true));
        assert_eq!(installed["version"], json!("local"));
        assert_eq!(installed["install_status"], json!("ready"));
        assert_eq!(installed["source"], json!("marketplace"));
        assert_eq!(
            installed["metadata"]["install_result"]["status"],
            json!("recorded")
        );
        // `enabled` is preserved through an install (it was false above).
        assert_eq!(installed["enabled"], json!(false));

        let removed = mark_uninstalled(&store, "alpha").unwrap();
        assert_eq!(removed["installed"], json!(false));
        assert_eq!(removed["enabled"], json!(false));

        let events: Vec<String> = store.load_state()["timeline"]
            .as_array()
            .unwrap()
            .iter()
            .map(|event| event["event_type"].as_str().unwrap().to_string())
            .collect();
        assert_eq!(
            events,
            vec![
                "skill_enabled",
                "skill_disabled",
                "skill_installed",
                "skill_uninstalled"
            ]
        );
    }

    #[test]
    fn install_metadata_can_name_the_source() {
        let (_dir, store) = store();
        let entry = mark_installed(&store, "beta", "1.0", &json!({"source": "plugin-x"})).unwrap();
        assert_eq!(entry["source"], json!("plugin-x"));
        // An empty metadata keeps whatever was there.
        let again = mark_installed(&store, "beta", "1.1", &json!({})).unwrap();
        assert_eq!(again["source"], json!("plugin-x"));
        assert_eq!(again["metadata"], json!({"source": "plugin-x"}));
    }

    #[test]
    fn removing_a_directory_is_contained_and_reports_not_found() {
        let skills = tempfile::tempdir().unwrap();
        write_skill(skills.path(), "gamma", "x", None);
        let removed = remove_skill_directory(skills.path(), "gamma").unwrap();
        assert_eq!(removed["status"], json!("ok"));
        assert_eq!(removed["skill"], json!("gamma"));
        assert!(!skills.path().join("gamma").exists());

        assert_eq!(
            remove_skill_directory(skills.path(), "gamma").unwrap_err(),
            StoreError::NotFound("gamma".into())
        );
        // A traversal attempt slugs into a sibling name and is still not found
        // rather than escaping the root.
        assert_eq!(
            remove_skill_directory(skills.path(), "../../etc").unwrap_err(),
            StoreError::NotFound("../../etc".into())
        );
    }
}
