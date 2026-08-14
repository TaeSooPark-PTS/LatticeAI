//! The markdown vault half of `latticeai/services/p_reinforce.py`.
//!
//! Everything here is plain filesystem state under the brain directory —
//! `~/.ltcai-brain` by default — which is user-owned, Obsidian-compatible and
//! *not* a worker-owned table, so it is written natively (WAVE2_COMMON rule 6
//! draws the line at the knowledge graph, and this is the other side of it).
//!
//! The five folder names, the classifier's rules **in order**, the
//! `YYYYMMDD_HHMMSS_<slug>.md` stamp, the note body and the daily log line are
//! reproduced literally: `rust/fixtures/http/memory_brain.json` pins the two
//! notes at 241 and 182 bytes and the log at 315, and those numbers only come
//! out right if every separator matches.

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use std::io::Write;
use std::path::{Path, PathBuf};

use lattice_core::pytext;
use serde::Serialize;

/// `p_reinforce.STRUCTURE`, in Python's dict order — which is the order
/// `get_tree` emits and therefore a client-visible contract.
pub const STRUCTURE: [(&str, &str); 5] = [
    ("10_Wiki", "검증된 지식, 개념 설명, 레퍼런스"),
    ("00_Raw", "정제되지 않은 원시 데이터, 아이디어 메모"),
    ("20_Skills", "재사용 가능한 코드 스니펫, 프롬프트, 워크플로"),
    ("30_Projects", "프로젝트별 컨텍스트, 진행 상황"),
    ("40_Log", "날짜별 작업 로그"),
];

/// `LATTICEAI_OBSIDIAN_VAULT_DIR`, else `LATTICEAI_BRAIN_DIR`, else
/// `~/.ltcai-brain` — `p_reinforce.BRAIN_DIR`, evaluated once per process
/// there (module import) and once per router build here.
///
/// Python's `os.getenv(...) or os.getenv(...)` treats an empty string as
/// unset, so the same `filter` is applied rather than a bare `ok()`.
pub fn brain_dir() -> PathBuf {
    for name in ["LATTICEAI_OBSIDIAN_VAULT_DIR", "LATTICEAI_BRAIN_DIR"] {
        if let Some(value) = std::env::var(name).ok().filter(|v| !v.is_empty()) {
            return PathBuf::from(value);
        }
    }
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_default();
    home.join(".ltcai-brain")
}

/// `_ensure_structure`: the five folders, then `INDEX.md` if it is absent.
///
/// Best effort by return type: the gardener runs this from its constructor, so
/// the caller is a router build with nowhere to report to. A folder that could
/// not be made surfaces on the next write, which *is* reported.
pub fn ensure_structure(dir: &Path, now_minute: &str) {
    for (folder, _) in STRUCTURE {
        let _ = std::fs::create_dir_all(dir.join(folder));
    }
    let index = dir.join("INDEX.md");
    if !index.exists() {
        let _ = std::fs::write(&index, render_index(now_minute));
    }
}

/// `_render_index` — the vault's front page, joined with `"\n"` like Python's.
pub fn render_index(now_minute: &str) -> String {
    let mut lines: Vec<String> = vec![
        "# 🧠 Lattice AI Brain — P-Reinforce Index\n".to_string(),
        format!("*Generated: {now_minute}*\n"),
        "\nThis folder is an Obsidian-compatible Markdown vault.\n".to_string(),
        "\nThe Knowledge Graph is the authoritative store; this vault is the\nuser-owned markdown mirror of garden notes.\n".to_string(),
    ];
    for (folder, desc) in STRUCTURE {
        lines.push(format!("## [{folder}](./{folder}/)\n_{desc}_\n"));
    }
    lines.push("## Connector Status\n".to_string());
    let ocr = if which("tesseract").is_some() {
        "tesseract"
    } else {
        "not installed"
    };
    lines.push(format!("- OCR engine: `{ocr}`\n"));
    lines.join("\n")
}

