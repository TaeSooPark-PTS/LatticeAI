use std::collections::HashSet;
use std::io::{Cursor, Write};

use serde_json::{json, Value};

const STEM_BYTES: usize = 120;
const HISTORY_DIR: &str = "_history";
const MANIFEST_ENTRY: &str = "_export.json";

#[derive(Debug, Default)]
pub(super) struct VaultNote {
    pub id: String,
    pub kind: String,
    pub title: String,
    pub body: String,
    pub valid_from: Option<String>,
    pub valid_to: Option<String>,
    pub superseded_by: Option<String>,
    pub source_uri: Option<String>,
    pub workspace: Option<String>,
    pub superseded: bool,
}

#[derive(Debug)]
pub(super) struct VaultLink {
    pub from: usize,
    pub to: usize,
    pub kind: String,
}

#[derive(Debug, Default, Clone, Copy)]
pub(super) struct VaultCounts {
    pub notes: usize,
    pub history: usize,
    pub withheld: usize,
    pub omitted: usize,
    pub truncated: usize,
}

impl VaultCounts {
    pub(super) fn to_json(self) -> Value {
        json!({
            "notes": self.notes,
            "history": self.history,
            "withheld": self.withheld,
            "omitted": self.omitted,
            "truncated": self.truncated,
        })
    }
}

fn is_reserved_device(stem: &str) -> bool {
    let base = stem
        .split('.')
        .next()
        .unwrap_or("")
        .trim()
        .to_ascii_uppercase();
    if matches!(base.as_str(), "CON" | "PRN" | "AUX" | "NUL") {
        return true;
    }
    let numbered = base
        .strip_prefix("COM")
        .or_else(|| base.strip_prefix("LPT"));
    numbered.is_some_and(|rest| rest.len() == 1 && rest.chars().all(|c| c.is_ascii_digit()))
}

fn clip_bytes(text: &str, limit: usize) -> &str {
    if text.len() <= limit {
        return text;
    }
    let mut end = limit;
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    &text[..end]
}

pub(super) fn sanitize_stem(title: &str) -> String {
    let replaced: String = title
        .chars()
        .map(|c| match c {
            '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|' | '#' | '^' | '[' | ']' => ' ',
            '\u{2028}' | '\u{2029}' | '\u{200e}' | '\u{200f}' | '\u{202a}'..='\u{202e}' => ' ',
            c if c.is_control() => ' ',
            c => c,
        })
        .collect();
    let collapsed = replaced.split_whitespace().collect::<Vec<_>>().join(" ");
    let edge = |c: char| c == '.' || c.is_whitespace();
    let clipped = clip_bytes(collapsed.trim_matches(edge), STEM_BYTES).trim_end_matches(edge);
    if clipped.is_empty() {
        return "untitled".into();
    }
    if is_reserved_device(clipped) {
        return format!("_{clipped}");
    }
    clipped.to_string()
}

pub(super) fn unique_stems<'a>(titles: impl Iterator<Item = &'a str>) -> Vec<String> {
    let mut taken: HashSet<String> = HashSet::new();
    titles
        .map(|title| {
            let base = sanitize_stem(title);
            let mut candidate = base.clone();
            let mut index = 2usize;
            while !taken.insert(candidate.to_lowercase()) {
                candidate = format!("{base} ({index})");
                index += 1;
            }
            candidate
        })
        .collect()
}

fn wikilink(stem: &str, title: &str) -> String {
    let alias = title
        .replace(['[', ']', '|'], " ")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    if alias.is_empty() || alias == stem {
        format!("[[{stem}]]")
    } else {
        format!("[[{stem}|{alias}]]")
    }
}

fn scalar(value: Option<&str>) -> String {
    value.map_or_else(|| "null".into(), |text| json!(text).to_string())
}

