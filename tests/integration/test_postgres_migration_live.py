"""Live SQLite → Postgres/pgvector migration.

The Python graph writer that this suite exercised left in v11.6.0 with
the rest of the product write path. SQLite is the live Brain store;
``lattice_brain.storage.postgres`` is gone. The scheduled workflow still
points here so a Monday cron does not fail on a missing file — the skip
is the honest status, not a silent green.

Gated by ``LTCAI_LIVE_POSTGRES_DOCKER_CONSENT=1`` the way the old suite
was, in case a future native pgvector door wants the same switch.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LTCAI_LIVE_POSTGRES_DOCKER_CONSENT") != "1",
    reason="live Postgres Docker is opt-in (LTCAI_LIVE_POSTGRES_DOCKER_CONSENT=1)",
)


def test_python_postgres_writer_is_not_part_of_the_product():
    """The writer this job used to drive was deleted with One Door."""
    pytest.skip(
        "SQLite is the live Brain store; the Python Postgres writer was "
        "removed in 11.6.0. Restore a native pgvector door before making "
        "this job start a container again."
    )
