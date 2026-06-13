# Lattice AI v4.6.0 - Living Brain Experience

Lattice AI v4.6.0 makes the Brain the product. The primary screen is now the
living Brain plus conversation, while memories, knowledge, relationships, and
the graph are progressively disclosed beneath the conversation flow.

## Highlights

- Home now opens directly into a living Brain conversation instead of a graph,
  dashboard, or status surface.
- Added an animated Brain presence that reacts to listening, recalling,
  thinking, planning, and agent/workflow activity.
- Reused the existing chat streaming, model, history, image attachment, and
  memory-preview APIs inside the Brain-first experience.
- Reduced primary navigation to Brain, Add, Automate, Library, and Care;
  `/ask` and `/chat` remain compatible aliases into the Brain conversation.
- Reordered Brain layers as Brain -> Memories -> Knowledge -> Relationships ->
  Graph, making the graph an intentional advanced exploration surface.
- Updated visual language away from the previous graph/dashboard identity while
  preserving 8px-or-smaller UI radii and stable responsive sizing.

## Preserved

- Brain Core and `lattice_brain`
- FastAPI APIs and compatibility routes
- Tauri desktop shell
- StorageEngine and local-first data model
- Backup, restore, archive, and portability flows
- Existing capture, model, agent, workflow, library, system, and graph
  capabilities

## Expected Artifacts

- `dist/ltcai-4.6.0-py3-none-any.whl`
- `dist/ltcai-4.6.0.tar.gz`
- `dist/ltcai-4.6.0.vsix`
- `ltcai-4.6.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.6.0_aarch64.dmg`

## Validation

Validation is tracked in `docs/V4_6_0_LIVING_BRAIN_EXPERIENCE_REPORT.md`.