/// `shutil.which(cmd)` for a bare command name on POSIX.
///
/// Same rule: split `PATH` on `:`, an empty entry means the working
/// directory, and a candidate counts only when it is an existing file the
/// process may execute. `os.defpath` is the fallback when `PATH` is unset.
fn which(command: &str) -> Option<PathBuf> {
    let path = std::env::var("PATH").unwrap_or_else(|_| ":/bin:/usr/bin".to_string());
    for entry in path.split(':') {
        let candidate = if entry.is_empty() {
            PathBuf::from(command)
        } else {
            Path::new(entry).join(command)
        };
        if is_executable_file(&candidate) {
            return Some(candidate);
        }
    }
    None
}

#[cfg(unix)]
fn is_executable_file(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path)
        .map(|meta| meta.is_file() && meta.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

#[cfg(not(unix))]
fn is_executable_file(path: &Path) -> bool {
    path.is_file()
}

// ── classify ────────────────────────────────────────────────────────────────

/// `_classify`, rule for rule and **in order**.
///
/// The first test reads the *original* text and the next two read the
/// lower-cased copy — that asymmetry is in the Python and is load-bearing:
/// `Class Foo` is not code by this classifier, `class Foo` is.
pub fn classify(text: &str) -> &'static str {
    const CODE: [&str; 8] = [
        "def ",
        "class ",
        "import ",
        "```",
        "function ",
        "const ",
        "let ",
        "var ",
    ];
    const WIKI: [&str; 7] = [
        "개념",
        "원리",
        "이란",
        "what is",
        "how does",
        "definition",
        "explanation",
    ];
    const PROJECT: [&str; 7] = [
        "project",
        "프로젝트",
        "todo",
        "task",
        "작업",
        "기능",
        "feature",
    ];

    if CODE.iter().any(|needle| text.contains(needle)) {
        return "20_Skills";
    }
    let lower = text.to_lowercase();
    if WIKI.iter().any(|needle| lower.contains(needle)) {
        return "10_Wiki";
    }
    if PROJECT.iter().any(|needle| lower.contains(needle)) {
        return "30_Projects";
    }
    "00_Raw"
}

/// The folder a `category` names, or `None` when it is not one of the five.
///
/// `category if category in STRUCTURE else self._classify(...)` — an unknown
/// category is silently classified rather than refused, which is why a typo'd
/// folder never 400s.
pub fn folder_for(category: Option<&str>, text: &str) -> &'static str {
    category
        .and_then(|name| {
            STRUCTURE
                .iter()
                .find(|(folder, _)| *folder == name)
                .map(|(folder, _)| *folder)
        })
        .unwrap_or_else(|| classify(text))
}

/// The description column of [`STRUCTURE`], by folder name.
pub fn description(folder: &str) -> &'static str {
    STRUCTURE
        .iter()
        .find(|(name, _)| *name == folder)
        .map(|(_, desc)| *desc)
        .unwrap_or_default()
}

// ── file naming ─────────────────────────────────────────────────────────────

/// `_make_filename(text, folder)` — `folder` is accepted and ignored there
/// too, so it is simply not a parameter here.
///
/// `re.sub(r"[^\w\s-]", "", …)` is spelled out rather than compiled: Python's
/// `\w` for `str` patterns is "`str.isalnum()` plus underscore" (letters and
/// numbers, *not* combining marks), and `\s` is `str.isspace()`, which counts
/// `U+001C`–`U+001F`. `fancy_regex`'s Unicode classes are neither, so the
/// predicate is written out with the mapping named.
pub fn make_filename(text: &str, stamp: &str) -> String {
    let first_line = pytext::truncate_chars(first_line_of(text), 60);
    let kept: String = first_line
        .chars()
        .filter(|c| c.is_alphanumeric() || *c == '_' || *c == '-' || pytext::is_py_space(*c))
        .collect();
    let collapsed = collapse_whitespace(&pytext::strip(&kept));
    // `safe or "note"` — an empty slug is falsy in Python.
    let safe = if collapsed.is_empty() {
        "note"
    } else {
        collapsed.as_str()
    };
    format!("{stamp}_{safe}.md")
}