fn render_note(index: usize, notes: &[VaultNote], stems: &[String], links: &[VaultLink]) -> String {
    let note = &notes[index];
    let mut out = String::from("---\n");
    for (key, value) in [
        ("id", Some(note.id.as_str())),
        ("type", Some(note.kind.as_str())),
        ("valid_from", note.valid_from.as_deref()),
        ("valid_to", note.valid_to.as_deref()),
        ("superseded_by", note.superseded_by.as_deref()),
        ("source_uri", note.source_uri.as_deref()),
        ("workspace", note.workspace.as_deref()),
    ] {
        out.push_str(&format!("{key}: {}\n", scalar(value)));
    }
    out.push_str("---\n\n");
    let heading = note.title.split_whitespace().collect::<Vec<_>>().join(" ");
    out.push_str(&format!("# {heading}\n"));
    if !note.body.trim().is_empty() {
        out.push('\n');
        out.push_str(note.body.trim_end());
        out.push('\n');
    }
    let mut lines: Vec<String> = links
        .iter()
        .filter(|link| link.from == index)
        .map(|link| {
            format!(
                "- {} ({})",
                wikilink(&stems[link.to], &notes[link.to].title),
                link.kind
            )
        })
        .collect();
    lines.sort();
    lines.dedup();
    if !lines.is_empty() {
        out.push_str("\n## Links\n\n");
        out.push_str(&lines.join("\n"));
        out.push('\n');
    }
    out
}

pub(super) fn write_vault(
    notes: &[VaultNote],
    links: &[VaultLink],
    counts: VaultCounts,
) -> Result<Vec<u8>, String> {
    let stems = unique_stems(notes.iter().map(|note| note.title.as_str()));
    let mut buffer = Cursor::new(Vec::new());
    let mut zip = zip::ZipWriter::new(&mut buffer);
    let options = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated);
    for (index, note) in notes.iter().enumerate() {
        let name = if note.superseded {
            format!("{HISTORY_DIR}/{}.md", stems[index])
        } else {
            format!("{}.md", stems[index])
        };
        zip.start_file(name, options).map_err(|e| e.to_string())?;
        zip.write_all(render_note(index, notes, &stems, links).as_bytes())
            .map_err(|e| e.to_string())?;
    }
    zip.start_file(MANIFEST_ENTRY, options)
        .map_err(|e| e.to_string())?;
    zip.write_all(counts.to_json().to_string().as_bytes())
        .map_err(|e| e.to_string())?;
    zip.finish().map_err(|e| e.to_string())?;
    Ok(buffer.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stems_cannot_escape_the_vault_and_keep_korean() {
        assert_eq!(sanitize_stem("../../etc/passwd"), "etc passwd");
        assert_eq!(sanitize_stem("..\\..\\x"), "x");
        assert_eq!(
            sanitize_stem("회의록: 3분기 [초안] #1"),
            "회의록 3분기 초안 1"
        );
        assert_eq!(sanitize_stem("  ...  "), "untitled");
        assert_eq!(sanitize_stem("a\u{0}b\nc"), "a b c");
        assert_eq!(sanitize_stem("con"), "_con");
        assert_eq!(sanitize_stem("LPT1.notes"), "_LPT1.notes");
        assert_eq!(sanitize_stem("Common"), "Common");
        let long = sanitize_stem(&"가".repeat(200));
        assert!(long.len() <= STEM_BYTES && long.chars().all(|c| c == '가'));
    }

    #[test]
    fn colliding_titles_get_deterministic_suffixes() {
        let titles = ["Plan", "plan", "Plan (2)", "a/b", "a:b"];
        assert_eq!(
            unique_stems(titles.into_iter()),
            ["Plan", "plan (2)", "Plan (2) (2)", "a b", "a b (2)"]
        );
    }

    #[test]
    fn links_alias_only_when_the_filename_differs() {
        assert_eq!(wikilink("Alpha", "Alpha"), "[[Alpha]]");
        assert_eq!(wikilink("a b", "a|[b]"), "[[a b]]");
        assert_eq!(wikilink("a b", "a: b"), "[[a b|a: b]]");
    }
}
