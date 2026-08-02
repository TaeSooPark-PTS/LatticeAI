"""`KnowledgeGraphCore` is the written-down seam between the eleven graph mixins.

It is typing-only, so nothing at runtime would notice if it drifted from
reality. These tests make it load-bearing: every declared member must exist on
the assembled store, the declaration must not change the MRO, and no mixin may
acquire the contract as a real base class by accident.
"""

from __future__ import annotations

import inspect

import pytest

from lattice_brain.graph._kg_contract import KG_CORE_MEMBERS, KnowledgeGraphCore
from lattice_brain.graph.store import KnowledgeGraphStore

MIXIN_MODULES = [
    ("discovery", "KnowledgeGraphDiscoveryMixin"),
    ("discovery_index", "KnowledgeGraphLocalIndexMixin"),
    ("documents", "KnowledgeGraphDocumentsMixin"),
    ("ingest", "KnowledgeGraphIngestMixin"),
    ("projection", "KnowledgeGraphProjectionMixin"),
    ("provenance", "KnowledgeGraphProvenanceMixin"),
    ("retrieval", "KnowledgeGraphRetrievalMixin"),
    ("retrieval_docgen", "KnowledgeGraphDocGenMixin"),
    ("retrieval_reads", "KnowledgeGraphReadsMixin"),
    ("retrieval_vector", "KnowledgeGraphVectorMixin"),
    ("write_master", "KnowledgeGraphWriteMixin"),
]


def _mixin_classes():
    import importlib

    for module_name, class_name in MIXIN_MODULES:
        module = importlib.import_module(f"lattice_brain.graph.{module_name}")
        yield class_name, getattr(module, class_name)


@pytest.mark.parametrize("member", KG_CORE_MEMBERS)
def test_store_provides_every_declared_member(member: str) -> None:
    """A declared member with no implementation is a lie in the contract."""
    assert hasattr(KnowledgeGraphStore, member) or member in _store_instance_attrs(), (
        f"{member} is declared in KnowledgeGraphCore but no mixin implements it"
    )


def _store_instance_attrs() -> set[str]:
    """Names assigned in ``KnowledgeGraphStore.__init__`` rather than declared."""
    source = inspect.getsource(KnowledgeGraphStore.__init__)
    return {
        line.split("=")[0].strip().removeprefix("self.")
        for line in source.splitlines()
        if line.strip().startswith("self.") and "=" in line
    }


@pytest.mark.parametrize("name,cls", list(_mixin_classes()))
def test_contract_is_typing_only(name: str, cls: type) -> None:
    """At runtime the base must still be ``object`` — the MRO cannot change."""
    assert cls.__bases__ == (object,), f"{name} gained a real base class"
    assert KnowledgeGraphCore not in cls.__mro__


def test_store_mro_is_the_eleven_mixins_and_object() -> None:
    mro = KnowledgeGraphStore.__mro__
    assert mro[0] is KnowledgeGraphStore
    assert mro[-1] is object
    assert len(mro) == 2 + len(MIXIN_MODULES)
    assert KnowledgeGraphCore not in mro


def test_declared_members_cover_the_real_cross_mixin_calls() -> None:
    """Spot-check the busiest seam members so the list cannot quietly shrink."""
    for member in ("_connect", "_upsert_node", "_upsert_edge", "_read_tables"):
        assert member in KG_CORE_MEMBERS


def test_core_is_never_instantiated_usefully() -> None:
    """Declarations only: calling one must fail loudly rather than no-op."""
    with pytest.raises(NotImplementedError):
        KnowledgeGraphCore()._read_tables()
