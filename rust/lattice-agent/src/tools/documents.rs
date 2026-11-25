const DOCUMENT_TOOL_TARGETS: [(&str, &str, &str); 4] = [
    ("create_docx", "generated_documents", ".docx"),
    ("create_pdf", "generated_pdfs", ".pdf"),
    ("create_pptx", "generated_presentations", ".pptx"),
    ("create_xlsx", "generated_spreadsheets", ".xlsx"),
];

fn safe_filename(name: &str, suffix: &str) -> String {
    let fallback = format!("artifact{suffix}");
    let source = if name.is_empty() { &fallback } else { name };
    let mut base = crate::kernel::transcript::path_name(source).to_string();
    if base.is_empty() {
        base = fallback.clone();
    }
    if !base.to_lowercase().ends_with(suffix) {
        base.push_str(suffix);
    }
    let safe: String = base
        .chars()
        .map(|character| {
            if character.is_alphanumeric() || matches!(character, '-' | '_' | '.' | ' ') {
                character
            } else {
                '_'
            }
        })
        .collect();
    let trimmed = safe.trim().to_string();
    if trimmed.is_empty() {
        fallback
    } else {
        trimmed
    }
}

pub fn document_output_target(tool_name: &str, filename: &str) -> Option<String> {
    DOCUMENT_TOOL_TARGETS
        .iter()
        .find(|(name, _, _)| *name == tool_name)
        .map(|(_, directory, suffix)| format!("{directory}/{}", safe_filename(filename, suffix)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn each_creator_lands_in_its_own_directory_with_its_own_suffix() {
        assert_eq!(
            document_output_target("create_docx", "report"),
            Some("generated_documents/report.docx".into())
        );
        assert_eq!(
            document_output_target("create_pdf", "a/b/notes.pdf"),
            Some("generated_pdfs/notes.pdf".into())
        );
        assert_eq!(
            document_output_target("create_pptx", "deck.PPTX"),
            Some("generated_presentations/deck.PPTX".into())
        );
        assert_eq!(
            document_output_target("create_xlsx", ""),
            Some("generated_spreadsheets/artifact.xlsx".into())
        );
    }

    #[test]
    fn tools_outside_the_table_write_where_they_are_told() {
        for tool in ["write_file", "edit_file", "create_web_project", "unknown"] {
            assert_eq!(document_output_target(tool, "a.md"), None, "{tool}");
        }
    }

    #[test]
    fn unsafe_characters_are_replaced_not_rejected() {
        assert_eq!(
            document_output_target("create_docx", "../../etc/pa$$wd"),
            Some("generated_documents/pa__wd.docx".into()),
            "the basename is taken first, so traversal cannot survive"
        );
        assert_eq!(
            document_output_target("create_docx", "보고서"),
            Some("generated_documents/보고서.docx".into()),
            "alphanumeric is Unicode-wide, as `str.isalnum` is"
        );
    }
}