/// `re.sub(r"\s+", "_", value)`.
fn collapse_whitespace(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    let mut in_run = false;
    for c in value.chars() {
        if pytext::is_py_space(c) {
            if !in_run {
                out.push('_');
                in_run = true;
            }
        } else {
            out.push(c);
            in_run = false;
        }
    }
    out
}

/// `text.strip().split("\n")[0]` — the title line every part of this module
/// starts from.
pub fn first_line_of(text: &str) -> &str {
    let stripped = strip_span(text);
    match stripped.find('\n') {
        Some(index) => &stripped[..index],
        None => stripped,
    }
}

/// `str.strip()` as a borrow of the original, so slicing stays allocation-free.
fn strip_span(text: &str) -> &str {
    let start = text
        .char_indices()
        .find(|(_, c)| !pytext::is_py_space(*c))
        .map(|(index, _)| index);
    let Some(start) = start else { return "" };
    let end = text
        .char_indices()
        .rev()
        .find(|(_, c)| !pytext::is_py_space(*c))
        .map(|(index, c)| index + c.len_utf8())
        .unwrap_or(text.len());
    &text[start..end]
}

// ── note body + daily log ───────────────────────────────────────────────────

/// `_wrap_markdown` — six lines joined with `"\n"`, embedded newlines and all.
pub fn wrap_markdown(raw: &str, folder: &str, now_minute: &str) -> String {
    let first_line = pytext::truncate_chars(first_line_of(raw), 80);
    [
        format!("# {first_line}"),
        format!("\n> 📁 `{folder}` | 🕐 {now_minute} | Lattice AI MLX\n"),
        "---\n".to_string(),
        raw.to_string(),
        "\n\n---".to_string(),
        "*Auto-organized by P-Reinforce Gardener*".to_string(),
    ]
    .join("\n")
}

/// `_append_log`: today's file under `40_Log`, header only when it is new.
///
/// The Python reads `open(path, "a")` *before* asking whether the file exists,
/// so the exists/size test always sees the file the open just created — the
/// header is written exactly when the log was empty. Reproduced by testing the
/// length of the handle instead of racing a second `stat`.
pub fn append_log(
    dir: &Path,
    preview: &str,
    folder: &str,
    filename: &str,
    date: &str,
    hhmm: &str,
) -> std::io::Result<()> {
    let path = dir.join("40_Log").join(format!("{date}.md"));
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)?;
    if file.metadata().map(|meta| meta.len()).unwrap_or(0) == 0 {
        writeln!(file, "# 📅 Log — {date}")?;
    }
    let preview = pytext::truncate_chars(preview, 100);
    writeln!(file, "\n- [{hhmm}] → `{folder}/{filename}`\n  > {preview}")
}

// ── tree ────────────────────────────────────────────────────────────────────

/// `get_tree()`'s answer. Field order is the serialized key order — a derived
/// `Serialize` writes declaration order, which is why this is a struct and not
/// a `serde_json::Map` (that one is a `BTreeMap` and would sort).
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct Tree {
    pub root: String,
    pub folders: Vec<Folder>,
}

/// One row of `get_tree()`'s `folders`.
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct Folder {
    pub name: &'static str,
    pub description: &'static str,
    pub files: Vec<FileEntry>,
    pub count: usize,
}

/// One `*.md` file inside a garden folder.
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct FileEntry {
    pub name: String,
    pub size_bytes: u64,
    pub modified_at: String,
}

