"""Model-agnostic file content generation pipeline.

Small local models (gemma/qwen/llama 7B class) asked to "generate an HTML
file" commonly wrap the payload in chat noise: leading commentary ("Sure!
Here is your page:"), Markdown fences, ``<think>`` reasoning blocks,
trailing explanations, or an incomplete document. The previous direct-write
path saved that reply nearly verbatim, so weak models produced broken files.

This package makes the file-creation flow robust regardless of which LLM is
loaded, by treating the model as an untrusted content source:

1. Prompt   — extension-aware instructions anchored with the exact first
   line the reply must start with (small models follow examples, not rules).
   → :mod:`.prompting`
2. Extract  — strip reasoning blocks and conversational framing, pick the
   best fenced block, slice known document boundaries. → :mod:`.extraction`
3. Validate — per-extension structural checks (HTML document shape, JSON
   parses, CSS has rule blocks, refusal/chat detection). → :mod:`.validation`
4. Retry    — one corrective attempt that tells the model what was wrong.
   → :mod:`.orchestration`
5. Repair   — deterministic scaffolds guarantee the user still gets a valid
   file even when the model never produces usable output. → :mod:`.repair`

Alongside the five steps: :mod:`.sanitize` is the single write-side door every
entry point runs untrusted content through, :mod:`.inference` guesses a
filename or a whole project manifest when the user named neither, and
:mod:`.bundles` checks that a multi-file project's links hold together.

The pipeline is pure (no I/O, no FastAPI); the chat layer injects an async
``generate(context) -> str`` callable.

Split into these submodules in v11.3.0 with no behaviour change. Every name
the single module exposed still resolves from ``latticeai.core.file_generation``.

Stubbing note: a name rebound *here* changes only this module's binding — the
submodule that calls it holds its own. A test standing in for one of the
regex/helper seams patches the submodule that reads it (for example
``latticeai.core.file_generation.inference._PKG_NAME_RE``).
"""

from __future__ import annotations

# The single module bound this for its own use, which made it part of the
# surface callers could reach. Re-exported in the redundant-alias form so it
# reads as deliberate rather than as a leftover import.
from latticeai.core.quiet import quiet as quiet

from .bundles import _EXTERNAL_REF_PREFIXES as _EXTERNAL_REF_PREFIXES
from .bundles import _HTML_LOCAL_REF_RE as _HTML_LOCAL_REF_RE
from .bundles import _local_bundle_refs as _local_bundle_refs
from .bundles import repair_bundle_references as repair_bundle_references
from .bundles import validate_project_bundle as validate_project_bundle
from .extraction import _CHAT_LINE_RE as _CHAT_LINE_RE
from .extraction import _EXT_FENCE_LANGS as _EXT_FENCE_LANGS
from .extraction import _FENCE_RE as _FENCE_RE
from .extraction import _THINK_BLOCK_RE as _THINK_BLOCK_RE
from .extraction import _THINK_OPEN_RE as _THINK_OPEN_RE
from .extraction import _ext as _ext
from .extraction import _slice_html_document as _slice_html_document
from .extraction import _slice_json_document as _slice_json_document
from .extraction import _strip_chat_lines as _strip_chat_lines
from .extraction import extract_file_content as extract_file_content
from .inference import _CREATE_VERB_RE as _CREATE_VERB_RE
from .inference import _CSS_HINT_RE as _CSS_HINT_RE
from .inference import _EXPLICIT_FILENAME_RE as _EXPLICIT_FILENAME_RE
from .inference import _HTML_HINT_RE as _HTML_HINT_RE
from .inference import _JS_HINT_RE as _JS_HINT_RE
from .inference import _PACKAGE_HINT_RE as _PACKAGE_HINT_RE
from .inference import _PKG_NAME_RE as _PKG_NAME_RE
from .inference import _PROJECT_NAME_RE as _PROJECT_NAME_RE
from .inference import _PYTHON_HINT_RE as _PYTHON_HINT_RE
from .inference import _REACT_HINT_RE as _REACT_HINT_RE
from .inference import _TYPE_KEYWORDS as _TYPE_KEYWORDS
from .inference import _VITE_HINT_RE as _VITE_HINT_RE
from .inference import _python_package_manifest as _python_package_manifest
from .inference import _react_manifest as _react_manifest
from .inference import infer_file_target as infer_file_target
from .inference import infer_project_manifest as infer_project_manifest
from .orchestration import _salvage_score as _salvage_score
from .orchestration import generate_file_content as generate_file_content
from .prompting import _BUNDLE_HTML_MODULE_RULE as _BUNDLE_HTML_MODULE_RULE
from .prompting import _BUNDLE_HTML_RULE as _BUNDLE_HTML_RULE
from .prompting import _FIRST_LINE_HINTS as _FIRST_LINE_HINTS
from .prompting import _TYPE_RULES as _TYPE_RULES
from .prompting import _bundle_html_rule as _bundle_html_rule
from .prompting import build_file_generation_context as build_file_generation_context
from .repair import _repair_html as _repair_html
from .repair import repair_file_content as repair_file_content
from .sanitize import PREVIEWABLE_EXTENSIONS as PREVIEWABLE_EXTENSIONS
from .sanitize import sanitize_write_content as sanitize_write_content
from .validation import _BRACED_CODE_EXTENSIONS as _BRACED_CODE_EXTENSIONS
from .validation import _COMMENTARY_RE as _COMMENTARY_RE
from .validation import _COMPONENT_EXTENSIONS as _COMPONENT_EXTENSIONS
from .validation import _REFUSAL_RE as _REFUSAL_RE
from .validation import _check_balanced_delimiters as _check_balanced_delimiters
from .validation import _check_component_blocks as _check_component_blocks
from .validation import _looks_like_commentary as _looks_like_commentary
from .validation import _strip_code_literals as _strip_code_literals
from .validation import looks_like_refusal as looks_like_refusal
from .validation import validate_file_content as validate_file_content

__all__ = [
    "PREVIEWABLE_EXTENSIONS",
    "build_file_generation_context",
    "extract_file_content",
    "generate_file_content",
    "infer_file_target",
    "infer_project_manifest",
    "looks_like_refusal",
    "repair_bundle_references",
    "repair_file_content",
    "sanitize_write_content",
    "validate_file_content",
    "validate_project_bundle",
]
