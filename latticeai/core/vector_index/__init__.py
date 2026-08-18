"""Search-side vector index backends (HNSW append + query).

``POST /worker/vector/query`` serves this sidecar. ``lattice-retrieval``
asks for top-k candidates when ``LATTICEAI_VECTOR_INDEX=hnsw`` and
re-scores them exactly. Default env stays the brute scan.

This package is the Python HNSW sidecar: a disposable ``.hnsw`` graph
next to the brain database, with incremental ``add_items`` so a write no
longer rebuilds the whole graph.

Rebuild (not append) when:

* the embedder identity (``model_id`` / dim / space) changed
* any previously indexed id is absent (deletion). Tombstones exist in
  hnswlib (``mark_deleted``) but a deletion-heavy store (more than
  :data:`DELETE_REBUILD_RATIO` of the graph, or any delete when the
  sidecar was loaded without source vectors) is cheaper to rebuild
  than to fragment.
"""

from .hnsw import (
    DELETE_REBUILD_RATIO,
    HNSW_BACKEND,
    HNSW_META_SUFFIX,
    HNSW_SUFFIX,
    HNSWLIB_MODULE,
    HnswIndex,
    hnswlib_available,
    load_hnswlib,
    sidecar_paths,
)
from .sidecar import (
    GRAPH_DB_NAME,
    VECTOR_QUERY_K_CAP,
    decode_f32le,
    query_sidecar,
    reset_sidecar_cache,
    resolve_graph_db,
    sidecar_fingerprint,
    sidecar_freshness,
)

__all__ = [
    "DELETE_REBUILD_RATIO",
    "GRAPH_DB_NAME",
    "HNSW_BACKEND",
    "HNSW_META_SUFFIX",
    "HNSW_SUFFIX",
    "HNSWLIB_MODULE",
    "HnswIndex",
    "VECTOR_QUERY_K_CAP",
    "decode_f32le",
    "hnswlib_available",
    "load_hnswlib",
    "query_sidecar",
    "reset_sidecar_cache",
    "resolve_graph_db",
    "sidecar_fingerprint",
    "sidecar_freshness",
    "sidecar_paths",
]
