use lattice_core::tokens::TokenPrefix;
use tree_sitter::{Language, Node, Parser};

use super::segments::Segment;

const MAX_AST_DEPTH: usize = 3;
const MAX_SYMBOL_CHARS: usize = 80;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CodeLanguage {
    Rust,
    Python,
    TypeScript,
    Tsx,
    JavaScript,
}

impl CodeLanguage {
    pub fn from_filename(filename: &str) -> Option<Self> {
        let lowered = filename.trim().to_ascii_lowercase();
        let path = lowered.split(['?', '#']).next().unwrap_or("");
        let extension = path.rsplit_once('.').map(|(_, ext)| ext)?;
        match extension {
            "rs" => Some(Self::Rust),
            "py" | "pyi" => Some(Self::Python),
            "ts" | "mts" | "cts" => Some(Self::TypeScript),
            "tsx" => Some(Self::Tsx),
            "js" | "jsx" | "mjs" | "cjs" => Some(Self::JavaScript),
            _ => None,
        }
    }

    pub fn label(&self) -> &'static str {
        match self {
            Self::Rust => "rust",
            Self::Python => "python",
            Self::TypeScript => "typescript",
            Self::Tsx => "tsx",
            Self::JavaScript => "javascript",
        }
    }

    fn grammar(&self) -> Language {
        match self {
            Self::Rust => tree_sitter_rust::LANGUAGE.into(),
            Self::Python => tree_sitter_python::LANGUAGE.into(),
            Self::TypeScript => tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            Self::Tsx => tree_sitter_typescript::LANGUAGE_TSX.into(),
            Self::JavaScript => tree_sitter_javascript::LANGUAGE.into(),
        }
    }
}

struct Source<'a> {
    text: &'a str,
    byte_starts: Vec<usize>,
}

impl Source<'_> {
    fn char_at(&self, byte: usize) -> usize {
        self.byte_starts.partition_point(|start| *start < byte)
    }

    fn node_text(&self, node: Node<'_>) -> &str {
        self.text
            .get(node.start_byte()..node.end_byte())
            .unwrap_or("")
    }
}

fn short_kind(kind: &str) -> String {
    let mapped = match kind {
        "function_item" | "function_signature_item" => "fn",
        "impl_item" => "impl",
        "struct_item" => "struct",
        "enum_item" | "enum_declaration" => "enum",
        "trait_item" => "trait",
        "mod_item" => "mod",
        "const_item" => "const",
        "static_item" => "static",
        "type_item" | "type_alias_declaration" => "type",
        "macro_definition" => "macro",
        "function_definition" => "def",
        "class_definition" | "class_declaration" | "abstract_class_declaration" | "class" => {
            "class"
        }
        "function_declaration" | "generator_function_declaration" => "function",
        "method_definition" => "method",
        "interface_declaration" => "interface",
        "lexical_declaration" | "variable_declaration" => "const",
        other => {
            let mut trimmed = other;
            for suffix in ["_item", "_definition", "_declaration", "_statement"] {
                if let Some(stripped) = trimmed.strip_suffix(suffix) {
                    trimmed = stripped;
                    break;
                }
            }
            return trimmed.replace('_', " ");
        }
    };
    mapped.to_string()
}

