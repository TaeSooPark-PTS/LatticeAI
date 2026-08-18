//! `infer_file_target` / `infer_project_manifest` — what the user asked for.
//!
//! A 1:1 port of `latticeai.core.file_generation.inference`. `normalize_plan`
//! is built on these two, so a drift here silently changes which files a run
//! writes: the manifest decides the file set for a recognised multi-file
//! project, and the single-target inference is what an empty plan falls back to.
//!
//! `fancy_regex` rather than `regex` for the same reason `lattice-retrieval`
//! uses it: the originals lean on ASCII lookbehind/lookahead (`(?<![a-z0-9])js
//! (?![a-z0-9])`) because `\b` fails against Korean particles — Hangul is a word
//! character in both engines, so `js로` would not match `\bjs\b` in either.

use std::sync::OnceLock;

use fancy_regex::Regex;
use serde_json::{json, Value};

fn compile(cell: &'static OnceLock<Regex>, pattern: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(pattern).expect("ported pattern must compile"))
}

fn hit(regex: &Regex, text: &str) -> bool {
    regex.is_match(text).unwrap_or(false)
}

fn create_verb() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compile(
        &RE,
        r"(?i)(만들|생성|작성|써\s*줘|저장|create|make|write|generate|build|save)",
    )
}

/// Explicit type keyword → default filename. Ordered: first match wins.
const TYPE_KEYWORDS: [(&str, &str); 11] = [
    (
        r"\bhtml\b|웹\s*페이지|웹페이지|홈페이지|landing\s*page|web\s*page",
        "generated_page.html",
    ),
    (r"\bcss\b|스타일\s*시트", "styles.css"),
    (
        r"\bjavascript\b|\bjs\b\s*(파일|file)|자바스크립트",
        "script.js",
    ),
    (r"\bpython\b|파이썬", "script.py"),
    (r"\bjson\b", "data.json"),
    (r"\bcsv\b", "data.csv"),
    (r"\byaml\b|\byml\b", "config.yaml"),
    (r"\bxml\b", "data.xml"),
    (r"\bsql\b", "query.sql"),
    (r"마크다운|\bmarkdown\b|\bmd\b\s*(파일|file)", "notes.md"),
    (r"텍스트\s*파일|\btext\s*file\b|\btxt\b", "notes.txt"),
];

fn type_keywords() -> &'static Vec<(Regex, &'static str)> {
    static SET: OnceLock<Vec<(Regex, &'static str)>> = OnceLock::new();
    SET.get_or_init(|| {
        TYPE_KEYWORDS
            .iter()
            .map(|(pattern, name)| {
                (
                    Regex::new(pattern).expect("ported pattern must compile"),
                    *name,
                )
            })
            .collect()
    })
}

/// Infer a filename for creation requests that name a type but no path.
pub fn infer_file_target(message: &str) -> Option<String> {
    let text = message.trim();
    if text.is_empty() || !hit(create_verb(), text) {
        return None;
    }
    let lower = text.to_lowercase();
    type_keywords()
        .iter()
        .find(|(pattern, _)| hit(pattern, &lower))
        .map(|(_, filename)| (*filename).to_string())
}

fn html_hint() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compile(
        &RE,
        r"(?<![a-z0-9])html(?![a-z0-9])|웹\s*페이지|웹페이지|홈페이지|웹\s*사이트|웹사이트|website|web\s*page|landing\s*page",
    )
}

fn css_hint() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compile(
        &RE,
        r"(?<![a-z0-9])css(?![a-z0-9])|스타일\s*시트|stylesheet",
    )
}

fn js_hint() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compile(
        &RE,
        r"(?<![a-z0-9])js(?![a-z0-9])|javascript|자바스크립트|자바\s*스크립트",
    )
}

fn explicit_filename() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compile(
        &RE,
        r"(?i)[\w-]+\.(?:html?|css|js|jsx|ts|tsx|py|json|md|txt|csv|vue|svelte)\b",
    )
}

