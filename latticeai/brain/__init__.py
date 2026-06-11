"""latticeai.brain — the durable substrate of the Digital Brain.

v4 home for the brain's storage modules. The knowledge-graph store itself
still lives in the root ``knowledge_graph`` module pending its decomposition
(T3d); new brain components land here first.
"""

from latticeai.brain.conversations import ConversationStore

__all__ = ["ConversationStore"]
