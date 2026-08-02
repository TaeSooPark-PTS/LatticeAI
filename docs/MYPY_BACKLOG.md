# mypy backlog

> **Status: reference**
> 10.2.0: 13 modules checked. 10.3.0: 193 of 270, with the remaining 77 listed
> here by error count. **10.4.0: 274 of 274 — the backlog is empty.**

`[tool.mypy] files` in `pyproject.toml` now lists **every** module in
`lattice_brain/` and `latticeai/`. There is no "adopted set" any more; adding a
module to the tree adds it to the checked set, and CI fails if it does not
type-check.

This file stays as the record of how the boundary closed, because two of the
1,407 errors 10.3.0 measured turned out to be worth more than the annotations.

## Two root causes were 68% of the backlog

**`_kg_common.__all__` was computed** (`[name for name in globals() if not
name.startswith("__")]`). Correct at runtime, opaque to a checker: mypy could
not resolve a single name behind `from ._kg_common import *`, so twelve graph
modules reported **~750 false `name-defined` errors** and stayed unchecked.
Freezing the list to a literal fixed all of them.
`tests/unit/test_kg_common_exports.py` asserts the literal still equals what
the expression would produce, so it cannot drift.

**The eleven graph mixins had an unwritten contract.** `ingest` calls
`_upsert_node` from `write_master`, which calls `_v2_project_node` from
`projection`, and everything calls `_connect` — 229 `attr-defined` errors,
because from a checker's position each mixin is a bare class calling methods it
does not have. `lattice_brain/graph/_kg_contract.py` writes that contract down:
23 declared members, typing-only (`_Core` is `object` at runtime, so the MRO is
unchanged), verified by `tests/unit/test_kg_contract.py`.

Neither was a typing problem. Both were *readability* problems that a reader
had to solve by grepping eleven files.

## Real defects the remaining work surfaced

| defect | module | why it mattered |
| --- | --- | --- |
| four lines duplicated after a `return` | `core/workspace_os.py` | dead since the merge that introduced it; `remove_member` looked like it did its work twice |
| `main` was never exported | `app_factory.py` | `python -m latticeai.server_app` raised `AttributeError` — the module entrypoint did not run |
| `str` shadowed a `dict` in a loop | `models/router.py` | the custom-cloud-model branch built ids from the wrong variable |
| `Optional` seam called unguarded | `lattice_brain/context.py` | an unconfigured retrieval port raised `TypeError` inside failure isolation instead of naming itself |

(10.3.0 found three more the same way: `self._path` inside an error handler,
`Iterable` used without importing it, and `.get` on a possible `None`.)

## Conventions the sweep settled on

* **JSON-shaped payloads are `Dict[str, Any]`, not `Dict[str, object]`.**
  `object` forced a cast at every nested access without catching anything;
  the value really is arbitrary JSON.
* **Optional dependencies are aliased, then re-exported as `Any`**
  (`AsyncOpenAI`, `mx`, `vlm_load`, `pyautogui`), so "installed" and "absent"
  have the same declared type and call sites keep their historical name.
* **Async generators in Protocols are declared with `def`, not `async def`.**
  `async def … -> AsyncIterator[T]` says "a coroutine resolving to an
  iterator", which no implementation does.
* **A router dependency it cannot run without is bound through
  `AppContext.require("name")`** — an absent dependency names itself at router
  construction instead of surfacing as `'NoneType' object is not callable`
  inside a request handler.
* **A defensive `isinstance` check on a parameter means the annotation is
  wrong.** Widen the parameter (`Any`, `Optional[str]`) rather than deleting
  the guard: the guard is what the callers actually need.
