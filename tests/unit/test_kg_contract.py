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
    """At runtime the contract must still be absent from every mixin's MRO.

    Until v11.3.0 this asserted ``cls.__bases__ == (object,)``. The largest
    mixins are now packages whose public class is composed from cohesive
    sub-mixins declared inside that same package, so a mixin may legitimately
    have bases. What may never change is what the assertion was protecting:
    ``KnowledgeGraphCore`` stays typing-only, and nothing from outside the
    graph package enters the hierarchy.
    """
    assert KnowledgeGraphCore not in cls.__mro__, f"{name} gained the contract"
    for base in cls.__mro__:
        assert base is object or base.__module__.startswith("lattice_brain.graph."), (
            f"{name} gained a foreign base class: {base!r}"
        )


def test_store_mro_is_the_declared_mixins_and_their_halves() -> None:
    mro = KnowledgeGraphStore.__mro__
    declared = {cls for _, cls in _mixin_classes()}
    assert mro[0] is KnowledgeGraphStore
    assert mro[-1] is object
    assert KnowledgeGraphCore not in mro
    assert len(mro) == len(set(mro)), "a class entered the store's MRO twice"
    assert declared <= set(mro), "a declared mixin dropped out of the store"
    # Everything else in the MRO is a sub-mixin of one of the declared eleven:
    # decomposing a mixin may add its own halves, never a foreign class.
    for cls in set(mro) - declared - {KnowledgeGraphStore, object}:
        assert any(cls in mixin.__mro__ for mixin in declared), (
            f"{cls!r} is in the store's MRO but belongs to no declared mixin"
        )


def test_declared_members_cover_the_real_cross_mixin_calls() -> None:
    """Spot-check the busiest seam members so the list cannot quietly shrink."""
    for member in ("_connect", "_upsert_node", "_upsert_edge", "_read_tables"):
        assert member in KG_CORE_MEMBERS


def test_core_is_never_instantiated_usefully() -> None:
    """Declarations only: calling one must fail loudly rather than no-op."""
    with pytest.raises(NotImplementedError):
        KnowledgeGraphCore()._read_tables()
