//! The loop's view of what else this machine can do (v12.0.0).
//!
//! `lattice-agent` asks one question of its host — *besides your own tools,
//! what may this run choose?* — through [`ToolCatalog`]. This is the product's
//! answer, and it has two halves.
//!
//! ## Skills
//!
//! Installed skills become `skill.<name>` rows, scanned from the **same
//! directory** `POST /mcp`'s `tools/list` scans (`McpState::skills_dir`, whose
//! resolution rule this module borrows rather than re-derives) with the **same
//! scanner** (`workspace::skills::scan_installed_skills`) under the **same
//! name** (`skill.<name>`). So a skill that Claude Desktop can see over MCP is
//! a skill the agent loop can see on its menu, spelled identically.
//!
//! Choosing one returns its `SKILL.md`. That is the whole effect and the honest
//! one: a skill is guidance, so nothing is written, nothing is run, and the
//! model still has to pick a real tool afterwards. `lattice-agent`'s menu says
//! so in as many words.
//!
//! ## MCP tools
//!
//! The MCP surface's callable set is [`MCP_TOOLS`] — seven read-only workspace
//! and knowledge tools — and **every one of them is already a governed tool of
//! the run** (`product_policy_table` names all seven; the test below proves it
//! rather than trusting it). They are published here under `mcp.` so the loop's
//! catalog is the honest whole, and `lattice_agent::tools::catalog::resolve`
//! then routes each one to its bare name, through the kernel's own gate chain:
//! circuit breaker, destructive policy, the fail-closed overwrite guard, the
//! approval gate, `check_role`. That chain is a **superset** of the MCP
//! surface's `check_governance`, so the loop is governed at least as strictly
//! as `/mcp` is, and one tool is never governed two different ways depending on
//! which of its two names the model happened to pick.
//!
//! What that leaves is a name the loop does *not* govern — a future remote MCP
//! server, or a curated tool added to the platform and not to the policy table.
//! [`PlatformCatalog::execute`] sends those through
//! [`lattice_platform::mcp::dispatch_for_agent`], which is the same
//! `check_governance` `POST /mcp` runs. A refusal — unknown tool, role, or a
//! missing tool surface — is a [`ToolOutcome::Error`], never a silent success.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use lattice_agent::tools::catalog::{ArgSpec, CatalogEntry, EntryKind, ToolCatalog};
use lattice_agent::tools::{CallScope, ToolFuture};
use lattice_agent::worker::ToolOutcome;
use lattice_platform::workspace::skills::scan_installed_skills;
use serde_json::{json, Map, Value};

/// The tools `POST /mcp`'s `tools/list` offers, with the one argument each of
/// them actually needs asked for by name.
///
/// Read-only, every one — the MCP surface is a *reading* surface by design, and
/// the loop's writers reach it under their own names. Kept in step with
/// `lattice_platform::mcp::dispatch::NATIVE_TOOLS` by
/// [`tests::every_published_mcp_tool_is_a_governed_tool_of_the_run`], which
/// fails the moment one of these stops being governed.
pub const MCP_TOOLS: &[(&str, &str, &str)] = &[
    ("list_dir", "path", "list a directory in the workspace"),
    ("read_file", "path", "read a workspace file"),
    ("workspace_tree", "path", "show the workspace tree"),
    ("grep", "pattern", "search the workspace for text"),
    (
        "knowledge_search",
        "query",
        "search the knowledge garden for notes",
    ),
    ("knowledge_tree", "", "list the knowledge garden's notes"),
    ("git_status", "", "read-only git status in the workspace"),
];

/// The product's answer to "what else may this run choose?".
#[derive(Debug, Clone)]
pub struct PlatformCatalog {
    skills_dir: PathBuf,
}

impl PlatformCatalog {
    /// A catalog over the directory `/mcp` scans for skills.
    pub fn new(skills_dir: impl Into<PathBuf>) -> Self {
        Self {
            skills_dir: skills_dir.into(),
        }
    }

    /// The directory this catalog reads skills from.
    pub fn skills_dir(&self) -> &Path {
        &self.skills_dir
    }

    fn skill_entries(&self) -> Vec<CatalogEntry> {
        scan_installed_skills(&self.skills_dir)
            .into_iter()
            .map(|skill| CatalogEntry {
                name: format!(
                    "{}{}",
                    lattice_agent::tools::catalog::SKILL_PREFIX,
                    skill.name
                ),
                kind: EntryKind::Skill,
                summary: first_line(&skill.description),
                // A skill takes no argument the harness could ask for: what it
                // returns is its instructions, and the run's own context is
                // what they apply to.
                required: Vec::new(),
            })
            .collect()
    }
}

