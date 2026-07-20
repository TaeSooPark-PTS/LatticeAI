from __future__ import annotations

from pathlib import Path

from latticeai.core.config import Config
from latticeai.core.product_hardening import build_product_hardening_status


def test_product_hardening_status_reports_local_only_and_inert_tokens(tmp_path: Path):
    env = {
        "LATTICEAI_DATA_DIR": str(tmp_path),
        "LATTICEAI_TELEGRAM_BOT_TOKEN": "present",
        "OPENAI_API_KEY": "present",
        "HF_TOKEN": "present",
    }
    config = Config.from_env(env)

    status = build_product_hardening_status(config=config, env=env)

    assert status["startup"]["local_only_default"] is True
    assert status["privacy"]["integrations"]["telegram"]["credential_present"] is True
    assert status["privacy"]["integrations"]["telegram"]["enabled"] is False
    assert status["privacy"]["integrations"]["external_connectors"]["credential_present"] is True
    assert status["privacy"]["integrations"]["external_connectors"]["enabled"] is False
    assert status["permissions"]["destructive_restore_requires_confirmation"] is True
    assert status["failure_policy"]["archive_corruption"] == "fail_closed"


def test_product_hardening_status_reports_opt_in_egress(tmp_path: Path):
    env = {
        "LATTICEAI_DATA_DIR": str(tmp_path),
        "LATTICEAI_ENABLE_TELEGRAM": "true",
        "LATTICEAI_TELEGRAM_BOT_TOKEN": "present",
    }
    config = Config.from_env(env)

    status = build_product_hardening_status(config=config, env=env)

    assert status["startup"]["local_only_default"] is False
    assert status["privacy"]["integrations"]["telegram"]["enabled"] is True
    assert status["privacy"]["integrations"]["telegram"]["automatic_egress"] is True
