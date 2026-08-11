"""Telegram bridge — the phone-side surface of a local Lattice AI.

The bot mirrors the web app's capabilities onto a chat: server status, models,
Knowledge Graph counts, screenshots, history, document capture, the Review
Center, and the full human-in-the-loop agent handshake. It is fail-closed by
construction — no bot token, no chat allowlist, or no explicitly provisioned
server capability and the loop refuses to start.

Split into cohesive submodules in v11.3.0 (no behaviour change): ``config``
(environment, endpoints, logger and the authenticated server client),
``helpers`` (chat-id registry, Telegram API, network, agent artifacts),
``screens`` (the menu commands that report on the running system), ``flows``
(ask → render → approve, including the pending-plan state), and ``dispatch``
(command/callback routing and the poll loop). ``__main__`` keeps
``python -m latticeai.integrations.telegram_bot`` working. This module
re-exports every name the single file exposed, so
``latticeai.integrations.telegram_bot.X`` keeps working.

Stubbing note: rebinding one of these *here* changes only this module's name.
The submodule that calls it holds its own reference, so a test standing in for
``CHAT_IDS_FILE``/``TOKEN``/``AGENT_WORKSPACE`` patches
``…telegram_bot.helpers``, for ``_server_client``/``_bot_pending_plans``
``…telegram_bot.flows`` (or ``…screens`` for a screen), and for ``get_updates``
``…telegram_bot.dispatch``.
"""

# The single file had no ``__all__``, so its public surface was "every module
# global" — including the names it imported for its own use. Every re-export
# below therefore uses the redundant-alias form: it reproduces exactly that
# surface, and it marks each name as deliberate rather than a leftover import.
from latticeai.cli.runtime import _load_env_file as _load_env_file
from latticeai.core.io_utils import atomic_write_json as atomic_write_json
from latticeai.core.logging_safety import (
    install_sensitive_log_filter as install_sensitive_log_filter,
)
from latticeai.core.logging_safety import safe_log_text as safe_log_text
from latticeai.core.quiet import quiet as quiet

from .config import AGENT_RESUME_URL as AGENT_RESUME_URL
from .config import AGENT_URL as AGENT_URL
from .config import AGENT_WORKSPACE as AGENT_WORKSPACE
from .config import API_URL as API_URL
from .config import BASE_URL as BASE_URL
from .config import CHAT_IDS_FILE as CHAT_IDS_FILE
from .config import CHAT_URL as CHAT_URL
from .config import DATA_DIR as DATA_DIR
from .config import GRAPH_STATS_URL as GRAPH_STATS_URL
from .config import HISTORY_URL as HISTORY_URL
from .config import INVITE_CODE as INVITE_CODE
from .config import MAX_TELEGRAM_FILE_BYTES as MAX_TELEGRAM_FILE_BYTES
from .config import MCP_TOOLS_URL as MCP_TOOLS_URL
from .config import MODELS_URL as MODELS_URL
from .config import PROPOSALS_URL as PROPOSALS_URL
from .config import PUBLIC_WEB_URL as PUBLIC_WEB_URL
from .config import SERVER_PORT as SERVER_PORT
from .config import STATUS_URL as STATUS_URL
from .config import TOKEN as TOKEN
from .config import UPLOAD_DOC_URL as UPLOAD_DOC_URL
from .config import _get_server_session as _get_server_session
from .config import _server_client as _server_client
from .config import env_value as env_value
from .config import load_env_file as load_env_file
from .config import logger as logger
from .dispatch import HELP_TEXT as HELP_TEXT
from .dispatch import _log_task_exception as _log_task_exception
from .dispatch import handle_callback_query as handle_callback_query
from .dispatch import handle_command as handle_command
from .dispatch import run_bot as run_bot
from .flows import _approval_pause_id as _approval_pause_id
from .flows import _bot_pending_plans as _bot_pending_plans
from .flows import _is_approval_pause as _is_approval_pause
from .flows import _resume_payload as _resume_payload
from .flows import ask_ai as ask_ai
from .flows import format_artifact_card as format_artifact_card
from .flows import format_grounding_badge as format_grounding_badge
from .flows import format_proposals as format_proposals
from .flows import format_run_explanation as format_run_explanation
from .flows import handle_plan_callback as handle_plan_callback
from .flows import handle_proposal_callback as handle_proposal_callback
from .flows import process_ai_request as process_ai_request
from .flows import send_artifact_card as send_artifact_card
from .flows import send_grounding_badge as send_grounding_badge
from .flows import send_plan_for_approval as send_plan_for_approval
from .flows import send_run_explanation as send_run_explanation
from .flows import show_review_center as show_review_center
from .helpers import allowed_chat_ids as allowed_chat_ids
from .helpers import answer_callback as answer_callback
from .helpers import broadcast_web_chat as broadcast_web_chat
from .helpers import collect_generated_files as collect_generated_files
from .helpers import collect_preview_urls as collect_preview_urls
from .helpers import download_as_base64 as download_as_base64
from .helpers import download_telegram_file as download_telegram_file
from .helpers import edit_message as edit_message
from .helpers import get_graph_url as get_graph_url
from .helpers import get_lan_ip as get_lan_ip
from .helpers import get_updates as get_updates
from .helpers import get_web_url as get_web_url
from .helpers import is_chat_allowed as is_chat_allowed
from .helpers import load_chat_ids as load_chat_ids
from .helpers import parse_allowed_chat_ids as parse_allowed_chat_ids
from .helpers import register_chat_id as register_chat_id
from .helpers import resolve_workspace_file as resolve_workspace_file
from .helpers import save_chat_ids as save_chat_ids
from .helpers import send_chat_action as send_chat_action
from .helpers import send_document as send_document
from .helpers import send_generated_files as send_generated_files
from .helpers import send_message as send_message
from .helpers import send_photo as send_photo
from .helpers import send_preview_links as send_preview_links
from .screens import MAIN_MENU as MAIN_MENU
from .screens import _mac_ram_used_gb as _mac_ram_used_gb
from .screens import _unload_all_report as _unload_all_report
from .screens import clear_server_history as clear_server_history
from .screens import do_unload_model as do_unload_model
from .screens import process_document_file as process_document_file
from .screens import send_mcp_tools as send_mcp_tools
from .screens import send_web_link as send_web_link
from .screens import show_graph_stats as show_graph_stats
from .screens import show_history_summary as show_history_summary
from .screens import show_menu as show_menu
from .screens import show_model_info as show_model_info
from .screens import show_status as show_status
from .screens import take_screenshot as take_screenshot