/// A path token with its directories, **without** a trailing `\b`.
///
/// [`explicit_filename`] ends in `\b`, which is exactly right for "did the user
/// manage paths themselves" and exactly wrong for reading the path back out:
/// Hangul is a word character, so `summary.md로` has no boundary after `md` and
/// the match never happens. The lookahead here rejects a longer *ASCII* word
/// (`hello.mdx`) and lets the Korean particle that follows a real path through,
/// which is the case this function exists for.
fn output_path_token() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compile(
        &RE,
        r"(?i)(?:[A-Za-z0-9_.-]+/)*[\w-]+\.(?:html?|css|js|jsx|ts|tsx|py|json|md|txt|csv|vue|svelte)(?![A-Za-z0-9_])",
    )
}

/// The Korean particles that mark what follows a path as its **destination**.
///
/// `로` / `으로` / `에` / `에다(가)`, optionally after one noun the request put
/// between the name and the particle (`notes/hello.md 파일로`). The negative
/// lookahead is what keeps `에서` and `로부터` — which mark an *input* — out:
/// a particle followed by another Hangul syllable is the start of a different
/// word, not the postposition this is looking for.
fn korean_destination() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compile(
        &RE,
        r"^\s*(?:파일|문서|노트|경로)?\s*(?:으로|로|에다가|에다|에)(?![가-힣])",
    )
}

/// The English prepositions that mark the path after them as a destination.
fn english_destination() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compile(&RE, r"(?i)(?:^|[^A-Za-z0-9])(?:to|into|as|in)\s+$")
}

/// The files a request names as **its own outputs**.
///
/// [`infer_project_manifest`] deliberately refuses to infer anything once the
/// message names a filename ("the user manages paths"), which left
/// [`crate::kernel::transcript::requirement_coverage`] with an empty declared
/// list — and therefore a vacuously complete one — for every request of the
/// shape `notes/summary.md로 저장해줘`. That is precisely the shape a small
/// model fails on, so the one gate that would have caught the failure was
/// unreachable exactly where it was needed.
///
/// This reads the same fact the other way round: a filename the user attached a
/// *destination* marker to is a file the run was asked to produce. Nothing else
/// counts — `README.md 첫 문단을 요약해` names a file the run only reads, and
/// declaring that one an output would turn an honest DONE into a false
/// NEEDS_REVIEW. When no marker is present nothing is declared, which is the
/// behaviour every caller had before.
pub fn requested_output_paths(message: &str) -> Vec<String> {
    let text = message.trim();
    if text.is_empty() || !hit(create_verb(), text) {
        return Vec::new();
    }
    let mut found: Vec<String> = Vec::new();
    for matched in output_path_token().find_iter(text).flatten() {
        let before = &text[..matched.start()];
        let after = &text[matched.end()..];
        if !hit(korean_destination(), after) && !hit(english_destination(), before) {
            continue;
        }
        let path = matched.as_str().to_string();
        if !found.contains(&path) {
            found.push(path);
        }
    }
    found
}

fn project_name() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compile(&RE, r"(?i)([A-Za-z][A-Za-z0-9_-]{1,30})\s*(?:앱|app\b)")
}

fn react_hint() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compile(&RE, r"(?<![a-z0-9])react(?![a-z0-9])|리액트")
}

fn vite_hint() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compile(&RE, r"(?<![a-z0-9])vite(?![a-z0-9])")
}

fn python_hint() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compile(&RE, r"(?<![a-z0-9])python(?![a-z0-9])|파이썬")
}

fn package_hint() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compile(&RE, r"패키지|(?<![a-z0-9])package(?![a-z0-9])")
}

fn package_name() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    compile(
        &RE,
        r"(?i)([A-Za-z][A-Za-z0-9_-]{1,30})\s*(?:패키지|package\b)",
    )
}

fn first_group(regex: &Regex, text: &str) -> Option<String> {
    regex
        .captures(text)
        .ok()
        .flatten()
        .and_then(|captures| captures.get(1).map(|group| group.as_str().to_string()))
}

