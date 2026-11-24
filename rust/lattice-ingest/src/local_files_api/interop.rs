//! Native Rust Obsidian vault & Interop (`notion`, `git`, `mail`) ingestion.
//!
//! Replaces legacy forwarding to retired Python routes (`/api/ingestion/obsidian`,
//! `/api/ingestion/interop`) so all Track 5 interop bridges write directly through
//! `GraphWriter` (single-writer SQLite) with `dry_run` preview support.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs::{self, File};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Arc;

use lattice_core::graph_write::types::EdgeSpec;
use lattice_core::graph_write::GraphWriter;
use serde_json::{json, Map, Value};
use zip::ZipArchive;

use super::LocalFilesState;
use crate::worker::{NoteIngestor, NoteSubmission};

const MAX_INTEROP_FILES: usize = 500;
const MAX_GIT_COMMITS: usize = 100;
const MAX_FILE_BYTES: u64 = 2 * 1024 * 1024;

/// Native Obsidian vault ingestion (`POST /api/ingestion/obsidian`).
///
/// Walks `.md` notes, extracts YAML/inline tags and `[[wikilink]]` targets,
/// ingests notes through `GraphWriter`, and creates `LINKS_TO` edges between
/// linked notes when `dry_run` is false.
pub(super) async fn ingest_obsidian_native(
    state: &Arc<LocalFilesState>,
    graph: GraphWriter,
    path: &str,
    dry_run: bool,
    owner: Option<&str>,
    workspace_id: Option<&str>,
) -> Result<Value, String> {
    let root = Path::new(path);
    if !root.exists() || !root.is_dir() {
        return Err(format!("Obsidian vault directory not found: {path}"));
    }

    let mut md_files = Vec::new();
    collect_files_by_ext(root, &["md"], &mut md_files);
    md_files.sort();

    let mut parsed_notes: Vec<ObsidianNote> = Vec::new();
    let mut total_wikilinks = 0usize;
    for file_path in &md_files {
        let Ok(meta) = fs::metadata(file_path) else {
            continue;
        };
        if meta.len() > MAX_FILE_BYTES {
            continue;
        }
        let Ok(raw) = fs::read_to_string(file_path) else {
            continue;
        };
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            continue;
        }
        let rel = file_path
            .strip_prefix(root)
            .unwrap_or(file_path)
            .to_string_lossy()
            .replace('\\', "/");
        let stem = file_path
            .file_stem()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_else(|| rel.clone());
        let wikilinks = extract_wikilinks(trimmed);
        total_wikilinks += wikilinks.len();
        let tags = extract_markdown_tags(trimmed);
        parsed_notes.push(ObsidianNote {
            stem,
            rel_path: rel,
            abs_path: file_path.display().to_string(),
            content: trimmed.to_string(),
            wikilinks,
            tags,
        });
    }

    if dry_run {
        let sample: Vec<String> = parsed_notes
            .iter()
            .take(20)
            .map(|n| n.rel_path.clone())
            .collect();
        return Ok(json!({
            "source": "obsidian",
            "path": path,
            "dry_run": true,
            "files_scanned": md_files.len(),
            "notes_found": parsed_notes.len(),
            "ingested": 0,
            "duplicates": 0,
            "failed": 0,
            "wikilinks_detected": total_wikilinks,
            "edges_created": 0,
            "sample_files": sample,
        }));
    }

    let ingestor = match state.seam() {
        Some(seam) => NoteIngestor::new(graph.clone()).with_seam(seam.clone()),
        None => NoteIngestor::new(graph.clone()),
    };
    let mut stem_to_node_id: HashMap<String, String> = HashMap::new();
    let mut note_links: Vec<(String, Vec<String>)> = Vec::new();
    let mut ingested = 0usize;
    let mut duplicates = 0usize;
    let mut failed = 0usize;
    let mut errors: Vec<Value> = Vec::new();

    for note in &parsed_notes {
        let mut metadata = Map::new();
        metadata.insert("source_kind".into(), json!("obsidian"));
        metadata.insert("relative_path".into(), json!(note.rel_path));
        metadata.insert("path".into(), json!(note.abs_path));
        metadata.insert("tags".into(), json!(note.tags));
        metadata.insert("wikilinks".into(), json!(note.wikilinks));
        metadata.insert("detected_by".into(), json!("lattice-ingest"));
        let submission = NoteSubmission {
            title: note.stem.clone(),
            content: note.content.clone(),
            source: Some(note.abs_path.clone()),
            metadata,
        };
        match ingestor.ingest_note(&submission, owner, workspace_id).await {
            Ok(receipt) => {
                if receipt.duplicate {
                    duplicates += 1;
                } else {
                    ingested += 1;
                }
                stem_to_node_id.insert(note.stem.to_lowercase(), receipt.node_id.clone());
                stem_to_node_id.insert(
                    note.rel_path.trim_end_matches(".md").to_lowercase(),
                    receipt.node_id.clone(),
                );
                if !note.wikilinks.is_empty() {
                    note_links.push((receipt.node_id, note.wikilinks.clone()));
                }
            }
            Err(err) => {
                failed += 1;
                errors.push(json!({"file": note.rel_path, "error": err.to_string()}));
            }
        }
    }

    let mut edge_specs: Vec<EdgeSpec> = Vec::new();
    let mut seen_pairs: HashSet<(String, String)> = HashSet::new();
    for (from_id, targets) in &note_links {
        for target in targets {
            let key = target.trim().to_lowercase();
            if let Some(to_id) = stem_to_node_id.get(&key) {
                if to_id != from_id && seen_pairs.insert((from_id.clone(), to_id.clone())) {
                    let mut meta = Map::new();
                    meta.insert("source".into(), json!("obsidian_wikilink"));
                    meta.insert("target".into(), json!(target));
                    edge_specs.push(EdgeSpec {
                        from_node: from_id.clone(),
                        to_node: to_id.clone(),
                        edge_type: "LINKS_TO".into(),
                        weight: 1.0,
                        metadata: meta,
                        legacy_label: Some("LINKS_TO".into()),
                    });
                }
            }
        }
    }

    let edges_created = if edge_specs.is_empty() {
        0
    } else {
        let writer = graph.clone();
        let specs = edge_specs;
        tokio::task::spawn_blocking(move || writer.upsert_edges(&specs))
            .await
            .map_err(|e| format!("obsidian edge write failed: {e}"))?
            .map_err(|e| format!("obsidian edge upsert failed: {e}"))?
            .len()
    };

    Ok(json!({
        "source": "obsidian",
        "path": path,
        "dry_run": false,
        "files_scanned": md_files.len(),
        "notes_found": parsed_notes.len(),
        "ingested": ingested,
        "duplicates": duplicates,
        "failed": failed,
        "wikilinks_detected": total_wikilinks,
        "edges_created": edges_created,
        "errors": errors,
    }))
}

