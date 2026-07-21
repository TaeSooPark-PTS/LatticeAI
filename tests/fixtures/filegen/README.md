# Filegen regression golden files

Each pair in this directory pins the write-side sanitize contract
(`latticeai.core.file_generation.sanitize_write_content`) against realistic
dirty model output:

| Pair | Target type | Failure mode reproduced |
| --- | --- | --- |
| `fenced_html_with_prose.*` | `.html` | prose wrapper + Markdown fence around a full document |
| `json_trailing_commentary.*` | `.json` | valid JSON followed by chat commentary |
| `css_in_fence.*` | `.css` | stylesheet wrapped in prose + fence (see note below) |
| `py_markdown_wrapper.*` | `.py` | chat greeting + ```` ```python ```` fence + trailing chat |
| `truncated_html.*` | `.html` | token-limit truncation (no `</body>`/`</html>`) |

`<case>.dirty.txt` is the raw model reply; `<case>.expected.<ext>` is the file
that must be written to disk after sanitize. The test
(`tests/unit/test_filegen_golden_files.py`) compares them with normalized
newlines (CRLF→LF, ignoring one trailing newline) — everything else is exact.

## Changing prompts or the sanitizer? Review golden diffs deliberately.

These files are the regression contract for file generation. If a change to
`file_generation.py` (extraction, validation, repair) or to the generation
prompts alters any sanitized output, the golden test will fail — that is the
point. Do **not** blindly regenerate the expected files to make the test
green: diff old vs. new expected output, confirm the new output is what a
user should receive on disk, and only then update the golden file, in the
same change, with the reasoning in the commit message.

## Known gap pinned by a strict xfail

`css_in_fence` currently *fails* to sanitize: `.css` validation only checks
for `{`/`}`, so a fenced/chatty stylesheet passes validation and is written
verbatim (fences and all). The expected file encodes the **desired** clean
output and its test is marked `xfail(strict=True)`. When the sanitizer gains
a fence check for CSS, the xpass will fail the suite — remove the marker and
re-review the golden at that moment.
