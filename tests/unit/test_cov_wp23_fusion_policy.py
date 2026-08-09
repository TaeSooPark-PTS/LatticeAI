"""wp23 coverage — graph fusion / retrieval policy / rerank / json helpers.

These are the deterministic, dependency-free edges of the graph layer: the
query-class fusion table and its ``LATTICEAI_FUSION_WEIGHTS`` override parser,
the rule-based query rewrite, the optional cross-encoder rerank (exercised via
a fake ``sentence_transformers`` module injected into ``sys.modules``), the
tolerant metadata JSON loader, the lazy ``lattice_brain.graph`` attribute hook,
and the ``kg_schema`` CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph import fusion, json_utils, rerank, retrieval_policy, schema

# ── fusion: env override parsing ─────────────────────────────────────────────


def test_env_override_ignores_a_non_mapping_payload(monkeypatch) -> None:
    monkeypatch.setenv(fusion.FUSION_WEIGHTS_ENV, "[1, 2, 3]")

    assert fusion._env_overrides() == {}
    # The full table therefore stays at the documented defaults.
    assert fusion.fusion_weight_table()["fact"] == fusion.DEFAULT_FUSION_WEIGHTS["fact"]


def test_env_override_skips_unknown_classes_and_non_dict_tables(monkeypatch) -> None:
    monkeypatch.setenv(
        fusion.FUSION_WEIGHTS_ENV,
        json.dumps({"bogus_class": {"alpha": 0.1}, "code": "not-a-table"}),
    )

    assert fusion._env_overrides() == {}


def test_env_override_drops_unknown_keys_and_unparseable_numbers(monkeypatch) -> None:
    monkeypatch.setenv(
        fusion.FUSION_WEIGHTS_ENV,
        json.dumps(
            {
                "code": {"alpha": 0.25, "nonsense": 9, "graph": "not-a-number"},
                "person": {"weird": 1},
            }
        ),
    )

    overrides = fusion._env_overrides()

    # only the recognised, numeric key survives
    assert overrides == {"code": {"alpha": 0.25}}
    # "person" contributed nothing at all, so it is absent rather than empty
    assert "person" not in overrides
    assert fusion.fusion_weight_table()["code"]["alpha"] == 0.25
    assert fusion.fusion_weight_table()["code"]["graph"] == (
        fusion.DEFAULT_FUSION_WEIGHTS["code"]["graph"]
    )


def test_caller_override_with_an_unparseable_value_keeps_the_default() -> None:
    table = fusion.fusion_weight_table({"code": {"alpha": "not-a-number"}})

    assert table["code"]["alpha"] == fusion.DEFAULT_FUSION_WEIGHTS["code"]["alpha"]


def test_caller_override_clamps_into_the_unit_interval() -> None:
    table = fusion.fusion_weight_table({"person": {"graph": 7.5, "keyword": -3}})

    assert table["person"]["graph"] == 1.0
    assert table["person"]["keyword"] == 0.0


def test_fusion_profile_falls_back_to_fact_for_an_unknown_class(monkeypatch) -> None:
    # classify_query only ever emits the four known classes; patch the seam to
    # prove the defensive fallback resolves real fact-class weights.
    monkeypatch.setattr(fusion, "classify_query", lambda query: "surprise")

    profile = fusion.fusion_profile("anything")

    assert profile["query_class"] == "fact"
    assert profile["alpha"] == fusion.DEFAULT_FUSION_WEIGHTS["fact"]["alpha"]
    assert profile["weights"]["graph"] == fusion.DEFAULT_FUSION_WEIGHTS["fact"]["graph"]


# ── retrieval policy: rewrite never raises ───────────────────────────────────


class _ExplodingQuery:
    """Truthiness itself raises — the shape ``rewrite_query`` must survive."""

    def __bool__(self) -> bool:
        raise RuntimeError("this query object refuses to be evaluated")


def test_rewrite_query_returns_the_empty_contract_when_coercion_explodes() -> None:
    assert retrieval_policy.rewrite_query(_ExplodingQuery()) == {
        "original": "",
        "rewritten": "",
        "rules": [],
    }


def test_resolve_policy_over_an_exploding_query_still_returns_a_policy() -> None:
    policy = retrieval_policy.resolve_policy(_ExplodingQuery())

    assert policy["query_class"] == "fact"
    assert policy["search_query"] == ""
    assert policy["recency_half_life_days"] is None


# ── rerank ───────────────────────────────────────────────────────────────────


class _FakeCrossEncoder:
    """Stand-in for ``sentence_transformers.CrossEncoder``."""

    instances: list = []

    def __init__(self, model_id: str, *, scores=None, fail=None):
        self.model_id = model_id
        self._scores = scores
        self._fail = fail
        type(self).instances.append(self)

    def predict(self, pairs):
        if self._fail is not None:
            raise self._fail
        if self._scores is not None:
            return self._scores
        return [float(len(pair[1])) for pair in pairs]


def _install_sentence_transformers(monkeypatch, factory) -> None:
    module = type(sys)("sentence_transformers")
    module.CrossEncoder = factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    # the module-level cache must not leak a fake between tests
    monkeypatch.setattr(rerank, "_model_cache", {})


def _candidates():
    return [
        {"node_id": "n1", "title": "short", "score": 0.2},
        {"node_id": "n2", "title": "a much longer candidate title", "score": 0.9},
    ]


def test_cross_encoder_rerank_reorders_and_caches_the_loaded_model(monkeypatch) -> None:
    _FakeCrossEncoder.instances = []
    _install_sentence_transformers(monkeypatch, _FakeCrossEncoder)

    first = rerank.cross_encoder_rerank("q", _candidates(), model_id="fake/model")

    assert first["mode"] == "cross_encoder"
    assert first["model"] == "fake/model"
    # longest candidate text scores highest under the fake encoder
    assert [m["node_id"] for m in first["matches"]] == ["n2", "n1"]
    assert first["matches"][0]["scores"]["rerank"] == first["matches"][0]["score"]
    assert first["matches"][0]["scores"]["fused"] == 0.9
    assert first["matches"][0]["rank"] == 1

    second = rerank.cross_encoder_rerank("q", _candidates(), model_id="fake/model")

    assert second["mode"] == "cross_encoder"
    # the cache hit means no second CrossEncoder was constructed
    assert len(_FakeCrossEncoder.instances) == 1


def test_cross_encoder_rerank_falls_back_when_predict_raises(monkeypatch) -> None:
    _FakeCrossEncoder.instances = []
    _install_sentence_transformers(
        monkeypatch,
        lambda model_id: _FakeCrossEncoder(model_id, fail=RuntimeError("cuda gone")),
    )

    result = rerank.cross_encoder_rerank("q", _candidates(), top_k=1)

    assert result["mode"] == "identity"
    assert result["detail"].startswith("cross_encoder_predict_failed: ")
    assert "cuda gone" in result["detail"]
    # identity fallback preserves fused order and honours top_k
    assert [m["node_id"] for m in result["matches"]] == ["n1"]


def test_cross_encoder_rerank_short_circuits_on_no_candidates() -> None:
    result = rerank.cross_encoder_rerank("q", [], model_id="pinned/model")

    assert result == {
        "matches": [],
        "mode": "cross_encoder",
        "model": "pinned/model",
        "detail": None,
    }


def test_rerank_matches_forced_on_uses_the_cross_encoder(monkeypatch) -> None:
    _FakeCrossEncoder.instances = []
    _install_sentence_transformers(monkeypatch, _FakeCrossEncoder)
    monkeypatch.setenv(rerank.CROSS_ENCODER_MODEL_ENV, "env/model")

    result = rerank.rerank_matches("q", _candidates(), force=True)

    assert result["mode"] == "cross_encoder"
    assert result["model"] == "env/model"


# ── json_utils ───────────────────────────────────────────────────────────────


def test_safe_loads_returns_an_empty_dict_for_a_corrupt_row(caplog) -> None:
    with caplog.at_level("WARNING"):
        assert json_utils._safe_loads('{"unterminated": ') == {}

    assert any("corrupt metadata_json" in record.message for record in caplog.records)


def test_safe_loads_keeps_valid_objects_and_drops_non_objects() -> None:
    assert json_utils._safe_loads('{"a": 1}') == {"a": 1}
    assert json_utils._safe_loads("[1, 2]") == {}
    assert json_utils._safe_loads(None) == {}


# ── lattice_brain.graph lazy attribute hook ──────────────────────────────────


def test_graph_package_lazily_resolves_its_public_names() -> None:
    import lattice_brain.graph as graph_pkg
    from lattice_brain.graph.schema import EdgeType, KGStoreV2, NodeType
    from lattice_brain.graph.store import KnowledgeGraphStore

    assert graph_pkg.KnowledgeGraphStore is KnowledgeGraphStore
    assert graph_pkg.KGStoreV2 is KGStoreV2
    assert graph_pkg.NodeType is NodeType
    assert graph_pkg.EdgeType is EdgeType

    with pytest.raises(AttributeError):
        graph_pkg.NoSuchGraphSymbol  # noqa: B018


# ── kg_schema CLI ────────────────────────────────────────────────────────────


def test_kg_schema_cli_init_creates_the_v2_tables(tmp_path, monkeypatch, capsys) -> None:
    db = tmp_path / "cli.sqlite"
    monkeypatch.setattr(sys, "argv", ["kg_schema", "init", str(db)])

    assert schema._cli() == 0

    assert db.exists()
    assert f"initialized v2 schema in {db}" in capsys.readouterr().out
    assert schema.KGStoreV2(db).stats()["nodes"] == 0


def test_kg_schema_cli_stats_prints_json(tmp_path, monkeypatch, capsys) -> None:
    db = tmp_path / "cli.sqlite"
    schema.KGStoreV2(db).init_schema()
    monkeypatch.setattr(sys, "argv", ["kg_schema", "stats", str(db)])

    assert schema._cli() == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["nodes"] == 0
    assert payload["schema_version"] == schema.KG_SCHEMA_V2_VERSION


def test_kg_schema_cli_reports_an_unhandled_subcommand(monkeypatch) -> None:
    # argparse itself refuses unknown subcommands, so patch the parse seam to
    # reach the defensive exit status the CLI still returns.
    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda self, *args, **kwargs: argparse.Namespace(cmd="unhandled"),
    )

    assert schema._cli() == 2


def test_kg_schema_module_entrypoint_exits_with_the_cli_status(
    tmp_path, monkeypatch, capsys
) -> None:
    import runpy

    db = tmp_path / "main.sqlite"
    monkeypatch.setattr(sys, "argv", ["kg_schema", "init", str(db)])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("lattice_brain.graph.schema", run_name="__main__")

    assert excinfo.value.code == 0
    assert db.exists()
    capsys.readouterr()