/// Native interop bridge ingestion (`POST /api/ingestion/interop`) for
/// `notion` (directory or `.zip` export), `git` (local repo log), and
/// `mail` (local `.eml` / `.ics` file or directory).
pub(super) async fn ingest_interop_native(
    state: &Arc<LocalFilesState>,
    graph: GraphWriter,
    source: &str,
    path: &str,
    dry_run: bool,
    owner: Option<&str>,
    workspace_id: Option<&str>,
) -> Result<Value, String> {
    let items = match source {
        "notion" => load_notion_items(Path::new(path))?,
        "git" => load_git_items(Path::new(path))?,
        "mail" => load_mail_items(Path::new(path))?,
        other => return Err(format!("Unsupported interop source: {other}")),
    };

    if dry_run {
        let sample: Vec<String> = items.iter().take(20).map(|i| i.title.clone()).collect();
        return Ok(json!({
            "source": source,
            "path": path,
            "dry_run": true,
            "items_found": items.len(),
            "ingested": 0,
            "duplicates": 0,
            "failed": 0,
            "sample_titles": sample,
        }));
    }

    let ingestor = match state.seam() {
        Some(seam) => NoteIngestor::new(graph).with_seam(seam.clone()),
        None => NoteIngestor::new(graph),
    };
    let mut ingested = 0usize;
    let mut duplicates = 0usize;
    let mut failed = 0usize;
    let mut errors: Vec<Value> = Vec::new();

    for item in &items {
        let mut metadata = item.metadata.clone();
        metadata.insert("source_kind".into(), json!(source));
        metadata.insert("detected_by".into(), json!("lattice-ingest"));
        let submission = NoteSubmission {
            title: item.title.clone(),
            content: item.content.clone(),
            source: Some(item.origin.clone()),
            metadata,
        };
        match ingestor.ingest_note(&submission, owner, workspace_id).await {
            Ok(receipt) => {
                if receipt.duplicate {
                    duplicates += 1;
                } else {
                    ingested += 1;
                }
            }
            Err(err) => {
                failed += 1;
                errors.push(json!({"title": item.title, "error": err.to_string()}));
            }
        }
    }

    Ok(json!({
        "source": source,
        "path": path,
        "dry_run": false,
        "items_found": items.len(),
        "ingested": ingested,
        "duplicates": duplicates,
        "failed": failed,
        "errors": errors,
    }))
}

