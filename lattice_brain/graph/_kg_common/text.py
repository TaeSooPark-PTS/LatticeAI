"""Text cleaning and the legacy fixed-width chunk walk.

Moved verbatim out of the ``_kg_common`` grab-bag (v11.3.0 decomposition).
Nothing here reaches back into the rest of the package — the import graph is
``text ← relations ← extraction ← __init__`` — so this is the layer every
other one may build on.

The **typed chunker** that used to live here — ``typed_chunks`` and its four
strategies, ``chunk_strategy_for``, ``pdf_page_offsets``, and the three
readers that only ever consumed their output (``typed_chunk_meta_fields``,
``citation_locator``, ``page_for_offset``) — was removed in 11.8.0. Chunking
is native: ``lattice-ingest`` owns it, pinned by
``rust/lattice-ingest/tests/chunking_parity.rs`` against the committed
``rust/fixtures/chunking`` goldens. The worker imports only the extraction
helpers (``POST /worker/extract`` — see
``latticeai/api/worker_compute.py::build_extract_reply``), so the Python copy
had no shipping call site left; keeping a second boundary algorithm that
nothing runs is how two chunkers quietly stop agreeing.
"""

from __future__ import annotations

import re
from typing import List


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _chunks(text: str, size: int = 1200, overlap: int = 160) -> List[str]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return []
    chunks: List[str] = []
    start = 0
    while start < len(cleaned):
        end = min(len(cleaned), start + size)
        chunks.append(cleaned[start:end])
        if end >= len(cleaned):
            break
        start = max(0, end - overlap)
    return chunks
