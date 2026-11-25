use std::collections::BTreeSet;

use lattice_core::pytext::safe_loads;
use rusqlite::Connection;

use crate::context::approx_tokens;
use crate::docgen::py_text;

pub const DEFAULT_SUMMARY_TOKENS: i64 = 200;

pub const SELF_ID_PREFIX: &str = "self:";
pub const SELF_ROOT_ID: &str = "self:root";

pub const KIND_ORDER: [&str; 5] = ["trait", "preference", "habit", "decision", "relationship"];

pub fn kind_label(kind: &str) -> &'static str {
    match kind {
        "trait" => "나",
        "preference" => "선호",
        "habit" => "습관",
        "decision" => "결정",
        "relationship" => "관계",
        _ => "",
    }
}

const SUMMARY_HEADER: &str = "사용자에 대해 확인된 사실:";

#[derive(Debug, Clone, PartialEq, Eq)]
struct Fact {
    kind: &'static str,
    text: String,
    workspace_id: String,
}

fn read_facts(conn: &Connection, allowed: Option<&BTreeSet<String>>) -> Vec<Fact> {
    let Ok(mut statement) = conn.prepare(
        "SELECT id, type, title, summary, metadata_json, updated_at \
         FROM nodes WHERE id LIKE ? AND id != ? ORDER BY id ASC",
    ) else {
        return Vec::new();
    };
    let like = format!("{SELF_ID_PREFIX}%");
    let Ok(rows) = statement.query_map(rusqlite::params![like, SELF_ROOT_ID], |row| {
        let title: Option<String> = row.get("title")?;
        let metadata: Option<String> = row.get("metadata_json")?;
        Ok((title, safe_loads(metadata.as_deref())))
    }) else {
        return Vec::new();
    };
    let mut facts: Vec<Fact> = Vec::new();
    for (title, metadata) in rows.flatten() {
        let raw = metadata
            .get("self_model_kind")
            .filter(|value| crate::shape::truthy(value))
            .map(|value| py_text(Some(value)))
            .unwrap_or_default();
        let Some(kind) = KIND_ORDER.iter().find(|known| **known == raw) else {
            continue;
        };
        facts.push(Fact {
            kind,
            text: title.unwrap_or_default(),
            workspace_id: metadata
                .get("workspace_id")
                .filter(|value| crate::shape::truthy(value))
                .map(|value| py_text(Some(value)))
                .unwrap_or_default(),
        });
    }
    if let Some(allowed) = allowed {
        let allowed: BTreeSet<&str> = allowed
            .iter()
            .filter(|scope| !scope.is_empty())
            .map(String::as_str)
            .collect();
        facts.retain(|fact| {
            fact.workspace_id.is_empty() || allowed.contains(fact.workspace_id.as_str())
        });
    }
    facts.sort_by(|left, right| {
        let rank = |fact: &Fact| KIND_ORDER.iter().position(|kind| *kind == fact.kind);
        rank(left)
            .cmp(&rank(right))
            .then_with(|| left.text.cmp(&right.text))
    });
    facts
}

pub fn summary_for_prompt(
    conn: &Connection,
    limit_tokens: i64,
    allowed: Option<&BTreeSet<String>>,
) -> String {
    let facts = read_facts(conn, allowed);
    if facts.is_empty() || limit_tokens <= 0 {
        return String::new();
    }
    let render = |lines: &[String]| {
        std::iter::once(SUMMARY_HEADER.to_string())
            .chain(lines.iter().cloned())
            .collect::<Vec<_>>()
            .join("\n")
    };
    let mut lines: Vec<String> = Vec::new();
    for kind in KIND_ORDER {
        for fact in facts.iter().filter(|fact| fact.kind == kind) {
            let line = format!("- {}: {}", kind_label(kind), fact.text);
            let mut candidate = lines.clone();
            candidate.push(line.clone());
            if approx_tokens(&render(&candidate)) as i64 > limit_tokens {
                return if lines.is_empty() {
                    String::new()
                } else {
                    render(&lines)
                };
            }
            lines.push(line);
        }
    }
    render(&lines)
}