struct ObsidianNote {
    stem: String,
    rel_path: String,
    abs_path: String,
    content: String,
    wikilinks: Vec<String>,
    tags: Vec<String>,
}

struct InteropItem {
    title: String,
    content: String,
    origin: String,
    metadata: Map<String, Value>,
}

fn collect_files_by_ext(dir: &Path, exts: &[&str], out: &mut Vec<PathBuf>) {
    if out.len() >= MAX_INTEROP_FILES {
        return;
    }
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        if out.len() >= MAX_INTEROP_FILES {
            break;
        }
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.starts_with('.')
            || matches!(name.as_str(), "node_modules" | "target" | "__pycache__")
        {
            continue;
        }
        if path.is_dir() {
            collect_files_by_ext(&path, exts, out);
        } else if path.is_file() {
            let ext = path
                .extension()
                .and_then(|e| e.to_str())
                .map(str::to_lowercase)
                .unwrap_or_default();
            if exts.iter().any(|allowed| *allowed == ext) {
                out.push(path);
            }
        }
    }
}

/// Extract `[[Target]]`, `[[Target|Alias]]`, and `[[Target#Heading]]` links.
pub(crate) fn extract_wikilinks(text: &str) -> Vec<String> {
    let mut links = Vec::new();
    let mut seen = HashSet::new();
    let bytes = text.as_bytes();
    let mut i = 0;
    while i + 1 < bytes.len() {
        if bytes[i] == b'[' && bytes[i + 1] == b'[' {
            let start = i + 2;
            if let Some(rel_end) = text[start..].find("]]") {
                let raw = text[start..start + rel_end].trim();
                let before_alias = raw.split('|').next().unwrap_or("").trim();
                let target = before_alias.split('#').next().unwrap_or("").trim();
                if !target.is_empty() && seen.insert(target.to_lowercase()) {
                    links.push(target.to_string());
                }
                i = start + rel_end + 2;
                continue;
            }
        }
        i += 1;
    }
    links
}

fn extract_markdown_tags(text: &str) -> Vec<String> {
    let mut tags = BTreeMap::new();
    for token in text.split_whitespace() {
        if let Some(rest) = token.strip_prefix('#') {
            let clean: String = rest
                .chars()
                .take_while(|c| c.is_alphanumeric() || *c == '-' || *c == '_' || *c == '/')
                .collect();
            if !clean.is_empty() && !clean.chars().all(|c| c.is_ascii_digit()) {
                tags.insert(clean.to_lowercase(), clean);
            }
        }
    }
    tags.into_values().collect()
}

fn strip_notion_uuid_suffix(stem: &str) -> String {
    let parts: Vec<&str> = stem.rsplitn(2, ' ').collect();
    if parts.len() == 2 && parts[0].len() == 32 && parts[0].chars().all(|c| c.is_ascii_hexdigit()) {
        return parts[1].trim().to_string();
    }
    stem.trim().to_string()
}

