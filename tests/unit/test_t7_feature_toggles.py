"""v11.2.0 T7 — the feature switchboard service.

The service has one job with three halves, and each is a promise worth pinning:

* **Precedence.** user → env → default, in that order, and it *says* which one
  answered. An install that never opened the panel must keep following its
  environment exactly as it did, and report ``source: "env"`` rather than
  pretending a person chose it.
* **Honesty about what is installable.** ``hnsw`` needs a compiled extension.
  The catalog shows the option, disabled, with the import's own complaint —
  and the writer refuses to store it. A panel that let you pick a backend the
  search silently could not use would be worse than not offering it.
* **Never breaks on bad data.** A hand-edited file with a typo, a corrupt
  JSON, an env var set to "maybe": each falls through to the next layer instead
  of taking the settings surface down.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.services.feature_toggles import (  # noqa: E402
    CATALOG,
    CATALOG_BY_ID,
    CHOICE,
    STORE_FILENAME,
    STORE_VERSION,
    TOGGLE,
    FeatureChoice,
    FeatureToggleService,
    InvalidFeatureValue,
    UnknownFeature,
    _hnsw_probe,
)

#: Every environment variable the catalog seeds from — cleared per test so the
#: developer's own shell can never decide what these assertions see.
ENV_VARS = tuple(item.env_var for item in CATALOG)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _service(tmp_path: Path, **kwargs) -> FeatureToggleService:
    kwargs.setdefault("probes", {"hnsw": lambda: (False, "hnswlib is not available")})
    return FeatureToggleService(data_dir=tmp_path, **kwargs)


def _feature(catalog, feature_id):
    return next(item for item in catalog["features"] if item["id"] == feature_id)


# ── the catalog is the server's, not the client's ────────────────────────────
def test_the_catalog_carries_everything_a_panel_needs_to_render(tmp_path):
    catalog = _service(tmp_path).catalog("ko")

    assert [item["id"] for item in catalog["features"]] == [
        item.id for item in CATALOG
    ]
    for item in catalog["features"]:
        assert item["label"] and not item["label"].startswith("features.")
        assert item["summary"] and not item["summary"].startswith("features.")
        assert item["kind"] in {TOGGLE, CHOICE}
        assert item["source"] in {"default", "env", "user"}
        # Every switch in this release is answered per call by a gate, so none
        # of them may claim a restart is needed.
        assert item["live"] is True
        assert item["restart_required"] is False
    assert catalog["note"]


def test_labels_follow_the_request_language(tmp_path):
    service = _service(tmp_path)
    ko = _feature(service.catalog("ko"), "brain_network")
    en = _feature(service.catalog("en"), "brain_network")

    assert ko["label"] != en["label"]
    assert ko["caution"] and en["caution"]
    # Only the sharing switch carries a caution line; the rest would be noise.
    assert [
        item["id"] for item in service.catalog("ko")["features"] if item["caution"]
    ] == ["brain_network"]


def test_the_video_switch_declares_its_parent_so_a_panel_can_indent_it(tmp_path):
    assert _feature(_service(tmp_path).catalog("ko"), "video_ingest")["parent"] == (
        "allow_multimodal"
    )
    assert _feature(_service(tmp_path).catalog("ko"), "vault_watch")["parent"] is None


# ── precedence: user → env → default ─────────────────────────────────────────
def test_an_untouched_install_reports_its_declared_default(tmp_path):
    service = _service(tmp_path)

    assert service.value("allow_multimodal") is False
    assert _feature(service.catalog("ko"), "allow_multimodal")["source"] == "default"
    # Two of them ship on: the Brain has always done these by itself.
    assert service.value("synthesis") is True
    assert service.value("auto_vector_index") is True
    assert service.value("vector_backend") == "brute"


def test_the_environment_seeds_a_feature_and_is_named_as_the_source(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LATTICEAI_ALLOW_MULTIMODAL", " ON ")
    monkeypatch.setenv("LATTICEAI_AUTO_VECTOR_INDEX", "off")
    monkeypatch.setenv("LATTICEAI_VECTOR_INDEX", "QUANTIZED")
    service = _service(tmp_path)

    assert service.value("allow_multimodal") is True
    assert service.value("auto_vector_index") is False
    assert service.value("vector_backend") == "quantized"
    catalog = service.catalog("ko")
    assert _feature(catalog, "allow_multimodal")["source"] == "env"
    assert _feature(catalog, "vector_backend")["source"] == "env"


def test_a_stored_choice_beats_the_environment_from_then_on(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICEAI_ALLOW_MULTIMODAL", "1")
    service = _service(tmp_path)

    rendered = service.set("allow_multimodal", False, language="ko")

    assert rendered["current"] is False
    assert rendered["source"] == "user"
    assert service.enabled("allow_multimodal") is False
    # The file is the record, and it is readable.
    stored = json.loads((tmp_path / STORE_FILENAME).read_text(encoding="utf-8"))
    assert stored == {"version": STORE_VERSION, "features": {"allow_multimodal": False}}


def test_nonsense_in_the_environment_is_not_a_decision(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICEAI_ALLOW_MULTIMODAL", "maybe")
    monkeypatch.setenv("LATTICEAI_VECTOR_INDEX", "sqlite-vec")
    service = _service(tmp_path)

    assert service.value("allow_multimodal") is False
    assert _feature(service.catalog("ko"), "allow_multimodal")["source"] == "default"
    assert service.value("vector_backend") == "brute"


def test_a_hand_edited_file_with_a_typo_falls_through_instead_of_breaking(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LATTICEAI_ALLOW_MULTIMODAL", "1")
    (tmp_path / STORE_FILENAME).write_text(
        json.dumps({"features": {"allow_multimodal": "sometimes"}}), encoding="utf-8"
    )

    service = _service(tmp_path)

    assert service.value("allow_multimodal") is True
    assert _feature(service.catalog("ko"), "allow_multimodal")["source"] == "env"


@pytest.mark.parametrize(
    "payload", ["{not json", json.dumps(["a", "list"]), json.dumps({"features": 3})]
)
def test_an_unreadable_store_reads_as_no_choices_yet(tmp_path, payload):
    (tmp_path / STORE_FILENAME).write_text(payload, encoding="utf-8")

    assert _service(tmp_path).value("synthesis") is True


@pytest.mark.parametrize(
    ("raw", "expected"), [("yes", True), ("NO", False), (True, True), (False, False)]
)
def test_every_word_this_product_accepts_for_on_and_off_is_storable(
    tmp_path, raw, expected
):
    assert _service(tmp_path).set("vault_watch", raw)["current"] is expected


# ── refusals ─────────────────────────────────────────────────────────────────
def test_an_unknown_feature_is_refused_at_every_door(tmp_path):
    service = _service(tmp_path)

    for call in (
        lambda: service.value("teleportation"),
        lambda: service.user_value("teleportation"),
        lambda: service.set("teleportation", True),
        lambda: service.resolver("teleportation"),
        lambda: service.choice_resolver("teleportation"),
    ):
        with pytest.raises(UnknownFeature):
            call()


def test_a_value_the_feature_cannot_take_is_refused(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(InvalidFeatureValue) as toggle_error:
        service.set("allow_multimodal", "quantized", language="ko")
    assert "quantized" in str(toggle_error.value)

    with pytest.raises(InvalidFeatureValue):
        service.set("vector_backend", True, language="ko")
    with pytest.raises(InvalidFeatureValue):
        service.set("vector_backend", "sqlite-vec", language="ko")
    # Nothing was written by a refused write.
    assert not (tmp_path / STORE_FILENAME).exists()


def test_a_backend_that_is_not_installed_is_shown_disabled_and_refused(tmp_path):
    service = _service(tmp_path)
    hnsw = next(
        choice
        for choice in _feature(service.catalog("ko"), "vector_backend")["choices"]
        if choice["id"] == "hnsw"
    )

    assert hnsw["available"] is False
    assert "설치 필요" in hnsw["detail"]
    assert "hnswlib is not available" in hnsw["detail"]
    with pytest.raises(InvalidFeatureValue) as error:
        service.set("vector_backend", "hnsw", language="en")
    assert "Install required" in str(error.value)


def test_an_installed_backend_is_selectable_and_carries_no_reason(tmp_path):
    service = _service(tmp_path, probes={"hnsw": lambda: (True, "")})

    choices = _feature(service.catalog("en"), "vector_backend")["choices"]
    assert all(choice["available"] for choice in choices)
    assert all(choice["detail"] is None for choice in choices)
    assert service.set("vector_backend", "hnsw")["current"] == "hnsw"


def test_a_probe_that_explodes_counts_as_not_installed(tmp_path):
    service = _service(
        tmp_path, probes={"hnsw": lambda: (_ for _ in ()).throw(OSError("no wheel"))}
    )

    hnsw = _feature(service.catalog("en"), "vector_backend")["choices"][2]
    assert hnsw["available"] is False
    assert "no wheel" in hnsw["detail"]


def test_an_option_nothing_can_check_is_offered_rather_than_hidden(tmp_path):
    """"We could not check" is not "not installed" — the option stays live."""
    service = _service(tmp_path, probes={})

    assert _feature(service.catalog("en"), "vector_backend")["choices"][2]["available"]
    assert service.set("vector_backend", "hnsw")["current"] == "hnsw"


def test_a_choice_without_a_probe_needs_no_check_at_all():
    assert FeatureChoice("brute", "features.vector_backend.choice.brute").probe is None


# ── the real availability probe, both ways ───────────────────────────────────
def test_the_hnsw_probe_reports_the_import_it_actually_attempted(monkeypatch):
    monkeypatch.setitem(sys.modules, "hnswlib", types.ModuleType("hnswlib"))
    assert _hnsw_probe() == (True, "")

    monkeypatch.setitem(sys.modules, "hnswlib", None)
    available, reason = _hnsw_probe()
    assert available is False
    assert "hnswlib" in reason


# ── resolvers, which is how a stored choice becomes behaviour ────────────────
def test_a_resolver_reads_the_service_every_time_it_is_asked(tmp_path):
    service = _service(tmp_path)
    resolve = service.resolver("vault_watch")
    pick = service.choice_resolver("vector_backend")

    assert resolve() is False
    # Nobody has chosen a backend yet, so the resolver has nothing to say and
    # the seam behind it keeps answering from the environment.
    assert pick() is None
    service.set("vault_watch", True)
    service.set("vector_backend", "quantized")
    assert resolve() is True
    assert pick() == "quantized"


def test_the_panel_speaks_only_for_the_switches_someone_actually_moved(tmp_path):
    """The precedence that keeps an untouched install byte-identical."""
    service = _service(tmp_path)
    fallback_calls = []

    def _operator_says_yes() -> bool:
        fallback_calls.append(1)
        return True

    resolve = service.resolver("vault_watch", _operator_says_yes)

    assert service.user_value("vault_watch") is None
    assert resolve() is True  # the gate's own answer, not the service's default
    assert len(fallback_calls) == 1

    service.set("vault_watch", False)

    assert service.user_value("vault_watch") is False
    assert resolve() is False  # a real choice wins, and the fallback is skipped
    assert len(fallback_calls) == 1


def test_a_hand_edited_typo_is_not_mistaken_for_a_choice(tmp_path):
    (tmp_path / STORE_FILENAME).write_text(
        json.dumps({"features": {"vault_watch": "perhaps"}}), encoding="utf-8"
    )

    assert _service(tmp_path).user_value("vault_watch") is None


# ── plumbing ─────────────────────────────────────────────────────────────────
def test_the_store_can_be_pointed_at_the_apps_real_data_dir_later(tmp_path):
    service = _service(tmp_path / "fallback")
    service.set("vault_watch", True)

    service.rebind_data_dir(tmp_path / "real")

    assert service.path == tmp_path / "real" / STORE_FILENAME
    assert service.value("vault_watch") is False


def test_a_change_is_audited_with_what_it_replaced(tmp_path):
    events = []
    service = _service(tmp_path)
    # No sink yet: a change before wiring must still be applied, not dropped.
    service.set("vault_watch", True)
    service.rebind_audit(lambda name, **payload: events.append((name, payload)))

    service.set("vault_watch", False, user_email="me@local")

    assert events == [
        (
            "feature_toggle_changed",
            {
                "feature": "vault_watch",
                "previous": True,
                "value": False,
                "user_email": "me@local",
            },
        )
    ]


def test_a_write_answers_with_the_same_row_the_catalog_would_render(tmp_path):
    service = _service(tmp_path)

    written = service.set("graph_expansion", True, language="ko")

    assert written == _feature(service.catalog("ko"), "graph_expansion")


def test_every_catalog_entry_is_reachable_by_id():
    assert set(CATALOG_BY_ID) == {item.id for item in CATALOG}
    assert len(CATALOG_BY_ID) == len(CATALOG)