/// `get_tree` — the five folders in `STRUCTURE` order, each with its `*.md`
/// entries sorted by name.
///
/// `sorted(folder_path.glob("*.md"))` sorts `Path` objects, which inside one
/// directory is a code-point sort of the file name; Rust's `str` ordering is
/// the same comparison. Dotfiles are included, because `pathlib.Path.glob`
/// includes them (unlike `glob.glob`).
pub fn get_tree(dir: &Path) -> Tree {
    let mut folders = Vec::with_capacity(STRUCTURE.len());
    for (name, description) in STRUCTURE {
        let path = dir.join(name);
        let mut files = Vec::new();
        if path.exists() {
            let mut names: Vec<String> = match std::fs::read_dir(&path) {
                Ok(entries) => entries
                    .filter_map(|entry| entry.ok())
                    .map(|entry| entry.file_name().to_string_lossy().into_owned())
                    .filter(|name| name.ends_with(".md"))
                    .collect(),
                // An unreadable directory yields nothing rather than failing
                // the whole tree, which is the `except OSError: continue`
                // inside the Python loop taken one level up.
                Err(_) => Vec::new(),
            };
            names.sort();
            for name in names {
                // `except OSError: continue` — a file that vanished between
                // the listing and the stat is skipped, not reported.
                let Ok(meta) = std::fs::metadata(path.join(&name)) else {
                    continue;
                };
                files.push(FileEntry {
                    name,
                    size_bytes: meta.len(),
                    modified_at: modified_at(&meta),
                });
            }
        }
        let count = files.len();
        folders.push(Folder {
            name,
            description,
            files,
            count,
        });
    }
    Tree {
        root: dir.display().to_string(),
        folders,
    }
}

/// `datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")`.
///
/// Local, naive, second-truncated. `fromtimestamp` rounds the float to the
/// nearest microsecond first, so a stamp 400 ns short of a whole second
/// reports the *next* second; that carry is reproduced rather than truncated
/// away, because it is the difference between matching a Python-written
/// listing and being one second behind it.
fn modified_at(meta: &std::fs::Metadata) -> String {
    let Ok(modified) = meta.modified() else {
        return String::new();
    };
    let Ok(since) = modified.duration_since(std::time::UNIX_EPOCH) else {
        return String::new();
    };
    let mut secs = since.as_secs() as i64;
    if round_half_even_micros(since.subsec_nanos()) == 1_000_000 {
        secs += 1;
    }
    local_iso(secs)
}

/// `round(nanos / 1000)` with Python's banker's rounding.
fn round_half_even_micros(nanos: u32) -> u32 {
    let whole = nanos / 1000;
    let remainder = nanos % 1000;
    match remainder.cmp(&500) {
        std::cmp::Ordering::Greater => whole + 1,
        std::cmp::Ordering::Less => whole,
        std::cmp::Ordering::Equal if whole % 2 == 1 => whole + 1,
        std::cmp::Ordering::Equal => whole,
    }
}

/// `datetime.fromtimestamp(secs).isoformat(timespec="seconds")` — naive local
/// time, through `localtime_r(3)` for the same reason
/// `memory_api::shared::now_iso` uses it: there is no timezone crate here.
#[cfg(unix)]
fn local_iso(utc_secs: i64) -> String {
    let stamp = utc_secs as libc::time_t;
    let mut broken: libc::tm = unsafe { std::mem::zeroed() };
    // SAFETY: `localtime_r` fills the caller-owned `tm` we just zeroed and
    // returns a pointer to it (or null on failure); nothing else is touched.
    if unsafe { libc::localtime_r(&stamp, &mut broken) }.is_null() {
        return String::new();
    }
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
        broken.tm_year + 1900,
        broken.tm_mon + 1,
        broken.tm_mday,
        broken.tm_hour,
        broken.tm_min,
        broken.tm_sec,
    )
}

#[cfg(not(unix))]
fn local_iso(_utc_secs: i64) -> String {
    String::new()
}

// ── clock readings ──────────────────────────────────────────────────────────

/// The four `strftime` renderings one request needs, from one clock reading.
///
/// Python calls `datetime.now()` four separate times per `process()` (the file
/// stamp, the note header, the log date and the log time); taking one reading
/// is the same answer except across a second boundary, where one reading is
/// the *more* correct of the two — a note whose header disagrees with its own
/// filename is a bug nobody wants reproduced.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Stamps {
    /// `%Y-%m-%d` — the daily log's name.
    pub date: String,
    /// `%H:%M` — the log entry's time.
    pub hhmm: String,
    /// `%Y-%m-%d %H:%M` — the note header and the index's `Generated:`.
    pub minute: String,
    /// `%Y%m%d_%H%M%S` — the filename stamp the fixtures call `@stamp`.
    pub file: String,
}

