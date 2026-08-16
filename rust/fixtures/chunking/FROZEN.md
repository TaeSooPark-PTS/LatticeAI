# FROZEN — last generating tree: commit e94ae6d

These chunking goldens were produced by
`scripts/generate_chunking_parity_fixtures.py` against
`lattice_brain/graph/_kg_common/text.py` — the four chunking strategies, their
boundary arithmetic and their per-chunk provenance — plus the chunk-id and
content-hash conventions from `lattice_brain/graph/ingest.py`.

v11.8.0 deleted the Python chunker and its generator. The committed JSON stays;
`rust/lattice-ingest/tests/chunking_parity.rs` keeps asserting it. Do not
regenerate.

If a golden and the Rust chunker disagree, **the chunker changed**. Every offset
in these files is a *character* offset, because Python slices `str` by code
points — a byte-sliced port disagrees on the first Korean sentence and panics on
the first emoji, and that is exactly the class of regression these files exist
to catch. Fix the port rather than the fixture.
