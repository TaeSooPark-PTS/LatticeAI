"""`_kg_common.__all__` is the star-import contract for the whole graph package.

It used to be computed (`[name for name in globals() if not
name.startswith("__")]`). That is correct at runtime and *opaque* to a type
checker: mypy could not resolve a single name behind
`from ._kg_common import *`, so twelve graph modules reported hundreds of
false `name-defined` errors and stayed outside the checked set.

Freezing the list makes it analysable. These tests keep the frozen list equal
to what the computed expression would produce, so a new helper added to
`_kg_common` without an export entry fails here instead of silently vanishing
from the mixins that star-import it.
"""

from __future__ import annotations

from types import ModuleType

import lattice_brain.graph._kg_common as kg_common


def _computed_exports() -> set[str]:
    """Every name `_kg_common` defines, minus its own submodules.

    v11.3.0 made `_kg_common` a package, and importing `.text` / `.relations`
    / `.extraction` binds each one as an attribute of the package. Those are
    file names, not part of the star-import contract — exporting them would
    push three module objects into twelve consumers. Everything else must
    still appear in `__all__`, which is what this list is for.
    """
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
    """`import *` normally skips `_name`; the mixins depend on these."""
    for name in ("_slug", "_sha256_text", "_json", "_chunks", "_now"):
        assert name in kg_common.__all__
        assert hasattr(kg_common, name)


def test_star_import_reaches_the_graph_package() -> None:
    """The keep-set package still re-exports the helpers the goldens pin."""
    import lattice_brain.graph._kg_common as common

    assert common._slug is kg_common._slug
    assert common._sha256_text is kg_common._sha256_text