/// Split `now_iso()`'s `YYYY-MM-DDTHH:MM:SS` into the four renderings.
///
/// Anything that is not that exact ASCII shape (only a test clock can produce
/// one) falls back to the epoch rather than panicking or slicing a char
/// boundary in half.
pub fn stamps(now: &str) -> Stamps {
    const EPOCH: &str = "1970-01-01T00:00:00";
    let now = if now.len() >= 19 && now.is_ascii() {
        &now[..19]
    } else {
        EPOCH
    };
    let date = &now[0..10];
    let hhmm = &now[11..16];
    Stamps {
        date: date.to_string(),
        hhmm: hhmm.to_string(),
        minute: format!("{date} {hhmm}"),
        file: format!(
            "{}{}{}_{}{}{}",
            &now[0..4],
            &now[5..7],
            &now[8..10],
            &now[11..13],
            &now[14..16],
            &now[17..19]
        ),
    }
}

/// `lattice_brain.utils.utc_now_iso()` — offset-aware UTC, `timespec="auto"`,
/// which is the `captured_at` the ingestion pipeline stamps every item with.
///
/// `auto` means microseconds appear only when they are non-zero.
pub fn utc_now_iso() -> String {
    let since = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    utc_iso_of(since.as_secs() as i64, since.subsec_micros())
}

/// The renderer behind [`utc_now_iso`], split out so it can be tested at a
/// fixed instant.
pub fn utc_iso_of(secs: i64, micros: u32) -> String {
    let days = secs.div_euclid(86_400);
    let rest = secs.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    let base = format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}",
        rest / 3600,
        (rest % 3600) / 60,
        rest % 60,
    );
    if micros == 0 {
        format!("{base}+00:00")
    } else {
        format!("{base}.{micros:06}+00:00")
    }
}

