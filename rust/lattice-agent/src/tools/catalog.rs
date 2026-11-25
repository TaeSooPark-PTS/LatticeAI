use std::collections::BTreeSet;

use serde_json::{Map, Value};

use super::host::{CallScope, ToolFuture};

#[path = "catalog_args.rs"]
mod catalog_args;
pub use catalog_args::{
    adopt_named_args, arg_synonyms, coerce_and_unwrap_args, defaulted_arg, entry_for,
    fill_documented_defaults, is_search_arg, missing_required,
};
use catalog_args::{native_args, native_summary};

pub const MCP_PREFIX: &str = "mcp.";

pub const SKILL_PREFIX: &str = "skill.";

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum EntryKind {
    Native,
    Mcp,
    Skill,
}

impl EntryKind {
    pub fn label(self) -> &'static str {
        match self {
            EntryKind::Native => "tool",
            EntryKind::Mcp => "mcp",
            EntryKind::Skill => "skill",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArgKind {
    Line,
    Text,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArgSpec {
    pub name: String,
    pub kind: ArgKind,
    pub hint: String,
    pub default: Option<String>,
}

impl ArgSpec {
    pub fn line(name: &str, hint: &str) -> Self {
        Self {
            name: name.to_string(),
            kind: ArgKind::Line,
            hint: hint.to_string(),
            default: None,
        }
    }

    pub fn line_defaulting(name: &str, hint: &str, default: &str) -> Self {
        Self {
            default: Some(default.to_string()),
            ..Self::line(name, hint)
        }
    }

    pub fn text(name: &str, hint: &str) -> Self {
        Self {
            name: name.to_string(),
            kind: ArgKind::Text,
            hint: hint.to_string(),
            default: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CatalogEntry {
    pub name: String,
    pub kind: EntryKind,
    pub summary: String,
    pub required: Vec<ArgSpec>,
}

impl CatalogEntry {
    pub fn signature(&self) -> String {
        if self.required.is_empty() {
            return self.name.clone();
        }
        let args: Vec<&str> = self
            .required
            .iter()
            .map(|spec| spec.name.as_str())
            .collect();
        format!("{}{{{}}}", self.name, args.join(", "))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Resolved<'a> {
    Native(&'a str),
    External(&'a str),
}

pub fn resolve<'a>(name: &'a str, native_known: impl Fn(&str) -> bool) -> Resolved<'a> {
    if let Some(bare) = name.strip_prefix(MCP_PREFIX) {
        if native_known(bare) {
            return Resolved::Native(bare);
        }
        return Resolved::External(name);
    }
    if name.starts_with(SKILL_PREFIX) {
        return Resolved::External(name);
    }
    Resolved::Native(name)
}

pub fn is_external_name(name: &str) -> bool {
    name.starts_with(MCP_PREFIX) || name.starts_with(SKILL_PREFIX)
}

pub trait ToolCatalog: std::fmt::Debug + Send + Sync {
    fn entries(&self) -> Vec<CatalogEntry>;

    fn execute<'a>(
        &'a self,
        name: &'a str,
        args: &'a Map<String, Value>,
        scope: &'a CallScope,
    ) -> ToolFuture<'a>;
}

pub fn native_entries(tool_names: &[String]) -> Vec<CatalogEntry> {
    crate::prompts::action_list(tool_names)
        .into_iter()
        .map(|name| CatalogEntry {
            required: native_args(&name),
            summary: native_summary(&name).to_string(),
            kind: EntryKind::Native,
            name,
        })
        .collect()
}

pub fn merge(native: Vec<CatalogEntry>, external: Vec<CatalogEntry>) -> Vec<CatalogEntry> {
    let bare: BTreeSet<String> = native.iter().map(|entry| entry.name.clone()).collect();
    let mut extra: Vec<CatalogEntry> = external
        .into_iter()
        .filter(|entry| {
            let unprefixed = entry
                .name
                .strip_prefix(MCP_PREFIX)
                .unwrap_or(&entry.name)
                .to_string();
            !bare.contains(&entry.name) && !bare.contains(&unprefixed)
        })
        .collect();
    extra.sort_by(|left, right| (left.kind, &left.name).cmp(&(right.kind, &right.name)));
    extra.dedup_by(|left, right| left.name == right.name);

    let mut merged: Vec<CatalogEntry> = Vec::with_capacity(native.len() + extra.len());
    let mut last: Option<CatalogEntry> = None;
    for entry in native {
        if entry.name == "final" {
            last = Some(entry);
            continue;
        }
        merged.push(entry);
    }
    merged.extend(extra);
    merged.extend(last);
    merged
}

#[cfg(test)]
#[path = "catalog_tests.rs"]
mod tests;