fn file(path: &str, brief: &str) -> Value {
    json!({"path": path, "brief": brief})
}

fn react_manifest(text: &str) -> Value {
    let name = first_group(project_name(), text)
        .map(|found| format!("{}-app", found.to_lowercase()))
        .unwrap_or_else(|| "react-app".into());
    json!({
        "name": name,
        "kind": "react",
        "files": [
            file("package.json", &format!(
                "Vite React app manifest: strictly valid JSON with \"name\": \"{name}\", \
    \"private\": true, \"type\": \"module\", \"scripts\" {{\"dev\": \"vite\", \
    \"build\": \"vite build\", \"preview\": \"vite preview\"}}, \"dependencies\" \
    with react and react-dom (^18), and \"devDependencies\" with vite \
    and @vitejs/plugin-react."
            )),
            file("index.html",
                "The Vite entry HTML: <div id=\"root\"></div> in <body> and \
    <script type=\"module\" src=\"/src/main.jsx\"></script> just \
    before </body>. No inline styles or scripts."),
            file("src/main.jsx",
                "React entry: createRoot from react-dom/client rendering <App /> \
    into #root; imports ./App.jsx and ./App.css."),
            file("src/App.jsx",
                "The main App component implementing the user's request as one \
    self-contained React component (hooks allowed, no extra deps)."),
            file("src/App.css", "All visual styles for the App component."),
        ],
    })
}

fn python_package_manifest(text: &str) -> Value {
    let raw_name = first_group(package_name(), text)
        .map(|found| found.to_lowercase())
        .unwrap_or_else(|| "my_package".into());
    let mut module: String = raw_name
        .chars()
        .map(|character| {
            if character.is_ascii_lowercase() || character.is_ascii_digit() || character == '_' {
                character
            } else {
                '_'
            }
        })
        .collect();
    // `re.match(r"[a-z_]", module)` — the first character decides.
    if !module
        .chars()
        .next()
        .is_some_and(|first| first.is_ascii_lowercase() || first == '_')
    {
        module = format!("pkg_{module}");
    }
    json!({
        "name": module,
        "kind": "python",
        "files": [
            file(&format!("{module}/__init__.py"), &format!(
                "Package init for {module}: import and re-export the public \
    API from .core with an explicit __all__."
            )),
            file(&format!("{module}/core.py"),
                "Implement the user's request as clean, documented functions/\
    classes with type hints. Standard library only."),
            file(&format!("{module}/cli.py"),
                "argparse CLI wrapping the core API: a main() function and an \
    if __name__ == \"__main__\": main() guard."),
            file("README.md", &format!(
                "Usage documentation for the {module} package: install, import \
    example, and CLI example."
            )),
        ],
    })
}

/// Infer a multi-file project manifest from a creation request.
pub fn infer_project_manifest(message: &str) -> Option<Value> {
    let text = message.trim();
    if text.is_empty() || !hit(create_verb(), text) {
        return None;
    }
    if hit(explicit_filename(), text) {
        return None;
    }
    let lower = text.to_lowercase();

    if hit(react_hint(), &lower) || hit(vite_hint(), &lower) {
        return Some(react_manifest(text));
    }
    if hit(python_hint(), &lower) && hit(package_hint(), &lower) {
        return Some(python_package_manifest(text));
    }

    let wants_html = hit(html_hint(), &lower);
    let wants_css = hit(css_hint(), &lower);
    let wants_js = hit(js_hint(), &lower);
    if !wants_html || !(wants_css || wants_js) {
        return None;
    }

    let name = first_group(project_name(), text)
        .map(|found| format!("{}-app", found.to_lowercase()))
        .unwrap_or_else(|| "web-project".into());

    let mut html_refs: Vec<&str> = Vec::new();
    if wants_css {
        html_refs.push("<link rel=\"stylesheet\" href=\"style.css\"> in <head>");
    }
    if wants_js {
        html_refs.push("<script src=\"app.js\"></script> just before </body>");
    }
    let mut files = vec![file(
        "index.html",
        &format!(
            "The main HTML page of the project. Reference the sibling files: {}. \
Do not inline styles or behavior scripts.",
            html_refs.join(" and ")
        ),
    )];
    if wants_css {
        files.push(file(
            "style.css",
            "All visual styles for index.html (layout, colors, typography).",
        ));
    }
    if wants_js {
        files.push(file(
            "app.js",
            "All page behavior for index.html as plain browser JavaScript \
(no build step, no imports of missing files).",
        ));
    }
    Some(json!({"name": name, "kind": "web", "files": files}))
}

