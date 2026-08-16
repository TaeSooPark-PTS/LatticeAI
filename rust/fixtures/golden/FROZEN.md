# FROZEN — last generating tree: commit fc65e60

These retrieval parity goldens — including `embeddings_golden.json`, which
`lattice-core`'s `tests/golden_embeddings.rs` reads — were produced by
`scripts/generate_rust_parity_fixtures.py` (and
`scripts/parity_fixture_corpus_{context,docgen}.py`) against the Python
`KnowledgeGraphStore` / chat / history write path and `lattice_brain.embeddings`.

WP-P1 deleted that write path and v11.8.0 deleted the generators themselves, so
there is nothing left to regenerate with. The committed JSON and
`parity_store.sqlite` stay; `lattice-retrieval`'s `tests/{parity,suites}.rs` and
`lattice-core`'s `tests/golden_embeddings.rs` keep asserting them.

If a golden and the Rust port disagree, **the port changed**. Fix the port, or —
if the change is deliberate — delete the rows it makes obsolete and say so here.
Never rewrite a row's expected values.
