"""The two content hashes the pipeline names things by.

:func:`content_hash_text` matches the store's own hashing scheme, so a text
payload hashed here and a text payload hashed there dedupe against each other.
:func:`_file_digest` streams a file instead of reading it whole — it is the key
a video's keyframe folder is named by, and videos are large.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def content_hash_text(text: str) -> str:
    """Canonical content hash for a text payload (matches store hashing scheme)."""
    return hashlib.sha256((text or "").encode("utf-8", "ignore")).hexdigest()


def _file_digest(path: Path) -> str:
    """Streaming sha256 of a file — the key a video's frame folder is named by."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
