# Knowledge Graph

The Knowledge Graph is the durable center of Lattice AI.

## What It Stores

- files
- documents
- images
- screenshots
- conversations
- notes
- decisions
- work history
- generated artifacts
- evidence links

## Pipeline

```text
source material
  -> multimodal understanding
  -> entity extraction
  -> relationship extraction
  -> evidence storage
  -> graph update
  -> context assembly
  -> AI advice, analysis, documents, automation
```

## Design Rules

- Preserve legacy read compatibility.
- Prefer reprojection over destructive mutation.
- Keep evidence with graph facts.
- Keep rollback paths available.
- Treat models as replaceable graph workers.

