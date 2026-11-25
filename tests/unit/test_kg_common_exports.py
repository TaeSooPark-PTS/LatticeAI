from __future__ import annotations

from types import ModuleType

import lattice_brain.graph._kg_common as kg_common


def _computed_exports() -> set[str]:
    submodules = {
        name
        for name, value in vars(kg_common).items()
        if isinstance(value, ModuleType)
        and getattr(value, "__name__", "").startswith(f"{kg_common.__name__}.")
    }
    return {
        name
        for name in vars(kg_common)
        if not name.startswith("__") and name not in submodules
    }


def test_static_all_matches_computed_globals() -> None:
    declared = set(kg_common.__all__)
    computed = _computed_exports()
    assert declared - computed == set(), "exported name no longer defined in _kg_common"
    assert computed - declared == set(), "new _kg_common name missing from __all__"


def test_all_is_a_literal_sorted_list_without_duplicates() -> None:
    assert isinstance(kg_common.__all__, list)
    assert kg_common.__all__ == sorted(kg_common.__all__)
    assert len(kg_common.__all__) == len(set(kg_common.__all__))


def test_underscore_helpers_are_exported_on_purpose() -> None:
    for name in ("_slug", "_sha256_text", "_json", "_chunks", "_now"):
        assert name in kg_common.__all__
        assert hasattr(kg_common, name)


def test_star_import_reaches_the_graph_package() -> None:
    import lattice_brain.graph._kg_common as common

    assert common._slug is kg_common._slug
    assert common._sha256_text is kg_common._sha256_text