fn load_notion_items(path: &Path) -> Result<Vec<InteropItem>, String> {
    if !path.exists() {
        return Err(format!("Notion export path not found: {}", path.display()));
    }
    let mut items = Vec::new();
    if path.is_file()
        && path
            .extension()
            .and_then(|e| e.to_str())
            .is_some_and(|e| e.eq_ignore_ascii_case("zip"))
    {
        let file = File::open(path).map_err(|e| format!("Cannot open Notion zip: {e}"))?;
        let mut archive = ZipArchive::new(file).map_err(|e| format!("Invalid Notion zip: {e}"))?;
        for idx in 0..archive.len().min(MAX_INTEROP_FILES) {
            let Ok(mut entry) = archive.by_index(idx) else {
                continue;
            };
            if entry.is_dir() || entry.size() > MAX_FILE_BYTES {
                continue;
            }
            let Ok(name_cow) = entry.name() else {
                continue;
            };
            let name = name_cow.into_owned();
            let lower = name.to_lowercase();
            if !(lower.ends_with(".md") || lower.ends_with(".csv")) {
                continue;
            }
            let mut body = String::new();
            if entry.read_to_string(&mut body).is_err() || body.trim().is_empty() {
                continue;
            }
            let stem = Path::new(&name)
                .file_stem()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_else(|| name.clone());
            let mut metadata = Map::new();
            metadata.insert("archive_entry".into(), json!(name));
            items.push(InteropItem {
                title: strip_notion_uuid_suffix(&stem),
                content: body.trim().to_string(),
                origin: format!("{}!{}", path.display(), name),
                metadata,
            });
        }
        return Ok(items);
    }

    if !path.is_dir() {
        return Err(format!(
            "Notion export must be a directory or .zip file: {}",
            path.display()
        ));
    }
    let mut files = Vec::new();
    collect_files_by_ext(path, &["md", "csv"], &mut files);
    files.sort();
    for file_path in files {
        let Ok(meta) = fs::metadata(&file_path) else {
            continue;
        };
        if meta.len() > MAX_FILE_BYTES {
            continue;
        }
        let Ok(body) = fs::read_to_string(&file_path) else {
            continue;
        };
        if body.trim().is_empty() {
            continue;
        }
        let stem = file_path
            .file_stem()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_else(|| file_path.display().to_string());
        let mut metadata = Map::new();
        metadata.insert("path".into(), json!(file_path.display().to_string()));
        items.push(InteropItem {
            title: strip_notion_uuid_suffix(&stem),
            content: body.trim().to_string(),
            origin: file_path.display().to_string(),
            metadata,
        });
    }
    Ok(items)
}

fn load_git_items(path: &Path) -> Result<Vec<InteropItem>, String> {
    if !path.exists() || !path.is_dir() {
        return Err(format!(
            "Git repository directory not found: {}",
            path.display()
        ));
    }
    let output = Command::new("git")
        .arg("-C")
        .arg(path)
        .args([
            "log",
            "-n",
            &MAX_GIT_COMMITS.to_string(),
            "--date=iso-strict",
            "--stat",
            "--pretty=format:===COMMIT===%n%H%n%an%n%ad%n%s%n%b%n===FILES===",
        ])
        .output()
        .map_err(|e| format!("Failed to invoke git: {e}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("git log failed: {}", stderr.trim()));
    }
    let raw = String::from_utf8_lossy(&output.stdout);
    let repo_name = path
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| "repo".into());
    let mut items = Vec::new();
    for block in raw.split("===COMMIT===").skip(1) {
        let mut lines = block.trim_start_matches('\n').lines();
        let sha = lines.next().unwrap_or("").trim();
        let author = lines.next().unwrap_or("").trim();
        let date = lines.next().unwrap_or("").trim();
        let subject = lines.next().unwrap_or("").trim();
        if sha.is_empty() || subject.is_empty() {
            continue;
        }
        let rest: String = lines.collect::<Vec<_>>().join("\n");
        let short_sha = &sha[..sha.len().min(8)];
        let content = format!(
            "Repository: {repo_name}\nCommit: {sha}\nAuthor: {author}\nDate: {date}\nSubject: {subject}\n\n{}",
            rest.replace("===FILES===", "Changed files:").trim()
        );
        let mut metadata = Map::new();
        metadata.insert("commit_sha".into(), json!(sha));
        metadata.insert("author".into(), json!(author));
        metadata.insert("commit_date".into(), json!(date));
        metadata.insert("repository".into(), json!(repo_name));
        items.push(InteropItem {
            title: format!("[{repo_name} {short_sha}] {subject}"),
            content,
            origin: format!("git://{}#{}", path.display(), sha),
            metadata,
        });
    }
    Ok(items)
}

fn load_mail_items(path: &Path) -> Result<Vec<InteropItem>, String> {
    if !path.exists() {
        return Err(format!("Mail/calendar path not found: {}", path.display()));
    }
    let mut files = Vec::new();
    if path.is_file() {
        files.push(path.to_path_buf());
    } else if path.is_dir() {
        collect_files_by_ext(path, &["eml", "ics"], &mut files);
        files.sort();
    }
    let mut items = Vec::new();
    for file_path in files {
        let Ok(meta) = fs::metadata(&file_path) else {
            continue;
        };
        if meta.len() > MAX_FILE_BYTES {
            continue;
        }
        let Ok(raw) = fs::read_to_string(&file_path) else {
            continue;
        };
        let ext = file_path
            .extension()
            .and_then(|e| e.to_str())
            .map(str::to_lowercase)
            .unwrap_or_default();
        if ext == "eml" {
            if let Some(item) = parse_eml(&file_path, &raw) {
                items.push(item);
            }
        } else if ext == "ics" {
            items.extend(parse_ics(&file_path, &raw));
        }
    }
    Ok(items)
}

