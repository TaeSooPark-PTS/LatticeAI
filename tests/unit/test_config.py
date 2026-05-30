"""Config is a deep module: one interface (from_env) over a dict seam."""

from pathlib import Path

from latticeai.core.config import Config


def test_defaults_local_mode():
    cfg = Config.from_env({})
    assert cfg.app_mode == "local"
    assert cfg.is_public is False
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 4825
    assert cfg.network_exposed is False
    assert cfg.enable_graph is True
    # loopback default ⇒ auth not forced on
    assert cfg.require_auth is False
    assert cfg.open_registration is True


def test_public_mode_flips_dependent_defaults():
    cfg = Config.from_env({"LATTICEAI_MODE": "public"})
    assert cfg.is_public is True
    assert cfg.require_auth is True          # public ⇒ auth required
    assert cfg.autoload_models is True
    assert cfg.allow_local_models is False
    assert cfg.open_registration is False


def test_non_loopback_host_forces_auth():
    cfg = Config.from_env({"LATTICEAI_HOST": "0.0.0.0"})
    assert cfg.network_exposed is True
    assert cfg.require_auth is True
    assert cfg.open_registration is False


def test_bad_mode_falls_back_to_local():
    assert Config.from_env({"LATTICEAI_MODE": "bogus"}).app_mode == "local"


def test_bad_port_falls_back_to_default():
    assert Config.from_env({"LATTICEAI_PORT": "not-a-number"}).port == 4825
    assert Config.from_env({"LATTICEAI_PORT": "8080"}).port == 8080


def test_cors_and_admin_lists_parsed():
    cfg = Config.from_env({
        "LATTICEAI_CORS_ALLOWED_ORIGINS": "https://a.com, https://b.com ,",
        "LATTICEAI_ADMIN_EMAILS": "Root@Example.com, ops@x.io",
    })
    assert cfg.cors_extra_origins == ["https://a.com", "https://b.com"]
    assert cfg.admin_emails == ["root@example.com", "ops@x.io"]


def test_model_default_chain():
    # PUBLIC_MODEL falls back to DEFAULT_MODEL then literal default
    assert Config.from_env({"LATTICEAI_DEFAULT_MODEL": "groq:llama"}).public_model == "groq:llama"
    assert Config.from_env({"LATTICEAI_PUBLIC_MODEL": "x:y"}).public_model == "x:y"


def test_rate_limit_toggle():
    assert Config.from_env({}).rate_limit_enabled is True
    assert Config.from_env({"LATTICEAI_RATE_LIMIT": "0"}).rate_limit_enabled is False


def test_data_dir_override(tmp_path: Path):
    cfg = Config.from_env({"LATTICEAI_DATA_DIR": str(tmp_path / "store")})
    assert cfg.data_dir == tmp_path / "store"
