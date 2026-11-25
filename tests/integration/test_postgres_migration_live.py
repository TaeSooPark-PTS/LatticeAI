from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LTCAI_LIVE_POSTGRES_DOCKER_CONSENT") != "1",
    reason="live Postgres Docker is opt-in (LTCAI_LIVE_POSTGRES_DOCKER_CONSENT=1)",
)


def test_python_postgres_writer_is_not_part_of_the_product():
    pytest.skip(
        "SQLite is the live Brain store; the Python Postgres writer was "
        "removed in 11.6.0. Restore a native pgvector door before making "
        "this job start a container again."
    )
