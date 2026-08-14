"""Graph compute: chunking, extraction, parsing, and the embedder fingerprint.

The store, the schema, the write mixins, the ANN index and the retrieval stack
all moved to ``lattice-core`` / ``lattice-retrieval`` in v11.6.0 §Wave 2.5. This
package keeps only what turns bytes into structures a writer can use, which is
why it now exports nothing itself — import the submodule you mean
(``._kg_common``, ``.documents``, ``.retrieval_vector.fingerprint``,
``.runtime``).
"""

from __future__ import annotations

__all__: list[str] = []
