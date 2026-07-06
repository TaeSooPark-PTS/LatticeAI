# Product Direction Review

Date: 2026-06-14

## Verdict

The direction is coherent and meaningfully differentiated: Lattice AI should be
positioned as a local-first Digital Brain, not as a graph database, note app,
dashboard, or model launcher.

The distinction between "Digital Brain" and "Knowledge Graph Product" is
important. A graph product asks users to operate a data structure. A Brain
product lets users converse with, grow, protect, and carry their own knowledge
over time. The graph remains valuable, but it is infrastructure and an advanced
inspection layer.

## Fit With Current Implementation

Already aligned:

- First launch follows Login -> Environment Analysis -> Recommended Models ->
  Install & Load -> Brain.
- The post-setup home is the living Brain plus conversation.
- The graph appears only after progressive depth: Brain -> Memories ->
  Knowledge -> Relationships -> Graph.
- Brain Core, SQLite live local storage, optional PostgreSQL/pgvector
  scale/migration tooling, backup/restore, and encrypted `.latticebrain`
  archives support user ownership.
- Models are treated as swappable runtime workers rather than the durable asset.

Gaps corrected in this review:

- `KNOWLEDGE_GRAPH.md` described the Knowledge Graph as the durable center. It
  now states that the Brain is the product and the graph is durable
  infrastructure inside it.
- `AI_PHILOSOPHY.md` now explicitly captures "models are temporary, knowledge is
  durable" and the Brain-first interaction contract.
- The Brain home header now surfaces ownership guarantees: Local-first,
  Portable, Private.

## Risks

- "Living Brain" can feel abstract if the user cannot quickly see value from
  their own documents and conversations. The first successful recall moment must
  happen early.
- A brain metaphor can become decorative if backup, restore, provenance, and
  source evidence are hidden too deeply. The product should keep ownership and
  evidence visible without becoming an admin console.
- Model setup is still a high-friction moment. The product should make the first
  local model recommendation feel safe, short, and reversible.
- The graph layer must stay inspectable for power users without leaking graph
  mechanics into the default experience.

## Comparisons

- Rewind / Limitless: personal memory, but Lattice should emphasize portable
  user-owned Brain archives rather than capture-only recall.
- Obsidian / Logseq: durable personal knowledge, but Lattice should avoid making
  users manually manage the graph.
- Notion AI / Mem: AI-assisted workspace memory, but Lattice should compete on
  local-first ownership and model replaceability.
- AnythingLLM / LM Studio: local AI tooling, but Lattice should not be framed as
  a model runner.
- Personal graph/RAG tools: relevant infrastructure peers, but not the product
  category Lattice should lead with.

## Priority

1. Make first value unmistakable: ingest something, recall it in conversation,
   show source evidence, and preserve it in the Brain.
2. Keep the Brain as home. Avoid reintroducing dashboards, model catalogs, or
   graph explorers as the first screen.
3. Make Brain ownership tangible: backup, restore, export, inspect, and move
   should be easy to find and phrased as care for the Brain.
4. Improve the early model path until it feels like choosing a voice for the
   Brain rather than configuring inference infrastructure.
5. Keep graph power available as the deepest layer for advanced inspection.
6. Keep the product reason visible in the first experience: models are
   replaceable, but the user's knowledge, decisions, projects, and context are
   the durable asset.

## Product Answer

Yes, this is a product people could want if it reliably turns their own
knowledge into useful recall and keeps ownership credible. The strongest
positioning is:

Lattice AI is a local-first Digital Brain. It keeps your knowledge durable,
portable, private, and useful across changing models.