pub fn is_self_model_id(node_id: &str) -> bool {
    node_id.starts_with(SELF_ID_PREFIX) && node_id != SELF_ROOT_ID
}

#[cfg(test)]
mod tests {
    use super::*;

    fn profile() -> (tempfile::TempDir, Connection) {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("g.sqlite")).unwrap();
        conn.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, updated_at TEXT);
             INSERT INTO nodes VALUES
               ('self:root','Self','나','root','{\"self_model_kind\":\"root\"}','2026-01-01'),
               ('self:a','Decision','문서는 한국어로','','
                  {\"self_model_kind\":\"decision\",\"workspace_id\":\"w1\"}','2026-01-02'),
               ('self:b','Self','검색 품질 담당자','','{\"self_model_kind\":\"trait\"}','2026-01-03'),
               ('self:c','Habit','회의록을 정리한다','','
                  {\"self_model_kind\":\"habit\",\"workspace_id\":\"w2\"}','2026-01-04'),
               ('self:d','Concept','kind가 없다','','{\"self_model\":true}','2026-01-05'),
               ('doc:x','Document','not a profile row','','{}','2026-01-06');",
        )
        .unwrap();
        (dir, conn)
    }

    #[test]
    fn the_summary_renders_kinds_in_order_and_skips_unknown_rows() {
        let (_dir, conn) = profile();
        assert_eq!(
            summary_for_prompt(&conn, 200, None),
            "사용자에 대해 확인된 사실:\n- 나: 검색 품질 담당자\n- 습관: 회의록을 정리한다\n- 결정: 문서는 한국어로"
        );
        assert!(!summary_for_prompt(&conn, 200, None).contains("root"));
        assert!(!summary_for_prompt(&conn, 200, None).contains("kind가 없다"));
    }

    #[test]
    fn the_allowance_cuts_whole_lines_and_zero_injects_nothing() {
        let (_dir, conn) = profile();
        assert_eq!(summary_for_prompt(&conn, 0, None), "");
        assert_eq!(summary_for_prompt(&conn, -5, None), "");
        let one = summary_for_prompt(&conn, 8, None);
        assert_eq!(one, "사용자에 대해 확인된 사실:\n- 나: 검색 품질 담당자");
        assert_eq!(
            summary_for_prompt(&conn, 14, None),
            "사용자에 대해 확인된 사실:\n- 나: 검색 품질 담당자\n- 습관: 회의록을 정리한다"
        );
        assert_eq!(summary_for_prompt(&conn, 7, None), "");
    }

    #[test]
    fn scoping_keeps_personal_facts_and_the_callers_own_workspaces() {
        let (_dir, conn) = profile();
        let w1: BTreeSet<String> = ["w1".to_string(), String::new()].into_iter().collect();
        let scoped = summary_for_prompt(&conn, 200, Some(&w1));
        assert!(
            scoped.contains("검색 품질 담당자"),
            "no workspace = personal"
        );
        assert!(scoped.contains("문서는 한국어로"));
        assert!(!scoped.contains("회의록을 정리한다"), "w2 is not readable");
        let none: BTreeSet<String> = BTreeSet::new();
        let empty = summary_for_prompt(&conn, 200, Some(&none));
        assert!(empty.contains("검색 품질 담당자"));
        assert!(!empty.contains("문서는 한국어로"));
    }

    #[test]
    fn an_unreadable_or_empty_profile_injects_nothing() {
        let dir = tempfile::tempdir().unwrap();
        let bare = Connection::open(dir.path().join("bare.sqlite")).unwrap();
        assert_eq!(summary_for_prompt(&bare, 200, None), "", "no nodes table");
        bare.execute_batch(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
                                metadata_json TEXT, updated_at TEXT);",
        )
        .unwrap();
        assert_eq!(summary_for_prompt(&bare, 200, None), "");
        assert!(is_self_model_id("self:a"));
        assert!(!is_self_model_id(SELF_ROOT_ID));
        assert!(!is_self_model_id("doc:a"));
        assert_eq!(kind_label("nope"), "");
        assert_eq!(DEFAULT_SUMMARY_TOKENS, 200);
    }
}
