"""The OpenAPI contract survives Python's shrinkage — proved here (v11.6.0 §I5).

``frontend/openapi.json`` stays the single committed contract while most of the
surface it describes moves to Rust. That only works if three things hold, and
each is a test below:

1. **Bijection.** ``scripts/openapi_route_families.json`` names a family for
   every operation in the committed spec, exactly once, with no entries for
   routes that do not exist. A missed route would be a route nobody ports.
2. **Byte-exactness.** ``scripts/compose_openapi.py`` reassembles the committed
   fragments into the committed bytes — compared as bytes, not as parsed JSON,
   so a re-ordered key or an ``int`` that became a ``float`` fails here.
3. **Freshness.** Regenerating the fragments from the current spec produces the
   committed files unchanged, so the fixtures the Rust crates are pinned to
   cannot quietly fall behind the contract.

The scripts are imported by path (``scripts`` is not a package) the way the
parity-contract tests import their generators, so all the logic under test
lives in the scripts themselves rather than being restated here.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = _load("gen_openapi_fragments", "gen_openapi_fragments.py")
composer = _load("compose_openapi", "compose_openapi.py")

#: The work-package families of docs/v11.6.0_ONE_DOOR_PLAN.md. Spelled out so a
#: renamed or invented family fails a test rather than reshaping the pipeline.
EXPECTED_FAMILIES = {
    "auth",
    "static_ui",
    "ui_redirects",
    "workspace",
    "admin",
    "chat",
    "memory_brain",
    "knowledge_search",
    "review_proposals",
    "mcp_market",
    "portability_network",
    "models_misc",
    "worker_keep",
}


@pytest.fixture(scope="module")
def spec() -> dict:
    return generator.load_spec()


@pytest.fixture(scope="module")
def mapping() -> dict:
    return generator.load_mapping()


@pytest.fixture(scope="module")
def rendered(spec, mapping) -> dict:
    return generator.render_all(spec, mapping)


# --------------------------------------------------------------------------
# 1. the map is a bijection with the committed contract
# --------------------------------------------------------------------------


def test_every_committed_operation_has_exactly_one_family(spec, mapping):
    keys = [key for key, _, _ in generator.spec_operations(spec)]
    assert len(keys) == len(set(keys)), "the same operation twice in the spec"
    assert set(keys) == set(mapping["operations"]), (
        "route→family map is out of sync with frontend/openapi.json: "
        f"unmapped={sorted(set(keys) - set(mapping['operations']))[:10]} "
        f"orphans={sorted(set(mapping['operations']) - set(keys))[:10]}"
    )
    assert len(keys) == mapping["source"]["operations"] == 457
    assert len(spec["paths"]) == mapping["source"]["paths"] == 415


def test_family_names_are_the_plan_work_packages(mapping):
    assert set(mapping["families"]) == EXPECTED_FAMILIES
    used = {entry["family"] for entry in mapping["operations"].values()}
    assert used == EXPECTED_FAMILIES, "a declared family with no routes is dead weight"
    for name, meta in mapping["families"].items():
        assert meta["operations"] == sum(
            1 for entry in mapping["operations"].values() if entry["family"] == name
        )


def test_family_is_derived_from_the_module_unless_overridden(mapping):
    """Every assignment is explainable: module default, or a named override."""
    for key, entry in mapping["operations"].items():
        override = mapping["overrides"].get(key)
        expected = override["family"] if override else mapping["module_family"][entry["module"]]
        assert entry["family"] == expected, f"{key} is filed under an unexplained family"
    assert set(mapping["overrides"]) <= set(mapping["operations"]), "override for a dead route"
    for key, override in mapping["overrides"].items():
        assert override["reason"].strip(), f"{key} overrides its module with no reason given"
    modules = {entry["module"] for entry in mapping["operations"].values()}
    assert modules == set(mapping["module_family"]), "module_family lists a module with no routes"


#: The eight operations v11.8.0 deleted end-to-end. Each had no caller — no
#: frontend fetch, no extension call, no Rust proxy target — so the handler, the
#: contract entry and the route→family row left together. Named here because a
#: route that quietly comes back is a route nothing asked for.
DELETED_IN_11_8_0 = (
    "GET /api/embeddings/providers",
    "GET /tools/pdf_pages",
    "POST /tools/read_document",
    "GET /api/ingestion/multimodal",
    "GET /api/capture/voice/status",
    "POST /models/switch/{model_id}",
    "DELETE /models/unload-all",
    "POST /engines/pull-model",
)


def test_the_worker_surface_is_the_keep_worker_set(mapping):
    """worker_keep: scout's KEEP_WORKER set and the graph single writer, less
    the eight caller-less routes v11.8.0 deleted."""
    worker = {key for key, entry in mapping["operations"].items() if entry["family"] == "worker_keep"}
    assert len(worker) == 19
    for key in (
        "GET /health",
        "POST /agent/llm",
        "POST /agent/tool",
        "POST /agent/change-proposal",
        "POST /api/index/drain",
        "POST /api/index/rebuild",
        "GET /api/embeddings/status",
        "GET /models",
        "POST /engines/prepare-model/stream",
        "POST /upload/document",
        "POST /api/capture/voice",
        "POST /knowledge-graph/ingest",
    ):
        assert key in worker, f"{key} must stay inside the Python worker box"
    assert "POST /chat" not in worker, "chat orchestration moves to lattice-chat"


def test_the_routes_deleted_in_11_8_0_left_the_whole_contract(spec, mapping):
    """Deleting a handler is only half of it — the contract must shrink too."""
    committed = {key for key, _, _ in generator.spec_operations(spec)}
    for key in DELETED_IN_11_8_0:
        assert key not in committed, f"{key} still has a public contract entry"
        assert key not in mapping["operations"], f"{key} still has a family"
        assert key not in mapping["overrides"], f"{key} still has an override"
        assert key not in mapping["greedy_path_params"], f"{key} still has a converter"
    for path in ("/tools/read_document", "/engines/pull-model", "/models/unload-all"):
        assert path not in spec["paths"], f"{path} survived as an empty path item"
    assert "PullModelRequest" not in spec["components"]["schemas"], (
        "the pull-model body schema outlived its only operation"
    )


# --------------------------------------------------------------------------
# 2. composition reproduces the committed bytes
# --------------------------------------------------------------------------


def test_committed_fragments_compose_into_the_committed_spec():
    composed_text, problems = composer.compose()
    assert problems == []
    assert composed_text == generator.SPEC_PATH.read_text(encoding="utf-8")


def test_compose_cli_is_green_and_reports_what_it_proved(capsys):
    assert composer.main([]) == 0
    out = capsys.readouterr().out
    assert "415 paths / 457 operations" in out
    assert "byte-identical" in out


def test_fragment_files_on_disk_are_current(rendered):
    assert generator.check_fragments(generator.FRAGMENT_DIR, rendered) == []
    assert generator.main(["--check"]) == 0


def test_generation_is_deterministic(spec, mapping, rendered, tmp_path):
    again = generator.render_all(spec, mapping)
    assert again == rendered
    changed = generator.write_fragments(tmp_path, rendered)
    assert sorted(changed) == sorted(rendered)
    assert generator.write_fragments(tmp_path, rendered) == []
    for name, text in rendered.items():
        assert (tmp_path / name).read_text(encoding="utf-8") == text


def test_generator_cli_writes_the_committed_tree(tmp_path, capsys):
    assert generator.main(["--out", str(tmp_path)]) == 0
    assert "415 paths / 457 operations" in capsys.readouterr().out
    for path in generator.FRAGMENT_DIR.glob("*.json"):
        assert (tmp_path / path.name).read_text(encoding="utf-8") == path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 3. the fragments are self-contained and self-describing
# --------------------------------------------------------------------------


def test_each_fragment_carries_its_own_schema_closure(spec):
    """A crate reading one fragment sees every schema its routes reference."""
    for path in sorted(generator.FRAGMENT_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        fragment = json.loads(path.read_text(encoding="utf-8"))
        carried = set(fragment["components"]["schemas"])
        referenced = generator.schema_refs(fragment["paths"])
        assert referenced <= carried, f"{path.name} references schemas it does not carry"
        closure = generator.schema_closure(carried, spec["components"]["schemas"])
        assert closure == carried, f"{path.name} carries a schema whose own $refs are missing"
        for name in carried:
            assert fragment["components"]["schemas"][name] == spec["components"]["schemas"][name]


def test_fragments_record_committed_source_order(spec):
    committed = [key for key, _, _ in generator.spec_operations(spec)]
    for path in sorted(generator.FRAGMENT_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        fragment = json.loads(path.read_text(encoding="utf-8"))
        order = fragment["operation_order"]
        assert order == [key for key in committed if key in set(order)]
        assert fragment["path_order"] == list(fragment["paths"])
        assert fragment["operation_count"] == len(order)
        assert fragment["path_count"] == len(fragment["paths"])
        assert fragment["schema_count"] == len(fragment["components"]["schemas"])


def test_greedy_path_params_reach_the_family_that_must_port_them(mapping):
    """``{name:path}`` matches slashes; the schema flattens it to ``{name}``.

    A port that reads only the schema mounts a plain capture and 404s on every
    id with a ``/`` in it, so the converter list travels with the fragment.
    """
    greedy = mapping["greedy_path_params"]
    assert set(greedy) <= set(mapping["operations"])
    assert len(greedy) == 13
    assert greedy["GET /workspace/relationships/{node_id}"] == "node_id"
    carried: dict[str, str] = {}
    for path in sorted(generator.FRAGMENT_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        fragment = json.loads(path.read_text(encoding="utf-8"))
        for key, param in fragment["greedy_path_params"].items():
            assert key in fragment["operation_order"], f"{path.name}: greedy key it does not serve"
            assert f"{{{param}}}" in key
            carried[key] = param
    assert carried == greedy, "a converter was recorded but never reached a fragment"


def test_index_totals_and_hashes_describe_the_files_on_disk():
    index = generator.load_json(generator.FRAGMENT_DIR / generator.INDEX_NAME)
    assert index["serialization"] == generator.SERIALIZATION
    assert index["totals"] == {"families": 13, "operations": 457, "paths": 415, "schemas": 174}
    for entry in index["fragments"]:
        text = (generator.FRAGMENT_DIR / entry["file"]).read_text(encoding="utf-8")
        assert generator.sha256_text(text) == entry["sha256"]
    envelope = generator.load_json(generator.FRAGMENT_DIR / generator.ENVELOPE_NAME)
    assert envelope["unattributed_schemas"] == [], "a schema no operation references"
    assert envelope["spec"]["info"] == generator.load_spec()["info"]


# --------------------------------------------------------------------------
# 4. the failure modes actually fail
# --------------------------------------------------------------------------


@pytest.fixture()
def fragment_copy(tmp_path) -> Path:
    directory = tmp_path / "openapi"
    shutil.copytree(generator.FRAGMENT_DIR, directory)
    return directory


def _write(path: Path, payload) -> None:
    path.write_text(generator.canonical_json(payload), encoding="utf-8")


def test_a_hand_edited_fragment_is_reported_as_stale(fragment_copy, capsys):
    fragment = generator.load_json(fragment_copy / "ui_redirects.json")
    fragment["paths"].pop("/activity")
    _write(fragment_copy / "ui_redirects.json", fragment)
    assert composer.main(["--fragments", str(fragment_copy)]) == 1
    out = capsys.readouterr().out
    assert "sha256 does not match" in out
    assert "GET /activity" in out
    assert "does not reproduce" in out


def test_an_extra_operation_is_reported_as_extra(fragment_copy, capsys):
    fragment = generator.load_json(fragment_copy / "ui_redirects.json")
    fragment["paths"]["/invented"] = {"get": {"responses": {}}}
    _write(fragment_copy / "ui_redirects.json", fragment)
    assert composer.main(["--fragments", str(fragment_copy)]) == 1
    assert "GET /invented" in capsys.readouterr().out


def test_a_gutted_fragment_names_what_the_composition_lost(fragment_copy, capsys):
    """The report is readable at scale: counts, a capped list, and the schemas."""
    fragment = generator.load_json(fragment_copy / "mcp_market.json")
    fragment["paths"] = {}
    fragment["components"]["schemas"] = {}
    _write(fragment_copy / "mcp_market.json", fragment)
    assert composer.main(["--fragments", str(fragment_copy)]) == 1
    out = capsys.readouterr().out
    assert "87 operation(s) missing from the composition" in out
    assert "47 more" in out, "long lists must be capped, not dumped"
    assert "schema(s) missing from the composition" in out


def test_a_hand_edited_envelope_is_reported_as_stale(fragment_copy, capsys):
    envelope = generator.load_json(fragment_copy / generator.ENVELOPE_NAME)
    envelope["spec"]["info"]["version"] = "0.0.0"
    _write(fragment_copy / generator.ENVELOPE_NAME, envelope)
    assert composer.main(["--fragments", str(fragment_copy)]) == 1
    assert "_envelope.json: sha256 does not match" in capsys.readouterr().out


def test_identical_bytes_produce_no_diff_report():
    text = generator.SPEC_PATH.read_text(encoding="utf-8")
    assert composer.diff_report(text, text) == []


def test_two_families_cannot_claim_the_same_operation(fragment_copy):
    fragment = generator.load_json(fragment_copy / "ui_redirects.json")
    stolen = generator.load_json(fragment_copy / "static_ui.json")
    stolen["paths"]["/activity"] = fragment["paths"]["/activity"]
    _write(fragment_copy / "static_ui.json", stolen)
    with pytest.raises(composer.CompositionError, match="one operation, one family"):
        composer.compose(fragment_copy)


def test_a_shared_schema_must_be_the_same_schema_everywhere(fragment_copy):
    """Shared schemas are duplicated into every fragment; the copies must agree."""
    fragment = generator.load_json(fragment_copy / "auth.json")
    elsewhere = generator.load_json(fragment_copy / "admin.json")["components"]["schemas"]
    name = next(n for n in fragment["components"]["schemas"] if n in elsewhere)
    fragment["components"]["schemas"][name] = {"title": "not the same schema"}
    _write(fragment_copy / "auth.json", fragment)
    with pytest.raises(composer.CompositionError, match="must be byte-identical copies"):
        composer.compose(fragment_copy)


def test_missing_artefacts_are_named(fragment_copy, tmp_path, capsys):
    (fragment_copy / "auth.json").unlink()
    with pytest.raises(composer.CompositionError, match="auth.json is missing"):
        composer.compose(fragment_copy)
    (fragment_copy / generator.ENVELOPE_NAME).unlink()
    with pytest.raises(composer.CompositionError, match="_envelope.json is missing"):
        composer.compose(fragment_copy)
    (fragment_copy / generator.INDEX_NAME).unlink()
    assert composer.main(["--fragments", str(fragment_copy)]) == 1
    assert "_index.json is missing" in capsys.readouterr().out


def test_check_mode_names_missing_stale_and_unexpected_files(rendered, tmp_path, capsys):
    generator.write_fragments(tmp_path, rendered)
    (tmp_path / "auth.json").unlink()
    (tmp_path / "chat.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "leftover.json").write_text("{}\n", encoding="utf-8")
    assert generator.main(["--out", str(tmp_path), "--check"]) == 1
    out = capsys.readouterr().out
    assert "missing: auth.json" in out
    assert "stale: chat.json" in out
    assert "unexpected: leftover.json" in out
    assert generator.write_fragments(tmp_path, rendered) == [
        "auth.json",
        "chat.json",
        "leftover.json",
    ]
    assert not (tmp_path / "leftover.json").exists()


def test_an_unmapped_route_fails_loudly(spec, mapping):
    """The map is hand-maintained; a new Python route must be filed, not guessed."""
    thinned = copy.deepcopy(mapping)
    thinned["operations"].pop("GET /health")
    with pytest.raises(KeyError, match="GET /health"):
        generator.render_all(spec, thinned)


# --------------------------------------------------------------------------
# 5. the P1 cutover precondition: the worker app ⊆ worker_keep
# --------------------------------------------------------------------------


def _worker_spec_from_fragment(subset: int | None = None) -> dict:
    fragment = generator.load_json(generator.FRAGMENT_DIR / "worker_keep.json")
    paths = dict(list(fragment["paths"].items())[:subset] if subset else fragment["paths"])
    return {
        "openapi": "3.1.0",
        "info": generator.load_spec()["info"],
        "paths": paths,
        "components": {"schemas": fragment["components"]["schemas"]},
    }


def test_worker_spec_subset_passes(tmp_path, capsys):
    worker = tmp_path / "worker.json"
    _write(worker, _worker_spec_from_fragment(subset=5))
    assert composer.main(["--worker-spec", str(worker)]) == 0
    assert "worker spec ⊆ worker_keep" in capsys.readouterr().out


def test_internal_worker_seams_are_exempt_from_the_subset_check(tmp_path, capsys):
    """WP-I6's loopback seams are gateway-only surface: no public contract, no failure."""
    worker_spec = _worker_spec_from_fragment()
    for path in composer.INTERNAL_WORKER_PATHS:
        worker_spec["paths"][path] = {"post": {"responses": {}}}
    worker_spec["paths"]["/openapi.json"] = {"get": {"responses": {}}}
    worker = tmp_path / "worker.json"
    _write(worker, worker_spec)
    assert composer.main(["--worker-spec", str(worker)]) == 0
    assert "worker spec ⊆ worker_keep" in capsys.readouterr().out
    spec = generator.load_json(generator.FRAGMENT_DIR / "worker_keep.json")
    assert composer.INTERNAL_WORKER_PATHS.isdisjoint(spec["paths"]), (
        "internal seams must stay out of the committed contract"
    )
    assert composer.INTERNAL_WORKER_PATHS == {
        "/worker/sysinfo",
        "/worker/llm/stream",
        "/worker/embed",
        "/worker/parse",
        "/worker/render/docx",
        "/worker/render/xlsx",
        "/worker/render/pptx",
        "/worker/render/pdf",
        "/worker/asr",
        "/worker/extract",
        "/worker/vector/query",
    }


