"""T3(a): FTS5 trigram keyword index over graph nodes.

Korean substring recall must not regress versus the LIKE scans this index
replaces ('프로젝트' must match '프로젝트를'), the index must track every
node write path (triggers), and the capability must be reported honestly
with the LIKE path surviving as fallback.
"""

import sqlite3

import pytest

from knowledge_graph import KnowledgeGraphStore


def _store(tmp_path):
    return KnowledgeGraphStore(tmp_path / "graph.sqlite", tmp_path / "blobs")


def _fts_available() -> bool:
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
        return True
    except sqlite3.OperationalError:
        return False


requires_fts = pytest.mark.skipif(not _fts_available(), reason="SQLite build lacks FTS5 trigram")


@requires_fts
def test_fts_enabled_and_reported(tmp_path):
    store = _store(tmp_path)
    assert store._fts_enabled is True
    assert store.index_status()["storage"]["fts_enabled"] is True


@requires_fts
def test_korean_substring_recall(tmp_path):
    store = _store(tmp_path)
    store.ingest_message("user", "프로젝트를 진행함: 새 일정 공유", user_email="a@b.c")
    matches = store.search("프로젝트")["matches"]
    assert matches, "agglutinated Korean token must be found by substring"
    assert any("프로젝트를" in (m["title"] or "") + (m["summary"] or "") for m in matches)


@requires_fts
def test_english_search_and_rank_order(tmp_path):
    store = _store(tmp_path)
    store.ingest_message("user", "kubernetes cluster upgrade plan", user_email="a@b.c")
    store.ingest_message("user", "grocery list for the weekend", user_email="a@b.c")
    matches = store.search("kubernetes")["matches"]
    assert matches
    joined = " ".join((m["title"] or "") + (m["summary"] or "") for m in matches)
    assert "kubernetes" in joined.lower()
    assert "grocery" not in joined.lower()


@requires_fts
def test_fts_triggers_track_node_writes(tmp_path):
    # The triggers guard the INDEX itself; store.search reads the v2
    # projection, so direct legacy-table SQL is asserted at node_fts level.
    store = _store(tmp_path)
    store.ingest_message("user", "ephemeralxyz marker content", user_email="a@b.c")

    def fts_hits(q):
        with store._connect() as conn:
            return conn.execute(
                "SELECT node_id FROM node_fts WHERE node_fts MATCH ?", (f'"{q}"',)
            ).fetchall()

    assert fts_hits("ephemeralxyz")
    with store._connect() as conn:
        conn.execute("UPDATE nodes SET title = 'renamedqqq marker' WHERE title LIKE '%ephemeralxyz%'")
    assert fts_hits("renamedqqq")
    with store._connect() as conn:
        conn.execute("DELETE FROM nodes WHERE title LIKE '%renamedqqq%'")
    assert not fts_hits("renamedqqq")


def test_like_fallback_when_fts_disabled(tmp_path):
    store = _store(tmp_path)
    store.ingest_message("user", "fallback-probe content xyzzy", user_email="a@b.c")
    store._fts_enabled = False  # simulate a build without FTS5/trigram
    matches = store.search("xyzzy")["matches"]
    assert matches, "LIKE path must remain a working fallback"


@requires_fts
def test_short_query_uses_like_path(tmp_path):
    # trigram needs >=3 chars; 2-char queries must still return results.
    store = _store(tmp_path)
    store.ingest_message("user", "ab testing of banners", user_email="a@b.c")
    assert store.search("ab")["matches"]


@requires_fts
def test_fts_backfills_existing_db(tmp_path):
    store = _store(tmp_path)
    store.ingest_message("user", "preexisting backfill probe", user_email="a@b.c")
    with store._connect() as conn:
        conn.executescript(
            "DROP TRIGGER node_fts_ai; DROP TRIGGER node_fts_au; "
            "DROP TRIGGER node_fts_ad; DROP TABLE node_fts;"
        )
    reopened = KnowledgeGraphStore(tmp_path / "graph.sqlite", tmp_path / "blobs")
    assert reopened._fts_enabled is True
    assert reopened.search("backfill")["matches"]