/// The declared paths of a manifest, in order.
pub fn manifest_paths(manifest: &Value) -> Vec<String> {
    manifest["files"]
        .as_array()
        .map(|files| {
            files
                .iter()
                .map(|spec| spec["path"].as_str().unwrap_or("").to_string())
                .collect()
        })
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_type_keyword_plus_a_creation_verb_names_a_file() {
        assert_eq!(
            infer_file_target("html 파일 만들어줘").as_deref(),
            Some("generated_page.html")
        );
        assert_eq!(
            infer_file_target("write me a python script").as_deref(),
            Some("script.py")
        );
        assert_eq!(
            infer_file_target("csv 저장해줘").as_deref(),
            Some("data.csv")
        );
    }

    #[test]
    fn without_a_verb_or_a_type_it_infers_nothing() {
        assert_eq!(infer_file_target("html이 뭐야?"), None);
        assert_eq!(infer_file_target("만들어줘"), None);
        assert_eq!(infer_file_target(""), None);
        assert_eq!(infer_file_target("   "), None);
    }

    #[test]
    fn word_boundaries_fail_against_korean_particles_in_both_engines() {
        // `\bhtml\b` does not match `html과`: Hangul is a word character, so
        // there is no boundary after `html` — in Python's `re` and here alike.
        // The next table row (`css`) is what answers. Cross-checked against the
        // Python original, which also returns `styles.css`.
        assert_eq!(
            infer_file_target("html과 css 만들어줘").as_deref(),
            Some("styles.css")
        );
        // With a boundary present the table order does apply.
        assert_eq!(
            infer_file_target("html and css 만들어줘").as_deref(),
            Some("generated_page.html")
        );
    }

    #[test]
    fn the_web_bundle_needs_html_plus_one_of_css_or_js() {
        let manifest = infer_project_manifest("todo 앱 html+css+js로 만들어줘").expect("manifest");
        assert_eq!(manifest["name"], "todo-app");
        assert_eq!(manifest["kind"], "web");
        assert_eq!(
            manifest_paths(&manifest),
            vec!["index.html", "style.css", "app.js"]
        );
        // The Korean particle case the ASCII lookarounds exist for.
        assert!(infer_project_manifest("웹페이지 js로 만들어줘").is_some());
        // html alone is a single-file request and stays on the old path.
        assert_eq!(infer_project_manifest("html 파일 만들어줘"), None);
    }

    #[test]
    fn the_html_brief_names_only_the_siblings_that_exist() {
        let css_only = infer_project_manifest("웹페이지 css로 만들어줘").expect("manifest");
        let brief = css_only["files"][0]["brief"].as_str().expect("brief");
        assert!(brief.contains("style.css"));
        assert!(!brief.contains("app.js"));
        assert!(!brief.contains(" and "), "one reference, no conjunction");
        assert_eq!(manifest_paths(&css_only), vec!["index.html", "style.css"]);
    }

    #[test]
    fn react_and_python_intents_take_precedence_in_that_order() {
        let react = infer_project_manifest("react 로 todo 앱 만들어줘").expect("react");
        assert_eq!(react["kind"], "react");
        assert_eq!(react["name"], "todo-app");
        assert_eq!(
            manifest_paths(&react),
            vec![
                "package.json",
                "index.html",
                "src/main.jsx",
                "src/App.jsx",
                "src/App.css"
            ]
        );
        assert!(react["files"][0]["brief"]
            .as_str()
            .expect("brief")
            .contains("\"name\": \"todo-app\""));

        let package = infer_project_manifest("mytool 패키지 python 으로 만들어줘").expect("py");
        assert_eq!(package["name"], "mytool");
        assert_eq!(
            manifest_paths(&package),
            vec![
                "mytool/__init__.py",
                "mytool/core.py",
                "mytool/cli.py",
                "README.md"
            ]
        );
    }

    #[test]
    fn a_package_name_is_sanitised_into_an_importable_module() {
        let package = infer_project_manifest("my-tool 패키지 파이썬으로 생성").expect("py");
        assert_eq!(package["name"], "my_tool");
    }

    #[test]
    fn an_explicit_filename_means_the_user_manages_paths() {
        assert_eq!(
            infer_project_manifest("index.html 이랑 style.css 만들어줘"),
            None
        );
    }

    #[test]
    fn a_destination_marker_is_what_makes_a_named_file_an_output() {
        // The three shapes the acceptance matrix actually sends.
        assert_eq!(
            requested_output_paths("인사말을 notes/hello.md 파일로 저장해줘"),
            vec!["notes/hello.md".to_string()]
        );
        assert_eq!(
            requested_output_paths(
                "code_review 스킬을 참고해서 notes/review_note.md에 리뷰 체크리스트를 써줘"
            ),
            vec!["notes/review_note.md".to_string()]
        );
        // The one that names two files: only the destination is an output.
        assert_eq!(
            requested_output_paths("README.md 첫 문단을 요약해 notes/summary.md로 저장해줘"),
            vec!["notes/summary.md".to_string()],
            "README.md is read, not written — declaring it would be a false NEEDS_REVIEW"
        );
        // English destinations.
        assert_eq!(
            requested_output_paths("summarise it and save to notes/summary.md"),
            vec!["notes/summary.md".to_string()]
        );
        assert_eq!(
            requested_output_paths("write the notes into docs/plan.md"),
            vec!["docs/plan.md".to_string()]
        );
    }

    #[test]
    fn an_input_particle_never_declares_an_output() {
        // `에서` / `로부터` mark a source. The particle lookahead is the only
        // thing standing between those and a wrong missing-file verdict.
        assert_eq!(
            requested_output_paths("README.md에서 첫 문단을 읽어서 요약을 저장해줘"),
            Vec::<String>::new()
        );
        assert_eq!(
            requested_output_paths("config.json로부터 값을 읽어서 만들어줘"),
            Vec::<String>::new()
        );
        // No creation verb at all: nothing is declared, whatever is named.
        assert_eq!(
            requested_output_paths("notes/hello.md에 뭐가 들어있어?"),
            Vec::<String>::new()
        );
        // A named file with no marker stays undeclared — the conservative half
        // of the rule, and what keeps the frozen coverage goldens byte-equal.
        assert_eq!(
            requested_output_paths("index.html 소개 페이지 만들어줘"),
            Vec::<String>::new()
        );
        assert_eq!(requested_output_paths(""), Vec::<String>::new());
        // A longer ASCII word is not a path: `hello.mdx` is not `hello.md`.
        assert_eq!(
            requested_output_paths("hello.mdx로 저장해줘"),
            Vec::<String>::new()
        );
    }

    #[test]
    fn an_unnamed_project_gets_the_generic_name() {
        let web = infer_project_manifest("웹사이트 css js 만들어줘").expect("web");
        assert_eq!(web["name"], "web-project");
        let react = infer_project_manifest("리액트로 만들어줘").expect("react");
        assert_eq!(react["name"], "react-app");
        let package = infer_project_manifest("파이썬 패키지 만들어줘").expect("py");
        assert_eq!(package["name"], "my_package");
    }
}
