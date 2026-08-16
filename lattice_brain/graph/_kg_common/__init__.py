"""The graph's *compute* vocabulary, in one importable place.

This package was the shared surface of the SQLite knowledge graph: constants,
path/hash helpers, chunking, concept and triple extraction, and the schema
handles the write mixins composed into ``KnowledgeGraphStore``. v11.6.0 moved
every write into ``lattice-core``, and the store went with them.

What is left is the half that produces *structures*: chunk a document, pull the
concepts and triples out of a passage, classify a node type, infer an edge verb,
hash a file. ``POST /worker/parse``, ``POST /worker/embed`` and
``POST /worker/extract`` are the routes that answer with them; Rust decides what
to write.
"""

# ruff: noqa: F401,F841

import asyncio
import hashlib
import json
import logging
import math
import os
import platform
import re
import shutil
import sqlite3
import time
import zipfile
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from ...embeddings import LocalEmbeddingModel

# Static constants (projection/format versions, local-ingestion classification
# tables, OS exclusion lists) live in ._kg_constants; re-exported here so every
# existing ``from ._kg_common import <CONST>`` site is unaffected.
from ...quiet import quiet
from .._kg_constants import (  # noqa: E402
    _KG_DB_FORMAT_KEY,
    _KG_DB_FORMAT_VERSION,
    _PROJECTION_VERSION,
    _V2_WRITE_MASTER_KEY,
    COMMON_EXCLUDED_DIRS,
    COMMON_EXCLUDED_FILE_NAMES,
    COMMON_EXCLUDED_FILE_SUFFIXES,
    GRAPH_SCHEMA_VERSION,
    LINUX_EXCLUDED_PREFIXES,
    LOCAL_CODE_EXTENSIONS,
    LOCAL_DOCUMENT_EXTENSIONS,
    LOCAL_IMAGE_EXTENSIONS,
    LOCAL_SIZE_LIMITS,
    LOCAL_SLIDE_EXTENSIONS,
    LOCAL_SPREADSHEET_EXTENSIONS,
    LOCAL_SUPPORTED_EXTENSIONS,
    LOCAL_TEXT_EXTENSIONS,
    MACOS_EXCLUDED_PREFIXES,
    SENSITIVE_PATH_KEYWORDS,
    WINDOWS_EXCLUDED_NAMES,
)

# Pure fs/path/hash/classification helpers → ._kg_fsutil, re-exported so the
# static __all__ below forwards them to the graph mixins. Listed explicitly
# rather than star-imported: a star import here made every name in this
# module unverifiable to both ruff and mypy.
from .._kg_fsutil import (  # noqa: E402,F401
    _current_os_type,
    _drive_id_for_path,
    _excluded_directory_reason,
    _file_category,
    _is_hidden_path,
    _is_relative_to,
    _node_type_for_category,
    _now,
    _parse_iso,
    _parser_type_for_category,
    _path_fingerprint,
    _path_parts_lower,
    _recency_score,
    _root_warning,
    _safe_iso_from_stat_mtime,
    _sample_file,
    _sensitive_file_reason,
    _sha256_bytes,
    _sha256_text,
    _size_limit_for_category,
    _slug,
)
from ..json_utils import _json, _safe_loads
from ..runtime import get_llm_router, set_llm_router

# The logic this module used to hold inline lives in three cohesive submodules
# (v11.3.0 decomposition). Every name is re-exported below, so the twelve
# `from ._kg_common import *` consumers are unaffected — and `__all__` is still
# the single written-down star-import contract.
from .extraction import (  # noqa: E402
    _CONCEPT_STOP,
    _LLM_EXTRACT_CONCEPT_PROMPT,
    _LLM_EXTRACT_TRIPLE_PROMPT,
    ENABLE_LLM_EXTRACTION,
    _extract_concepts,
    _extract_concepts_rules,
    _extract_triples,
    _extract_triples_rules,
    _llm_extract_concepts,
    _llm_extract_triples,
    _semantic_items,
    _topic_candidates,
)
from .relations import (  # noqa: E402
    _NOT_PERSON_WORDS,
    COOCCURRENCE_CONCEPT_LIMIT,
    COOCCURRENCE_EDGE_WEIGHT,
    EDGE_VERB,
    VERB_EDGE_WEIGHT,
    _classify_node_type,
    _infer_edge,
    infer_edge_relation,
)
from .text import _chunks, _clean_text  # noqa: E402

