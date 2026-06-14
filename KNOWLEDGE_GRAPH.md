# Knowledge Graph

The Knowledge Graph is durable infrastructure inside the Lattice AI Brain.

The Brain is the product. The graph is the deepest structured layer users can
open when they want to inspect how memories, knowledge, sources, and
relationships connect. It should grow out of the Brain experience rather than
be presented as the first or primary product surface.

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
  -> Brain recall, conversation, analysis, documents, automation
```

## Design Rules

- Keep the Brain-first UX contract: Brain -> Memories -> Knowledge ->
  Relationships -> Graph.
- Do not expose graph mechanics as the default user journey.
- Preserve legacy read compatibility.
- Prefer reprojection over destructive mutation.
- Keep evidence with graph facts.
- Keep rollback paths available.
- Treat models as replaceable Brain workers.