/// Days since the Unix epoch → `(year, month, day)`, proleptic Gregorian.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_classifier_rules_fire_in_the_python_order() {
        // Code wins over everything, and it reads the original case.
        assert_eq!(classify("def main():\n    프로젝트 개념"), "20_Skills");
        assert_eq!(classify("Class Foo"), "00_Raw");
        assert_eq!(classify("class Foo"), "20_Skills");
        assert_eq!(classify("```rust"), "20_Skills");
        // Wiki beats project, and reads the lower-cased copy.
        assert_eq!(classify("WHAT IS a task?"), "10_Wiki");
        assert_eq!(classify("이 개념은 무엇인가"), "10_Wiki");
        assert_eq!(classify("TODO: ship it"), "30_Projects");
        assert_eq!(classify("프로젝트 킥오프"), "30_Projects");
        assert_eq!(classify("Ranking notes: alpha fusion stays."), "00_Raw");
    }

    #[test]
    fn a_named_category_wins_unless_it_is_not_one_of_the_five() {
        assert_eq!(folder_for(Some("10_Wiki"), "def x"), "10_Wiki");
        assert_eq!(folder_for(Some("99_Nope"), "def x"), "20_Skills");
        assert_eq!(folder_for(None, "def x"), "20_Skills");
        assert_eq!(description("40_Log"), "날짜별 작업 로그");
        assert_eq!(description("absent"), "");
    }

    #[test]
    fn the_filename_is_the_stamp_plus_a_slugged_first_line() {
        assert_eq!(
            make_filename(
                "Rust 이관 결정: 게이트웨이가 제품 서버가 된다.",
                "20260814_123456"
            ),
            "20260814_123456_Rust_이관_결정_게이트웨이가_제품_서버가_된다.md"
        );
        assert_eq!(
            make_filename("Ranking notes: alpha fusion stays.", "20260814_123456"),
            "20260814_123456_Ranking_notes_alpha_fusion_stays.md"
        );
        // Only the first line, only 60 characters of it, and punctuation gone.
        assert_eq!(
            make_filename("  hello   world!!\nsecond line", "S"),
            "S_hello_world.md"
        );
        assert_eq!(
            make_filename(&"가".repeat(80), "S"),
            format!("S_{}.md", "가".repeat(60))
        );
        // Nothing survives the filter → the literal fallback.
        assert_eq!(make_filename("!!!", "S"), "S_note.md");
        assert_eq!(make_filename("", "S"), "S_note.md");
        // A hyphen and an underscore are kept; `\s+` collapses to one `_`.
        assert_eq!(make_filename("a-b _ c", "S"), "S_a-b___c.md");
    }

    #[test]
    fn the_note_body_is_the_one_the_fixture_weighed() {
        let body = wrap_markdown(
            "Rust 이관 결정: 게이트웨이가 제품 서버가 된다.",
            "10_Wiki",
            "2026-08-14 12:34",
        );
        assert_eq!(body.len(), 241, "the fixture records size_bytes 241");
        assert!(
            body.starts_with("# Rust 이관 결정: 게이트웨이가 제품 서버가 된다.\n\n> 📁 `10_Wiki`")
        );
        assert!(body.ends_with("\n\n\n---\n*Auto-organized by P-Reinforce Gardener*"));
        assert_eq!(
            wrap_markdown(
                "Ranking notes: alpha fusion stays.",
                "00_Raw",
                "2026-08-14 12:34"
            )
            .len(),
            182,
        );
    }

    #[test]
    fn the_daily_log_gets_one_header_and_one_entry_per_note() {
        let dir = tempfile::tempdir().expect("tempdir");
        ensure_structure(dir.path(), "2026-08-14 12:34");
        append_log(
            dir.path(),
            "Rust 이관 결정: 게이트웨이가 제품 서버가 된다.",
            "10_Wiki",
            "20260814_123400_Rust_이관_결정_게이트웨이가_제품_서버가_된다.md",
            "2026-08-14",
            "12:34",
        )
        .expect("first entry");
        append_log(
            dir.path(),
            "Ranking notes: alpha fusion stays.",
            "00_Raw",
            "20260814_123400_Ranking_notes_alpha_fusion_stays.md",
            "2026-08-14",
            "12:34",
        )
        .expect("second entry");
        let text = std::fs::read_to_string(dir.path().join("40_Log/2026-08-14.md")).expect("log");
        assert_eq!(text.len(), 315, "the fixture records size_bytes 315");
        assert_eq!(text.matches("# 📅 Log — 2026-08-14").count(), 1);
        assert_eq!(text.matches("\n- [12:34] → `").count(), 2);
    }

    #[test]
    fn the_tree_walks_the_five_folders_in_structure_order() {
        let dir = tempfile::tempdir().expect("tempdir");
        ensure_structure(dir.path(), "2026-08-14 12:34");
        std::fs::write(dir.path().join("10_Wiki/b.md"), "bb").expect("b");
        std::fs::write(dir.path().join("10_Wiki/a.md"), "a").expect("a");
        std::fs::write(dir.path().join("10_Wiki/skip.txt"), "no").expect("txt");
        std::fs::write(dir.path().join("INDEX.md"), "root file is not in the tree").expect("index");
        let tree = get_tree(dir.path());
        assert_eq!(tree.root, dir.path().display().to_string());
        let names: Vec<&str> = tree.folders.iter().map(|f| f.name).collect();
        assert_eq!(
            names,
            ["10_Wiki", "00_Raw", "20_Skills", "30_Projects", "40_Log"]
        );
        assert_eq!(
            tree.folders[0].description,
            "검증된 지식, 개념 설명, 레퍼런스"
        );
        assert_eq!(tree.folders[0].count, 2);
        assert_eq!(
            tree.folders[0]
                .files
                .iter()
                .map(|f| (f.name.as_str(), f.size_bytes))
                .collect::<Vec<_>>(),
            [("a.md", 1), ("b.md", 2)],
            "sorted by name, .txt excluded"
        );
        assert_eq!(tree.folders[1].count, 0);
        assert!(tree.folders[0].files[0].modified_at.len() == 19);
        // Key order is the declaration order, not an alphabetical sort.
        let rendered = serde_json::to_string(&tree).expect("render");
        assert!(rendered.starts_with("{\"root\":"));
        assert!(rendered.contains("{\"name\":\"a.md\",\"size_bytes\":1,\"modified_at\":"));
    }

    #[test]
    fn a_missing_folder_is_an_empty_one_rather_than_an_error() {
        let dir = tempfile::tempdir().expect("tempdir");
        let tree = get_tree(dir.path());
        assert_eq!(tree.folders.len(), 5);
        assert!(tree
            .folders
            .iter()
            .all(|f| f.count == 0 && f.files.is_empty()));
    }

    #[test]
    fn the_index_page_names_the_folders_and_the_ocr_engine() {
        let dir = tempfile::tempdir().expect("tempdir");
        ensure_structure(dir.path(), "2026-08-14 12:34");
        let text = std::fs::read_to_string(dir.path().join("INDEX.md")).expect("index");
        assert!(text.starts_with(
            "# 🧠 Lattice AI Brain — P-Reinforce Index\n\n*Generated: 2026-08-14 12:34*\n"
        ));
        assert!(
            text.contains("## [30_Projects](./30_Projects/)\n_프로젝트별 컨텍스트, 진행 상황_\n")
        );
        assert!(text.contains("- OCR engine: `"));
        // Written once: a second construction must not overwrite the user's.
        std::fs::write(dir.path().join("INDEX.md"), "mine").expect("overwrite");
        ensure_structure(dir.path(), "2026-08-14 12:35");
        assert_eq!(
            std::fs::read_to_string(dir.path().join("INDEX.md")).expect("index"),
            "mine"
        );
        assert!(which("this-command-does-not-exist-anywhere").is_none());
    }

    #[test]
    fn one_clock_reading_renders_the_four_stamps() {
        let stamps = stamps("2026-08-14T12:34:56");
        assert_eq!(stamps.date, "2026-08-14");
        assert_eq!(stamps.hhmm, "12:34");
        assert_eq!(stamps.minute, "2026-08-14 12:34");
        assert_eq!(stamps.file, "20260814_123456");
        // A clock that answered nonsense falls back rather than panicking.
        assert_eq!(super::stamps("nope").file, "19700101_000000");
        assert_eq!(
            super::stamps("한글이라서짧지않지만아스키가아니다").date,
            "1970-01-01"
        );
    }

    #[test]
    fn captured_at_is_the_pipelines_offset_aware_utc_stamp() {
        assert_eq!(utc_iso_of(0, 0), "1970-01-01T00:00:00+00:00");
        assert_eq!(utc_iso_of(1_786_000_496, 0), "2026-08-06T07:14:56+00:00");
        assert_eq!(
            utc_iso_of(1_786_000_496, 123_456),
            "2026-08-06T07:14:56.123456+00:00"
        );
        assert!(utc_now_iso().ends_with("+00:00"));
        assert_eq!(round_half_even_micros(1_500), 2);
        assert_eq!(round_half_even_micros(2_500), 2);
        assert_eq!(round_half_even_micros(2_501), 3);
        assert_eq!(round_half_even_micros(2_499), 2);
    }

    #[test]
    fn the_brain_dir_reads_the_two_env_names_then_home() {
        // Process-global env is not touched here (parallel tests); the fallback
        // shape is what a caller can rely on.
        let dir = brain_dir();
        assert!(dir.is_absolute() || dir.to_string_lossy().ends_with(".ltcai-brain"));
    }

    #[test]
    fn first_line_and_strip_follow_pythons_definitions() {
        assert_eq!(first_line_of("  a\nb  "), "a");
        assert_eq!(first_line_of("\u{1c}x\u{1f}"), "x");
        assert_eq!(first_line_of("   "), "");
        assert_eq!(strip_span(""), "");
        assert_eq!(collapse_whitespace("a \t b"), "a_b");
    }
}