def test_the_exemption_list_is_exactly_the_worker_profile_seams():
    """One list in the composer, one in the worker profile — kept the same set.

    A seam added to the profile but not here fails the subset check at cutover;
    a path listed only here is an exemption for a route nothing serves. Reading
    the profile rather than restating it means neither can happen quietly.
    """
    from latticeai.runtime.build_phases.worker_profile import worker_route_keys

    served = {path for _method, path in worker_route_keys() if path.startswith("/worker/")}
    assert served == set(composer.INTERNAL_WORKER_PATHS)


def test_an_unlisted_worker_seam_still_fails(tmp_path, capsys):
    """The exemption is a named list, not a ``/worker/`` prefix rule."""
    worker_spec = _worker_spec_from_fragment()
    worker_spec["paths"]["/worker/undeclared-seam"] = {"post": {"responses": {}}}
    worker = tmp_path / "worker.json"
    _write(worker, worker_spec)
    assert composer.main(["--worker-spec", str(worker)]) == 1
    assert "POST /worker/undeclared-seam" in capsys.readouterr().out


def test_a_worker_route_without_a_contract_fails(tmp_path, capsys):
    worker_spec = _worker_spec_from_fragment()
    worker_spec["paths"]["/agent/invented-seam"] = {"post": {"responses": {}}}
    worker = tmp_path / "worker.json"
    _write(worker, worker_spec)
    assert composer.main(["--worker-spec", str(worker)]) == 1
    out = capsys.readouterr().out
    assert "POST /agent/invented-seam" in out
    assert "no committed contract" in out


def test_a_worker_spec_from_a_different_version_fails(tmp_path, capsys):
    # Derived, never a literal: this test named "11.6.0" until 11.6.0 shipped,
    # at which point the "different" version was the current one and the case
    # asserted the opposite of its name. The mismatch is now a fact about the
    # committed envelope rather than a number someone has to remember to bump.
    worker_spec = _worker_spec_from_fragment()
    current = worker_spec["info"]["version"]
    major, _, _ = current.partition(".")
    mismatched = f"{int(major) + 1}.0.0"
    assert mismatched != current
    worker_spec["info"] = {"title": "Lattice AI Server (local)", "version": mismatched}
    worker = tmp_path / "worker.json"
    _write(worker, worker_spec)
    assert composer.main(["--worker-spec", str(worker)]) == 1
    assert "regenerate the fragments after the bump" in capsys.readouterr().out


def test_output_writes_the_composed_contract(tmp_path, capsys):
    target = tmp_path / "nested" / "composed.json"
    assert composer.main(["--output", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == generator.SPEC_PATH.read_text(encoding="utf-8")
    assert f"wrote {target}" in capsys.readouterr().out