fn unwrap_declaration(node: Node<'_>) -> Node<'_> {
    match node.kind() {
        "export_statement" => node.child_by_field_name("declaration").unwrap_or(node),
        "decorated_definition" => node.child_by_field_name("definition").unwrap_or(node),
        _ => node,
    }
}

fn compact(text: &str) -> String {
    let joined = text.split_whitespace().collect::<Vec<_>>().join(" ");
    joined.chars().take(MAX_SYMBOL_CHARS).collect()
}

fn symbol_name(node: Node<'_>, source: &Source<'_>) -> Option<String> {
    if let Some(name) = node.child_by_field_name("name") {
        return Some(compact(source.node_text(name)));
    }
    if node.kind() == "impl_item" {
        let ty = node.child_by_field_name("type")?;
        return Some(match node.child_by_field_name("trait") {
            Some(tr) => format!(
                "{} for {}",
                compact(source.node_text(tr)),
                compact(source.node_text(ty))
            ),
            None => compact(source.node_text(ty)),
        });
    }
    if matches!(node.kind(), "lexical_declaration" | "variable_declaration") {
        let mut cursor = node.walk();
        let declarator = node.named_children(&mut cursor).next()?;
        let name = declarator.child_by_field_name("name")?;
        return Some(compact(source.node_text(name)));
    }
    None
}

fn symbol_label(node: Node<'_>, source: &Source<'_>) -> Option<String> {
    let target = unwrap_declaration(node);
    let name = symbol_name(target, source).filter(|name| !name.is_empty())?;
    Some(format!("{} {name}", short_kind(target.kind())))
}

fn join_path(parent: Option<&str>, label: Option<String>) -> Option<String> {
    match (parent, label) {
        (Some(parent), Some(label)) => Some(format!("{parent} > {label}")),
        (Some(parent), None) => Some(parent.to_string()),
        (None, label) => label,
    }
}

struct Walk<'a, 'b> {
    source: &'a Source<'b>,
    prefix: &'a TokenPrefix,
    split_over_tokens: usize,
}

impl Walk<'_, '_> {
    fn collect(
        &self,
        container: Node<'_>,
        parent_path: Option<&str>,
        start: usize,
        end: usize,
        depth: usize,
    ) -> Vec<Segment> {
        let mut out: Vec<Segment> = Vec::new();
        let mut cursor_at = start;
        let mut walker = container.walk();
        let children: Vec<Node<'_>> = container.named_children(&mut walker).collect();
        for child in children {
            let child_end = self.source.char_at(child.end_byte()).min(end);
            if child_end <= cursor_at {
                continue;
            }
            let path = join_path(parent_path, symbol_label(child, self.source));
            let oversized = self.prefix.tokens(cursor_at, child_end) > self.split_over_tokens;
            let body = unwrap_declaration(child).child_by_field_name("body");
            if oversized && depth < MAX_AST_DEPTH {
                if let Some(body) = body.filter(|body| body.named_child_count() > 0) {
                    let inner =
                        self.collect(body, path.as_deref(), cursor_at, child_end, depth + 1);
                    if !inner.is_empty() {
                        out.extend(inner);
                        cursor_at = child_end;
                        continue;
                    }
                }
            }
            out.push(Segment {
                start: cursor_at,
                end: child_end,
                heading_path: None,
                symbol_path: path,
            });
            cursor_at = child_end;
        }
        if cursor_at < end {
            match out.last_mut() {
                Some(last) => last.end = end,
                None => out.push(Segment {
                    start,
                    end,
                    heading_path: None,
                    symbol_path: parent_path.map(str::to_string),
                }),
            }
        }
        out
    }
}

pub fn ast_segments(
    cleaned: &[char],
    prefix: &TokenPrefix,
    language: CodeLanguage,
    split_over_tokens: usize,
) -> Option<Vec<Segment>> {
    let text: String = cleaned.iter().collect();
    let mut parser = Parser::new();
    parser.set_language(&language.grammar()).ok()?;
    let tree = parser.parse(&text, None)?;
    let root = tree.root_node();
    if root.has_error() || root.named_child_count() == 0 {
        return None;
    }
    let mut byte_starts = Vec::with_capacity(cleaned.len());
    let mut offset = 0usize;
    for c in cleaned {
        byte_starts.push(offset);
        offset += c.len_utf8();
    }
    let source = Source {
        text: &text,
        byte_starts,
    };
    let walk = Walk {
        source: &source,
        prefix,
        split_over_tokens: split_over_tokens.max(1),
    };
    let segments = walk.collect(root, None, 0, cleaned.len(), 0);
    (!segments.is_empty()).then_some(segments)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run(text: &str, language: CodeLanguage, split: usize) -> Option<Vec<Segment>> {
        let cleaned: Vec<char> = text.chars().collect();
        let prefix = TokenPrefix::new(&cleaned);
        ast_segments(&cleaned, &prefix, language, split)
    }

    fn symbols(segments: &[Segment]) -> Vec<Option<String>> {
        segments.iter().map(|s| s.symbol_path.clone()).collect()
    }

    fn assert_covers(segments: &[Segment], len: usize) {
        assert_eq!(segments.first().map(|s| s.start), Some(0));
        assert_eq!(segments.last().map(|s| s.end), Some(len));
        for pair in segments.windows(2) {
            assert_eq!(pair[0].end, pair[1].start);
        }
    }

    #[test]
    fn languages_come_from_the_file_extension() {
        assert_eq!(
            CodeLanguage::from_filename("src/lib.RS"),
            Some(CodeLanguage::Rust)
        );
        assert_eq!(
            CodeLanguage::from_filename("a.py"),
            Some(CodeLanguage::Python)
        );
        assert_eq!(
            CodeLanguage::from_filename("a.pyi"),
            Some(CodeLanguage::Python)
        );
        assert_eq!(
            CodeLanguage::from_filename("a.ts"),
            Some(CodeLanguage::TypeScript)
        );
        assert_eq!(
            CodeLanguage::from_filename("a.tsx"),
            Some(CodeLanguage::Tsx)
        );
        assert_eq!(
            CodeLanguage::from_filename("a.mjs?x=1"),
            Some(CodeLanguage::JavaScript)
        );
        assert_eq!(CodeLanguage::from_filename("a.go"), None);
        assert_eq!(CodeLanguage::from_filename("Makefile"), None);
        for language in [
            CodeLanguage::Rust,
            CodeLanguage::Python,
            CodeLanguage::TypeScript,
            CodeLanguage::Tsx,
            CodeLanguage::JavaScript,
        ] {
            assert!(!language.label().is_empty());
        }
    }

    #[test]
    fn rust_items_become_symbol_segments_and_big_impls_split_into_methods() {
        let body = "        let x = 1;\n".repeat(40);
        let text = format!(
            "use std::fmt;\n\n/// docs\npub struct Foo;\n\nimpl Display for Foo {{\n    fn fmt(&self) {{\n{body}    }}\n    fn other(&self) {{}}\n}}\n\nfn tail() {{}}\n"
        );
        let segments = run(&text, CodeLanguage::Rust, 60).expect("parses");
        assert_covers(&segments, text.chars().count());
        let names = symbols(&segments);
        assert!(names.contains(&Some("struct Foo".into())), "{names:?}");
        assert!(
            names.contains(&Some("impl Display for Foo > fn other".into())),
            "{names:?}"
        );
        assert!(names
            .iter()
            .flatten()
            .any(|n| n.starts_with("impl Display for Foo > fn fmt")));
        assert!(names.contains(&Some("fn tail".into())));
    }

    #[test]
    fn python_typescript_and_javascript_name_their_declarations() {
        let py = "import os\n\n@cache\ndef load(path):\n    return path\n\nclass Store:\n    def get(self):\n        return 1\n";
        let segments = run(py, CodeLanguage::Python, 500).expect("python parses");
        assert_covers(&segments, py.chars().count());
        let names = symbols(&segments);
        assert!(names.contains(&Some("def load".into())), "{names:?}");
        assert!(names.contains(&Some("class Store".into())));

        let ts = "export interface Item { id: string }\nexport const make = (id: string): Item => ({ id });\ntype Alias = Item;\n";
        let names = symbols(&run(ts, CodeLanguage::TypeScript, 500).expect("ts parses"));
        assert!(names.contains(&Some("interface Item".into())), "{names:?}");
        assert!(names.contains(&Some("const make".into())), "{names:?}");
        assert!(names.contains(&Some("type Alias".into())), "{names:?}");

        let tsx = "export function View() { return <div>hi</div>; }\n";
        let names = symbols(&run(tsx, CodeLanguage::Tsx, 500).expect("tsx parses"));
        assert_eq!(names, vec![Some("function View".into())]);

        let js = "class Box { open() { return 1 } }\nfunction shut() {}\nlet n = 1;\n";
        let names = symbols(&run(js, CodeLanguage::JavaScript, 500).expect("js parses"));
        assert!(names.contains(&Some("class Box".into())), "{names:?}");
        assert!(names.contains(&Some("function shut".into())));
        assert!(names.contains(&Some("const n".into())));
    }

    #[test]
    fn broken_or_empty_sources_fall_back_to_prefix_splitting() {
        assert!(run("fn broken( {", CodeLanguage::Rust, 100).is_none());
        assert!(run("", CodeLanguage::Python, 100).is_none());
        assert!(run("   \n", CodeLanguage::JavaScript, 100).is_none());
    }

    #[test]
    fn multibyte_sources_map_byte_offsets_back_to_characters() {
        let text = "fn a() { let s = \"가나\"; }\nfn b() { let s = \"한글\"; }\n";
        let segments = run(text, CodeLanguage::Rust, 500).expect("parses");
        assert_covers(&segments, text.chars().count());
        assert_eq!(symbols(&segments)[1], Some("fn b".into()));
    }

    #[test]
    fn kinds_without_a_mapping_are_shortened_generically() {
        assert_eq!(short_kind("union_item"), "union");
        assert_eq!(short_kind("foreign_mod_item"), "foreign mod");
        assert_eq!(short_kind("weird"), "weird");
        assert_eq!(join_path(Some("a"), None).as_deref(), Some("a"));
        assert_eq!(join_path(None, None), None);
    }
}