/// Who MCP governance sees for this call.
///
/// `CallScope` carries the run's email, not its role. An address we actually
/// have is a signed-in member (`user`); empty / `local` is the loopback
/// owner. That is the fail-closed reading: a named account does not inherit
/// the owner's auto-approve.
fn identity_from_scope(scope: &CallScope) -> lattice_auth::Identity {
    match scope.user_email.as_deref() {
        Some(email) if !email.is_empty() && email != "local" => lattice_auth::Identity {
            email: email.to_string(),
            role: "user".into(),
        },
        _ => lattice_auth::Identity::local_owner(),
    }
}

/// One line, capped — a menu row a small model can read in a glance.
fn first_line(text: &str) -> String {
    let line = text.lines().next().unwrap_or_default().trim();
    if line.chars().count() <= 100 {
        return line.to_string();
    }
    let short: String = line.chars().take(100).collect();
    format!("{short}…")
}

impl ToolCatalog for PlatformCatalog {
    fn entries(&self) -> Vec<CatalogEntry> {
        let mut entries: Vec<CatalogEntry> = MCP_TOOLS
            .iter()
            .map(|(name, argument, summary)| CatalogEntry {
                name: format!("{}{name}", lattice_agent::tools::catalog::MCP_PREFIX),
                kind: EntryKind::Mcp,
                summary: (*summary).to_string(),
                required: if argument.is_empty() {
                    Vec::new()
                } else {
                    vec![ArgSpec::line(argument, summary)]
                },
            })
            .collect();
        entries.extend(self.skill_entries());
        entries
    }

    fn execute<'a>(
        &'a self,
        name: &'a str,
        args: &'a Map<String, Value>,
        scope: &'a CallScope,
    ) -> ToolFuture<'a> {
        Box::pin(async move {
            let Some(skill_name) = name.strip_prefix(lattice_agent::tools::catalog::SKILL_PREFIX)
            else {
                // An `mcp.*` name the run does not govern: the platform's own
                // dispatch, including `check_governance`. Never a bypass.
                let identity = identity_from_scope(scope);
                return match lattice_platform::mcp::dispatch_for_agent(
                    None,
                    &self.skills_dir,
                    &identity,
                    name,
                    &Value::Object(args.clone()),
                ) {
                    Ok(result) => ToolOutcome::Result(result),
                    Err(error) => ToolOutcome::Error(error.to_string()),
                };
            };
            let dir = self.skills_dir.clone();
            let wanted = skill_name.to_string();
            // A directory scan and a file read, off the reactor — this server
            // has one event loop for every user.
            let found = tokio::task::spawn_blocking(move || {
                scan_installed_skills(&dir)
                    .into_iter()
                    .find(|skill| skill.name == wanted)
            })
            .await
            .ok()
            .flatten();
            match found {
                // The same payload `mcp::dispatch::skill_result` returns, minus
                // the echoed arguments: the loop already records what it asked
                // for on the step, and repeating it would be transcript noise.
                Some(skill) => ToolOutcome::Result(json!({
                    "kind": "skill",
                    "name": skill.name,
                    "description": skill.description,
                    "text": skill.body,
                    "note": "A skill is guidance, not an executable. Follow these instructions \
                             with the run's own tools.",
                })),
                None => ToolOutcome::Error(format!(
                    "Skill '{skill_name}' is not installed on this machine."
                )),
            }
        })
    }
}

/// The skills directory `/mcp` resolves, without a second copy of the rule.
///
/// `McpState::new` is the owner of "where do skills live" (env override, then
/// `<data>/skills`, then the repo's). Building one throwaway state to read the
/// answer is cheaper than being wrong about it.
pub fn skills_dir_for(auth: Arc<lattice_auth::AuthState>, data_dir: &Path) -> PathBuf {
    lattice_platform::mcp::McpState::new(auth, data_dir).skills_dir
}

#[cfg(test)]
mod tests {
    use super::*;
    use lattice_agent::tools::catalog::{resolve, Resolved};

    fn scope() -> CallScope {
        CallScope::default()
    }

    #[test]
    fn every_published_mcp_tool_is_a_governed_tool_of_the_run() {
        // The load-bearing assertion of this whole module: because each of
        // these names is in the run's policy table, `resolve` sends it to the
        // kernel's gate chain and the refusal branch below is unreachable. If
        // a tool is ever dropped from `product_policy_table`, this fails here
        // rather than silently becoming an ungoverned menu row.
        let table = super::super::agent_bind::product_policy_table();
        for (name, _, _) in MCP_TOOLS {
            assert!(
                table.tools.contains_key(*name),
                "mcp.{name} is offered but the run does not govern {name}"
            );
            let qualified = format!("mcp.{name}");
            assert_eq!(
                resolve(&qualified, |bare| table.tools.contains_key(bare)),
                Resolved::Native(name),
                "mcp.{name} must take the kernel's gate chain"
            );
            let policy = &table.tools[*name];
            assert_eq!(policy.risk, "read", "the MCP surface is read-only: {name}");
        }
        assert_eq!(MCP_TOOLS.len(), 7);
    }

