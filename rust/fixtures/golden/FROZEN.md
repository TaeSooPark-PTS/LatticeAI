# FROZEN — last generating tree: commit fc65e60

These retrieval parity goldens were produced by
`scripts/generate_rust_parity_fixtures.py` (and
`scripts/parity_fixture_corpus_{context,docgen}.py`) against the Python
`KnowledgeGraphStore` / chat / history write path.

WP-P1 deleted that write path. The generator cannot survive on the keep-set.
The committed JSON and `parity_store.sqlite` stay; Rust
`lattice-retrieval` tests keep asserting them. Do not regenerate.