fn parse_eml(file_path: &Path, raw: &str) -> Option<InteropItem> {
    let normalized = raw.replace("\r\n", "\n");
    let (header_part, body_part) = normalized
        .split_once("\n\n")
        .unwrap_or((normalized.as_str(), ""));
    let mut subject = String::new();
    let mut from = String::new();
    let mut date = String::new();
    for line in header_part.lines() {
        if let Some((k, v)) = line.split_once(':') {
            match k.trim().to_ascii_lowercase().as_str() {
                "subject" => subject = v.trim().to_string(),
                "from" => from = v.trim().to_string(),
                "date" => date = v.trim().to_string(),
                _ => {}
            }
        }
    }
    let title = if subject.is_empty() {
        file_path
            .file_stem()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_else(|| "Email".into())
    } else {
        subject
    };
    let content = format!(
        "Subject: {title}\nFrom: {from}\nDate: {date}\n\n{}",
        body_part.trim()
    );
    if content.trim().is_empty() {
        return None;
    }
    let mut metadata = Map::new();
    metadata.insert("format".into(), json!("eml"));
    metadata.insert("from".into(), json!(from));
    metadata.insert("date".into(), json!(date));
    metadata.insert("path".into(), json!(file_path.display().to_string()));
    Some(InteropItem {
        title,
        content,
        origin: file_path.display().to_string(),
        metadata,
    })
}

fn parse_ics(file_path: &Path, raw: &str) -> Vec<InteropItem> {
    let normalized = raw.replace("\r\n", "\n");
    let mut items = Vec::new();
    for block in normalized.split("BEGIN:VEVENT").skip(1) {
        let body = block.split("END:VEVENT").next().unwrap_or("");
        let mut summary = String::new();
        let mut dtstart = String::new();
        let mut dtend = String::new();
        let mut description = String::new();
        let mut location = String::new();
        for line in body.lines() {
            if let Some((k, v)) = line.split_once(':') {
                let key = k
                    .split(';')
                    .next()
                    .unwrap_or("")
                    .trim()
                    .to_ascii_uppercase();
                match key.as_str() {
                    "SUMMARY" => summary = v.trim().to_string(),
                    "DTSTART" => dtstart = v.trim().to_string(),
                    "DTEND" => dtend = v.trim().to_string(),
                    "DESCRIPTION" => description = v.trim().replace("\\n", "\n"),
                    "LOCATION" => location = v.trim().to_string(),
                    _ => {}
                }
            }
        }
        if summary.is_empty() && description.is_empty() {
            continue;
        }
        let title = if summary.is_empty() {
            "Calendar Event".to_string()
        } else {
            summary
        };
        let content = format!(
            "Event: {title}\nStart: {dtstart}\nEnd: {dtend}\nLocation: {location}\n\n{description}"
        );
        let mut metadata = Map::new();
        metadata.insert("format".into(), json!("ics"));
        metadata.insert("dtstart".into(), json!(dtstart));
        metadata.insert("dtend".into(), json!(dtend));
        metadata.insert("location".into(), json!(location));
        metadata.insert("path".into(), json!(file_path.display().to_string()));
        items.push(InteropItem {
            title,
            content: content.trim().to_string(),
            origin: file_path.display().to_string(),
            metadata,
        });
    }
    items
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extract_wikilinks_handles_aliases_and_headings() {
        let links = extract_wikilinks(
            "See [[Project Alpha]], [[Project Alpha|Alpha]], and [[Design Doc#Scope]].",
        );
        assert_eq!(links, vec!["Project Alpha", "Design Doc"]);
    }

    #[test]
    fn strip_notion_uuid_suffix_cleans_32_hex_id() {
        assert_eq!(
            strip_notion_uuid_suffix("Roadmap 2026 a1b2c3d4e5f60718293a4b5c6d7e8f90"),
            "Roadmap 2026"
        );
        assert_eq!(strip_notion_uuid_suffix("Plain Title"), "Plain Title");
    }
}