# Static export list. This used to be `[name for name in globals() if not
# name.startswith("__")]`, which is invisible to a type checker: every
# `from ._kg_common import *` consumer then had *no* resolvable names, and
# mypy reported ~750 spurious `name-defined` errors across the graph
# package. `tests/unit/test_kg_common_exports.py` asserts this list still
# equals what the computed expression would produce, so it cannot drift.
__all__ = [
    "Any",
    "COMMON_EXCLUDED_DIRS",
    "COMMON_EXCLUDED_FILE_NAMES",
    "COMMON_EXCLUDED_FILE_SUFFIXES",
    "COOCCURRENCE_CONCEPT_LIMIT",
    "COOCCURRENCE_EDGE_WEIGHT",
    "Counter",
    "Dict",
    "EDGE_VERB",
    "ENABLE_LLM_EXTRACTION",
    "GRAPH_SCHEMA_VERSION",
    "Iterable",
    "Iterator",
    "LINUX_EXCLUDED_PREFIXES",
    "LOCAL_CODE_EXTENSIONS",
    "LOCAL_DOCUMENT_EXTENSIONS",
    "LOCAL_IMAGE_EXTENSIONS",
    "LOCAL_SIZE_LIMITS",
    "LOCAL_SLIDE_EXTENSIONS",
    "LOCAL_SPREADSHEET_EXTENSIONS",
    "LOCAL_SUPPORTED_EXTENSIONS",
    "LOCAL_TEXT_EXTENSIONS",
    "List",
    "LocalEmbeddingModel",
    "MACOS_EXCLUDED_PREFIXES",
    "Optional",
    "Path",
    "SENSITIVE_PATH_KEYWORDS",
    "Tuple",
    "VERB_EDGE_WEIGHT",
    "WINDOWS_EXCLUDED_NAMES",
    "_CONCEPT_STOP",
    "_KG_DB_FORMAT_KEY",
    "_KG_DB_FORMAT_VERSION",
    "_LLM_EXTRACT_CONCEPT_PROMPT",
    "_LLM_EXTRACT_TRIPLE_PROMPT",
    "_NOT_PERSON_WORDS",
    "_PROJECTION_VERSION",
    "_V2_WRITE_MASTER_KEY",
    "_chunks",
    "_classify_node_type",
    "_clean_text",
    "_current_os_type",
    "_drive_id_for_path",
    "_excluded_directory_reason",
    "_extract_concepts",
    "_extract_concepts_rules",
    "_extract_triples",
    "_extract_triples_rules",
    "_file_category",
    "_infer_edge",
    "_is_hidden_path",
    "_is_relative_to",
    "_json",
    "_llm_extract_concepts",
    "_llm_extract_triples",
    "_node_type_for_category",
    "_now",
    "_parse_iso",
    "_parser_type_for_category",
    "_path_fingerprint",
    "_path_parts_lower",
    "_recency_score",
    "_root_warning",
    "_safe_iso_from_stat_mtime",
    "_safe_loads",
    "_sample_file",
    "_semantic_items",
    "_sensitive_file_reason",
    "_sha256_bytes",
    "_sha256_text",
    "_size_limit_for_category",
    "_slug",
    "_topic_candidates",
    "asyncio",
    "contextmanager",
    "datetime",
    "get_llm_router",
    "hashlib",
    "infer_edge_relation",
    "json",
    "logging",
    "math",
    "os",
    "platform",
    "quiet",
    "re",
    "set_llm_router",
    "shutil",
    "sqlite3",
    "time",
    "zipfile",
]
