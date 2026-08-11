"""Bundle-level checks: does this set of files hold together?

A multi-file project is only as valid as its links. These helpers find the
local references an HTML document makes, repoint the ones a weak model got
almost right, and report every file that is invalid or dangling.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from .extraction import _ext
from .validation import validate_file_content

_HTML_LOCAL_REF_RE = re.compile(
    r"(?:href|src)\s*=\s*[\"']([^\"'#?]+)[\"']", re.IGNORECASE
)
_EXTERNAL_REF_PREFIXES = ("http://", "https://", "//", "data:", "mailto:", "tel:", "javascript:")


def _local_bundle_refs(html: str) -> List[str]:
    """File references inside an HTML document that must exist in the bundle."""
    refs: List[str] = []
    for ref in _HTML_LOCAL_REF_RE.findall(html or ""):
        candidate = ref.strip()
        if not candidate or candidate.startswith(_EXTERNAL_REF_PREFIXES):
            continue
        if "." not in candidate.rsplit("/", 1)[-1]:
            continue  # anchors / routes, not files
        refs.append(candidate)
    return refs


def repair_bundle_references(files: Dict[str, str]) -> Tuple[Dict[str, str], List[str]]:
    """Deterministically point dangling HTML refs at real bundle files.

    A weak model asked for ``style.css`` sometimes links ``styles.css``. When
    a referenced file is missing but the bundle contains exactly one file of
    the same extension, the reference is rewritten. Returns ``(files, fixes)``.
    """
    names = {p.rsplit("/", 1)[-1] for p in files}
    fixes: List[str] = []
    repaired = dict(files)
    for path, content in files.items():
        if _ext(path) not in (".html", ".htm"):
            continue
        updated = content
        for ref in _local_bundle_refs(content):
            base = ref.rsplit("/", 1)[-1]
            if base in names:
                continue
            same_ext = [n for n in names if _ext(n) == _ext(base)]
            if len(same_ext) == 1:
                updated = updated.replace(ref, same_ext[0])
                fixes.append(f"{path}: '{ref}' -> '{same_ext[0]}'")
        if updated != content:
            repaired[path] = updated
    return repaired, fixes


def validate_project_bundle(files: Dict[str, str]) -> Dict[str, Any]:
    """Bundle-level verification: every file valid, every HTML ref resolvable."""
    issues: List[str] = []
    per_file: Dict[str, Dict[str, Any]] = {}
    names = {p.rsplit("/", 1)[-1] for p in files}
    for path, content in files.items():
        ok, reason = validate_file_content(content, path)
        per_file[path] = {"valid": ok, "reason": reason}
        if not ok:
            issues.append(f"{path}: {reason}")
        if _ext(path) in (".html", ".htm"):
            for ref in _local_bundle_refs(content):
                if ref.rsplit("/", 1)[-1] not in names:
                    issues.append(f"{path}: references missing file '{ref}'")
    return {"ok": not issues, "issues": issues, "files": per_file}
