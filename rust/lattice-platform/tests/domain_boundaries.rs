//! Static architectural domain-boundary enforcement (v13.0.0).
//!
//! Verifies that the crate and domain layering rules documented in
//! `rust/lattice-platform/ARCHITECTURE.md` and `rust/lattice-agent/ARCHITECTURE.md`
//! hold in source code and cannot silently regress.

use std::path::{Path, PathBuf};

fn rust_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("rust workspace root")
        .to_path_buf()
}

fn collect_rs_files(dir: &Path) -> Vec<PathBuf> {
    fn visit(dir: &Path, out: &mut Vec<PathBuf>) {
        let Ok(entries) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                visit(&path, out);
            } else if path.extension().and_then(|ext| ext.to_str()) == Some("rs") {
                out.push(path);
            }
        }
    }
    let mut files = Vec::new();
    visit(dir, &mut files);
    files.sort();
    files
}

/// Strip line comments (`//`, `//!`, `///`) and optionally truncate at `#[cfg(test)]`.
fn code_lines(source: &str, stop_at_cfg_test: bool) -> Vec<(usize, String)> {
    let mut out = Vec::new();
    for (idx, line) in source.lines().enumerate() {
        let trimmed = line.trim();
        if stop_at_cfg_test && trimmed.starts_with("#[cfg(test)]") {
            break;
        }
        if trimmed.starts_with("//") {
            continue;
        }
        let code_part = match line.find("//") {
            Some(pos) => &line[..pos],
            None => line,
        };
        out.push((idx + 1, code_part.to_string()));
    }
    out
}

fn assert_no_forbidden_references(
    dir: &Path,
    forbidden: &[&str],
    stop_at_cfg_test: bool,
    rule_description: &str,
) {
    let mut violations = Vec::new();
    for path in collect_rs_files(dir) {
        let is_test_module = path
            .file_stem()
            .and_then(|s| s.to_str())
            .is_some_and(|stem| stem == "tests" || stem.starts_with("tests_"));
        if stop_at_cfg_test && is_test_module {
            continue;
        }
        let source = std::fs::read_to_string(&path).expect("read rust source");
        for (lineno, line) in code_lines(&source, stop_at_cfg_test) {
            for pattern in forbidden {
                if line.contains(pattern) {
                    violations.push(format!(
                        "{}:{lineno}: found `{pattern}` ({rule_description})",
                        path.display()
                    ));
                }
            }
        }
    }
    assert!(
        violations.is_empty(),
        "Domain boundary violation(s):
{}",
        violations.join(
            "
"
        )
    );
}

#[test]
fn lattice_platform_domains_respect_horizontal_decoupling() {
    let platform_src = rust_root().join("lattice-platform/src");

    assert_no_forbidden_references(
        &platform_src.join("modelops"),
        &[
            "crate::adminops",
            "crate::governance",
            "crate::toolsurface",
            "crate::knowledge",
        ],
        false,
        "modelops must not reach sideways into adminops, governance, toolsurface, or knowledge",
    );

    assert_no_forbidden_references(
        &platform_src.join("shell"),
        &[
            "crate::adminops",
            "crate::governance",
            "crate::knowledge",
            "crate::modelops",
            "crate::toolsurface",
            "crate::workspaceos",
        ],
        false,
        "shell is purely static UI / redirect transport and must not import sibling domains",
    );

    assert_no_forbidden_references(
        &platform_src.join("knowledge"),
        &["workspaceos::workspace::redact"],
        false,
        "knowledge must consume canonical secret redaction from lattice_core::redact",
    );
}

#[test]
fn lattice_agent_kernel_and_pure_layers_never_import_inbound_surface() {
    let agent_src = rust_root().join("lattice-agent/src");

    assert_no_forbidden_references(
        &agent_src.join("kernel"),
        &["crate::surface"],
        true,
        "lattice-agent::kernel production code must call seam/parse/content/tools downward, never surface",
    );

    assert_no_forbidden_references(
        &agent_src.join("tools"),
        &["crate::surface"],
        true,
        "lattice-agent::tools production code must call seam downward, never surface",
    );

    assert_no_forbidden_references(
        &agent_src.join("parse"),
        &["crate::kernel", "crate::surface", "crate::tools", "crate::seam"],
        true,
        "lattice-agent::parse must remain pure text/value parsing with zero kernel/surface/tools I/O",
    );

    assert_no_forbidden_references(
        &agent_src.join("content"),
        &["crate::kernel", "crate::surface", "crate::tools", "crate::seam"],
        true,
        "lattice-agent::content must remain pure byte validation/repair with zero kernel/surface/tools I/O",
    );
}

#[test]
fn lattice_chat_consumes_canonical_lattice_agent_domain_groups() {
    let chat_src = rust_root().join("lattice-chat/src");
    assert_no_forbidden_references(
        &chat_src,
        &[
            "lattice_agent::inference",
            "lattice_agent::sanitize",
            "lattice_agent::sandbox",
            "lattice_agent::state",
            "lattice_agent::worker",
        ],
        false,
        "lattice-chat must import canonical lattice_agent::{kernel,parse,content,tools,seam} modules",
    );
}
