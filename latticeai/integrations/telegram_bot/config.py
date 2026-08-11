"""Environment, endpoints, logger, and the bot's server capability.

Every value the bridge reads from the environment is resolved here, once, at
import — the module *is* the bot's configuration surface. Importing it has the
same side effects the single file had at its own import: the sensitive-log
filter is installed, ``.env`` is loaded, and the data directory is provisioned.

Security boundary (unchanged since 11.0.1): :func:`_server_client` is the only
authenticated client. It carries the explicitly provisioned bot-to-server
bearer capability and is used for Lattice-server calls alone — never for
api.telegram.org, which always gets the plain ``httpx.AsyncClient`` the poll
loop owns.

Stubbing note: a constant is read through the globals of the module that *uses*
it, so a test standing in for ``TOKEN``/``CHAT_IDS_FILE``/``AGENT_WORKSPACE``
patches ``helpers``/``screens``/``flows``/``dispatch``, not this module.
"""

import logging
import os
from pathlib import Path

import httpx

from latticeai.cli.runtime import _load_env_file
from latticeai.core.logging_safety import install_sensitive_log_filter

install_sensitive_log_filter()

def load_env_file(path=".env"):
    # Single source of truth: shared with ltcai_cli via latticeai.cli.runtime.
    _load_env_file(Path(path))

load_env_file()

def env_value(primary: str, default: str = "") -> str:
    return os.getenv(primary) or default

TOKEN          = env_value("LATTICEAI_TELEGRAM_BOT_TOKEN")
API_URL        = f"https://api.telegram.org/bot{TOKEN}"
SERVER_PORT    = int(env_value("LATTICEAI_SERVER_PORT", "4825"))
BASE_URL       = env_value("LATTICEAI_BASE_URL", env_value("LATTICEAI_SERVER_URL", f"http://127.0.0.1:{SERVER_PORT}")).rstrip("/")
CHAT_URL       = f"{BASE_URL}/chat"
AGENT_URL      = f"{BASE_URL}/agent"
MCP_TOOLS_URL  = f"{BASE_URL}/mcp/tools"
HISTORY_URL    = f"{BASE_URL}/history"
STATUS_URL     = f"{BASE_URL}/status"
MODELS_URL     = f"{BASE_URL}/models"
GRAPH_STATS_URL = f"{BASE_URL}/knowledge-graph/stats"
UPLOAD_DOC_URL  = f"{BASE_URL}/upload/document"
PROPOSALS_URL   = f"{BASE_URL}/api/proposals"

AGENT_RESUME_URL      = f"{BASE_URL}/agent/resume"
AGENT_WORKSPACE       = Path(env_value("LATTICEAI_AGENT_ROOT", "agent_workspace")).resolve()

MAX_TELEGRAM_FILE_BYTES = 45 * 1024 * 1024
INVITE_CODE           = env_value("LATTICEAI_INVITE_CODE")
PUBLIC_WEB_URL        = env_value("LATTICEAI_PUBLIC_URL")
DATA_DIR              = Path(env_value("LATTICEAI_DATA_DIR", str(Path.home() / ".ltcai")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CHAT_IDS_FILE = Path(env_value("LATTICEAI_TELEGRAM_CHATS_FILE", str(DATA_DIR / "telegram_chats.json")))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Server session auth ───────────────────────────────────────────────────────

def _get_server_session() -> str:
    """Return only the explicitly provisioned bot-to-server token.

    Web sessions are hashed at rest and belong to interactive users. Scanning
    ``sessions.json`` cannot recover a usable token and would couple the bot to
    whichever administrator happened to be logged in most recently.
    """
    return env_value("LATTICEAI_SERVER_SESSION_TOKEN").strip()

def _server_client(**kwargs) -> httpx.AsyncClient:
    """Return a server client authenticated by an explicit bearer capability."""
    token = _get_server_session()
    if not token:
        raise RuntimeError(
            "LATTICEAI_SERVER_SESSION_TOKEN is required for Telegram server API calls."
        )
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(headers=headers, **kwargs)