    #[test]
    fn a_skill_directory_becomes_menu_rows_named_as_mcp_names_them() {
        let dir = tempfile::tempdir().expect("tempdir");
        for (name, description) in [
            ("code_review", "Review code for defects."),
            ("file_edit", "Edit files carefully."),
        ] {
            let path = dir.path().join(name);
            std::fs::create_dir_all(&path).expect("dir");
            std::fs::write(
                path.join("SKILL.md"),
                format!("name: {name}\ndescription: {description}\n\nBody here.\n"),
            )
            .expect("skill");
        }
        // Not a skill: no SKILL.md.
        std::fs::create_dir_all(dir.path().join("not-a-skill")).expect("dir");

        let catalog = PlatformCatalog::new(dir.path());
        let entries = catalog.entries();
        let skills: Vec<&CatalogEntry> = entries
            .iter()
            .filter(|entry| entry.kind == EntryKind::Skill)
            .collect();
        assert_eq!(skills.len(), 2, "{entries:?}");
        assert_eq!(skills[0].name, "skill.code_review");
        assert_eq!(skills[0].summary, "Review code for defects.");
        assert!(
            skills[0].required.is_empty(),
            "a skill is guidance; there is nothing to ask for"
        );
        // And the MCP half is there beside it, prefixed and with its argument.
        let read = entries
            .iter()
            .find(|entry| entry.name == "mcp.read_file")
            .expect("mcp.read_file");
        assert_eq!(read.kind, EntryKind::Mcp);
        assert_eq!(read.signature(), "mcp.read_file{path}");
        let status = entries
            .iter()
            .find(|entry| entry.name == "mcp.git_status")
            .expect("mcp.git_status");
        assert!(status.required.is_empty(), "no argument, none invented");
    }

    #[tokio::test]
    async fn choosing_a_skill_returns_its_instructions_and_says_what_it_is() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("file_edit");
        std::fs::create_dir_all(&path).expect("dir");
        std::fs::write(
            path.join("SKILL.md"),
            "description: Edit carefully.\n\nAlways read before you edit.\n",
        )
        .expect("skill");

        let catalog = PlatformCatalog::new(dir.path());
        let outcome = catalog
            .execute("skill.file_edit", &Map::new(), &scope())
            .await;
        match outcome {
            ToolOutcome::Result(result) => {
                assert_eq!(result["kind"], json!("skill"));
                assert!(result["text"]
                    .as_str()
                    .expect("text")
                    .contains("Always read before you edit"));
                assert!(result["note"]
                    .as_str()
                    .expect("note")
                    .contains("guidance, not an executable"));
            }
            other => panic!("expected the skill body, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn an_uninstalled_skill_refuses() {
        let dir = tempfile::tempdir().expect("tempdir");
        let catalog = PlatformCatalog::new(dir.path());
        match catalog.execute("skill.nope", &Map::new(), &scope()).await {
            ToolOutcome::Error(message) => assert!(message.contains("not installed"), "{message}"),
            other => panic!("expected a refusal, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn an_mcp_name_not_in_the_run_policy_table_still_hits_mcp_governance() {
        // execute() is the ungoverned path: resolve already sent a name the
        // run governs to the kernel. What reaches here must still pass
        // through MCP's own check_governance — a refusal is a tool error,
        // never a bypass and never the old by-name stub.
        let dir = tempfile::tempdir().expect("tempdir");
        let catalog = PlatformCatalog::new(dir.path());
        let member = CallScope {
            user_email: Some("member@example.com".into()),
            workspace_id: None,
        };
        let mut args = Map::new();
        args.insert("query".into(), json!("notes"));
        match catalog
            .execute("mcp.knowledge_search", &args, &member)
            .await
        {
            ToolOutcome::Error(message) => {
                assert!(
                    message.contains("명시 승인이 필요"),
                    "governance refusal must surface as a tool error: {message}"
                );
                assert!(
                    !message.contains("second governance check"),
                    "the stub refusal is gone: {message}"
                );
            }
            other => panic!("must not bypass MCP governance: {other:?}"),
        }
        match catalog
            .execute("mcp.some_future_server", &Map::new(), &scope())
            .await
        {
            ToolOutcome::Error(message) => {
                assert!(
                    message.contains("unknown MCP tool"),
                    "an unknown name is a tool error, not a run: {message}"
                );
            }
            other => panic!("an unknown name must never run: {other:?}"),
        }
    }

    #[test]
    fn a_long_description_is_shortened_to_one_menu_line() {
        assert_eq!(first_line("one\ntwo"), "one");
        assert_eq!(first_line("  padded  "), "padded");
        let long = "x".repeat(200);
        let shortened = first_line(&long);
        assert!(shortened.ends_with('…'));
        assert_eq!(shortened.chars().count(), 101);
    }
}
