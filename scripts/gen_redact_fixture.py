#!/usr/bin/env python3
"""Dump the Python chat-turn redaction chain for the Rust parity test (WP-W3a).

``latticeai/runtime/history_writer.py`` step 1 is two pure functions:

* :func:`latticeai.core.security.redact_secret_text` — the secret-shape
  redactor every turn passes through, and
* :func:`latticeai.models.router.branding.normalize_branding` — the
  legacy-alias rewrite applied to **assistant** turns only.

Neither touches a model: ``redact_secret_text`` is eleven compiled regular
expressions and ``normalize_branding`` is four, so the whole of step 1 is
portable arithmetic over text. (The *ingest* step's concept extraction is
LLM-first and therefore is not — see the W3a wiring note.) This script is the
proof that the port agrees: it drives the real Python functions over a corpus
that reaches every rule branch and writes what they answered.

``rust/lattice-chat/tests/redact_parity.rs`` replays the corpus through
``lattice_chat::redact`` and asserts byte-identical output for all three
columns:

``redacted``
    ``redact_secret_text(input)`` — what a **user** turn stores.
``branded``
    ``normalize_branding(input)`` — the branding step on its own.
``assistant``
    ``normalize_branding(redact_secret_text(input))`` — the exact composition
    ``write_chat_turn`` applies when ``role == "assistant"``.

Determinism is the constraint: the corpus is a literal list in source order,
the functions are pure, and the file is written with sorted keys and a
trailing newline, so running the script twice produces the same bytes.

Usage::

    .venv/bin/python scripts/gen_redact_fixture.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from latticeai.core.security import (  # noqa: E402
    SECRET_TEXT_PATTERNS,
    TELEGRAM_TOKEN_BARE_RE,
    TELEGRAM_TOKEN_WITH_BOT_RE,
    redact_secret_text,
)
from latticeai.models.router.branding import (  # noqa: E402
    BRAND_NAME,
    LEGACY_BRAND_PATTERNS,
    normalize_branding,
)

OUTPUT = REPO_ROOT / "rust" / "fixtures" / "redact.json"

#: One entry per branch of the two rule sets, plus the negatives that prove a
#: branch does *not* fire. ``case`` is the branch it exists for; the Rust test
#: reports it when an assertion fails, so a divergence names its own rule.
CORPUS: List[Dict[str, str]] = [
    # ── nothing to do ───────────────────────────────────────────────────────
    {"case": "empty", "text": ""},
    {"case": "plain", "text": "nothing secret here"},
    {"case": "korean_plain", "text": "안녕하세요. 오늘 회의록을 정리해 주세요."},
    # ── keyed pattern (group 1 kept, group 2 dropped) ───────────────────────
    {"case": "keyed_api_key_colon", "text": "api_key: abcdefgh12345678"},
    {"case": "keyed_api_key_space", "text": "api key = abcdefgh12345678"},
    {"case": "keyed_api_key_dash", "text": "api-key:abcdefgh12345678"},
    {"case": "keyed_api_key_upper", "text": "API_KEY: ABCDEFGH12345678"},
    {"case": "keyed_secret", "text": "secret = 'sup3rsecretvalue'"},
    {"case": "keyed_token_quoted", "text": 'token: "abcdefgh12345678"'},
    {"case": "keyed_password", "text": "password=hunter2hunter2"},
    {"case": "keyed_passwd", "text": "passwd : abcdefgh1234"},
    {"case": "keyed_authorization", "text": "Authorization: Bearer abcdefgh12345678"},
    {"case": "keyed_bearer", "text": "bearer=abcdefgh12345678"},
    {"case": "keyed_client_secret", "text": "client_secret: abcdefgh12345678"},
    {"case": "keyed_client_secret_dash", "text": "client-secret = abcdefgh12345678"},
    {"case": "keyed_webhook", "text": "webhook: https://hooks.example.com/T0/B0/xyz"},
    {"case": "keyed_dsn", "text": "dsn=postgres-dsn-value-1234"},
    {"case": "keyed_too_short", "text": "token: short"},
    {"case": "keyed_korean_sentence", "text": "제 api_key: abcdefgh12345678 입니다"},
    {"case": "keyed_two_on_one_line", "text": "token: abcdefgh12345678 secret: zyxwvu9876543210"},
    # ── shaped patterns (whole match dropped) ───────────────────────────────
    {"case": "shape_openai", "text": "value sk-abcdefghijklmnop01 end"},
    {"case": "shape_xai", "text": "value xai-abcdefghijklmnop01 end"},
    {"case": "shape_groq", "text": "value gsk_abcdefghijklmnop01 end"},
    {"case": "shape_github", "text": "value ghp_" + "a" * 30 + " end"},
    {"case": "shape_github_short", "text": "value ghp_" + "a" * 20 + " end"},
    {"case": "shape_slack_bot", "text": "value xoxb-1234567890-abc end"},
    {"case": "shape_slack_user", "text": "value xoxp-1234567890-abc end"},
    {"case": "shape_slack_refresh", "text": "value xoxr-1234567890-abc end"},
    {"case": "shape_slack_app", "text": "value xoxa-1234567890-abc end"},
    {"case": "shape_slack_service", "text": "value xoxs-1234567890-abc end"},
    {"case": "shape_aws", "text": "value AKIAABCDEFGHIJKLMNOP end"},
    {"case": "shape_aws_lowercase_tail", "text": "value AKIAabcdefghijklmnop end"},
    {"case": "shape_postgres", "text": "postgres://u:p@host/db"},
    {"case": "shape_postgresql", "text": "POSTGRESQL://user:pw@10.0.0.1:5432/app"},
    {
        "case": "shape_private_key",
        "text": "-----BEGIN RSA PRIVATE KEY-----\nMIIE\nabc\n-----END RSA PRIVATE KEY-----",
    },
    {
        "case": "shape_private_key_openssh",
        "text": "head\n-----BEGIN OPENSSH PRIVATE KEY-----\nx\n-----END OPENSSH PRIVATE KEY-----\ntail",
    },
    # ── telegram tokens (rewritten, not blanked) ────────────────────────────
    {"case": "telegram_with_bot", "text": "bot123456:abcdefghij"},
    {"case": "telegram_bare", "text": "123456:abcdefghij"},
    {"case": "telegram_bare_in_sentence", "text": "토큰은 1234567:AAEEabcdefghij 입니다"},
    {"case": "telegram_lookbehind_blocked", "text": "x123456:abcdefghij"},
    # The trailing lookahead never fires: `[A-Za-z0-9_-]{8,}` is greedy, so it
    # swallows the tail and the lookahead is satisfied at end of string.
    {"case": "telegram_lookahead_is_greedy", "text": "123456:abcdefghij-more_TAIL"},
    {"case": "telegram_too_short_digits", "text": "12:34"},
    {"case": "telegram_url_colon", "text": "https://host:123456:abcdefghij"},
    # ── things that must survive (the classifier's job, not the redactor's) ─
    {"case": "email_plain", "text": "연락처는 owner@example.com 입니다"},
    {"case": "email_plus", "text": "a.b+tag_1%x@sub.example.co.kr"},
    {"case": "email_and_key", "text": "owner@example.com api_key: abcdefgh12345678"},
    {"case": "phone_korean", "text": "010-1234-5678 로 연락 주세요"},
    {"case": "rrn_korean", "text": "주민등록번호 900101-1234567"},
    {"case": "card_number", "text": "카드번호 4111 1111 1111 1111"},
    # ── branding rewrite (assistant turns) ──────────────────────────────────
    {"case": "brand_space", "text": "I am Connect AI, your assistant."},
    {"case": "brand_space_lower", "text": "connect ai here"},
    {"case": "brand_space_multi", "text": "connect   ai here"},
    {"case": "brand_hyphen", "text": "connect-ai release notes"},
    {"case": "brand_joined", "text": "ConnectAI is the old name"},
    {"case": "brand_korean", "text": "저는 커넥트 AI 입니다"},
    {"case": "brand_korean_tight", "text": "저는 커넥트AI 입니다"},
    {"case": "brand_korean_wide", "text": "저는 커넥트   ai 입니다"},
    {"case": "brand_not_a_word", "text": "disconnectai should not change"},
    {"case": "brand_already_right", "text": "Lattice AI is the product name"},
    {"case": "brand_and_secret", "text": "Connect AI says api_key: abcdefgh12345678"},
    {"case": "brand_and_telegram", "text": "connect-ai token 123456:abcdefghij"},
    # ── shape/ordering interactions ─────────────────────────────────────────
    {"case": "multiline_mixed", "text": "line1 sk-abcdefghijklmnop01\nline2 커넥트 AI\nline3 ok"},
    {"case": "keyed_wins_over_shape", "text": "token: sk-abcdefghijklmnop01"},
    {"case": "trailing_punctuation", "text": "(api_key: abcdefgh12345678), done."},
    {"case": "unicode_emoji", "text": "🔐 secret: abcdefgh12345678 🔐"},
]


def build() -> Dict[str, object]:
    """The fixture body: the rule inventory, then the corpus with answers."""
    rows = []
    for entry in CORPUS:
        text = entry["text"]
        redacted = redact_secret_text(text)
        rows.append(
            {
                "case": entry["case"],
                "text": text,
                # `write_chat_turn` step 1, both roles.
                "redacted": redacted,
                "branded": normalize_branding(text),
                "assistant": normalize_branding(redacted),
            }
        )
    return {
        "source": "latticeai.core.security.redact_secret_text + "
        "latticeai.models.router.branding.normalize_branding",
        "brand_name": BRAND_NAME,
        # The Rust port hard-codes the same pattern list; recording it here
        # makes a Python-side edit show up as a fixture diff rather than as a
        # silent divergence nobody notices until a token leaks.
        "secret_patterns": [pattern.pattern for pattern in SECRET_TEXT_PATTERNS],
        "telegram_patterns": [
            TELEGRAM_TOKEN_WITH_BOT_RE.pattern,
            TELEGRAM_TOKEN_BARE_RE.pattern,
        ],
        "brand_patterns": [pattern.pattern for pattern, _ in LEGACY_BRAND_PATTERNS],
        "cases": rows,
    }


def main() -> int:
    payload = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)} — {len(payload['cases'])} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
