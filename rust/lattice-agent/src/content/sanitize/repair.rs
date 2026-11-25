use serde_json::Value;

use crate::content::pydiff::splitlines;
use crate::parse::pystr::char_slice;

use super::extract::{slice_html_document, slice_json_document};
use super::python::python_parses;
use super::text::{contains_ci, ext_of, html_tag, matched};
use super::validate::looks_like_refusal;

fn escape_html(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}

pub fn repair_file_content(content: &str, target_path: &str, user_request: &str) -> String {
    let ext = ext_of(target_path);
    let mut salvage = content.trim().to_string();
    if looks_like_refusal(&salvage) {
        salvage = String::new();
    }

    if ext == ".html" || ext == ".htm" {
        return repair_html(&salvage, user_request);
    }
    if ext == ".json" {
        if let Some(sliced) = slice_json_document(&salvage) {
            return sliced;
        }
        let quoted = |text: &str| Value::String(text.to_string()).to_string();
        return format!(
            "{{\n  \"request\": {},\n  \"content\": {}\n}}",
            quoted(user_request),
            quoted(&salvage)
        );
    }
    if ext == ".py" && !salvage.is_empty() {
        if python_parses(&salvage).is_ok() {
            return salvage;
        }
        let commented = splitlines(&salvage)
            .into_iter()
            .map(|line| format!("# {line}"))
            .collect::<Vec<_>>()
            .join("\n");
        return format!(
            "# TODO: model produced invalid Python for: {user_request}\n\
# The draft below is preserved as comments — fix and uncomment.\n\
{commented}\n"
        );
    }
    if !salvage.is_empty() {
        return salvage;
    }
    let comment = match ext.as_str() {
        ".py" | ".sh" => "# TODO: model produced no usable content for: ",
        ".js" | ".jsx" | ".ts" | ".tsx" => "// TODO: model produced no usable content for: ",
        ".css" => "/* TODO: model produced no usable content for: ",
        ".sql" => "-- TODO: model produced no usable content for: ",
        _ => "",
    };
    if ext == ".css" {
        return format!("{comment}{user_request} */\n");
    }
    if !comment.is_empty() {
        return format!("{comment}{user_request}\n");
    }
    format!("{user_request}\n")
}

fn repair_html(salvage: &str, user_request: &str) -> String {
    if contains_ci(salvage, "<html") || contains_ci(salvage, "<!doctype") {
        let mut doc = slice_html_document(salvage);
        let low = doc.to_lowercase();
        if !low.contains("</body>") && low.contains("<body") {
            doc.push_str("\n</body>");
        }
        if !low.contains("</html>") {
            doc.push_str("\n</html>");
        }
        return doc;
    }
    let body = if matched(html_tag(), salvage) {
        salvage.to_string()
    } else if !salvage.is_empty() {
        splitlines(salvage)
            .into_iter()
            .filter(|line| !line.trim().is_empty())
            .map(|line| format!("  <p>{}</p>", escape_html(line)))
            .collect::<Vec<_>>()
            .join("\n")
    } else {
        format!("  <p>{}</p>", escape_html(user_request))
    };
    let title = escape_html(if user_request.is_empty() {
        "Generated page"
    } else {
        char_slice(user_request, 60)
    });
    format!(
        concat!(
            "<!DOCTYPE html>\n",
            "<html lang=\"ko\">\n",
            "<head>\n",
            "  <meta charset=\"utf-8\">\n",
            "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n",
            "  <title>{title}</title>\n",
            "  <style>\n",
            "    body {{ font-family: system-ui, sans-serif; margin: 2rem auto; ",
            "max-width: 720px; line-height: 1.6; padding: 0 1rem; }}\n",
            "  </style>\n",
            "</head>\n",
            "<body>\n",
            "{body}\n",
            "</body>\n",
            "</html>",
        ),
        title = title,
        body = body,
    )
}
