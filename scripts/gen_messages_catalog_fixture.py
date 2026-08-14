#!/usr/bin/env python3
"""Dump the Python server-message catalog for the Rust parity test (WP-I3).

``lattice-core::messages`` is a port of :mod:`latticeai.core.messages`. A port
is only worth having if something keeps proving the two still agree, so this
script is the Python half of that proof: it imports the live catalog and writes
every id rendered in both languages (with representative interpolation args
where a template takes them) plus a ``resolve_language`` vector table that
walks every branch of the Python function.

The Rust side loads ``rust/fixtures/messages_catalog.json`` and asserts
byte-identical ``text`` / ``http_error`` bodies and matching language
resolution. Determinism is the constraint: keys are sorted, interpolation
args are a stable subset of :data:`REPRESENTATIVE_ARGS`, and running the
script twice must produce the same bytes.

Usage::

    .venv/bin/python scripts/gen_messages_catalog_fixture.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from latticeai.core.messages import (  # noqa: E402
    DEFAULT_LANGUAGE,
    LANGUAGE_HEADER,
    MESSAGES,
    SUPPORTED_LANGUAGES,
    resolve_language,
    translate,
)

FIXTURE_PATH = REPO_ROOT / "rust" / "fixtures" / "messages_catalog.json"
SCHEMA = "messages-catalog-parity/v1"

#: ``{name}`` in a catalog string — the same substitution ``translate`` does.
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: One value per placeholder the catalog currently uses. Values are strings
#: because ``translate`` runs ``str(value)`` before replacing; dumping the
#: already-stringified form keeps the fixture language-neutral.
REPRESENTATIVE_ARGS: Dict[str, str] = {
    "allowed": "user, assistant",
    "arg": "label",
    "cap": "50",
    "feature": "telepathy",
    "kind": "docx",
    "limit": "26214400",
    "max": "100",
    "mcp_id": "slack",
    "min": "1",
    "model": "gemma-4",
    "name": "notes.md",
    "op": "add_node",
    "provider": "mlx",
    "reason": "no-gpu",
    "role": "system",
    "size": "31457280",
    "source": "notion",
    "status": "rejected",
    "suffix": ".ogg",
    "tool": "write_file",
    "value": "maybe",
    "workspace": "ws-alpha",
}


class _Request:
    """Minimal stand-in: ``resolve_language`` only ever reads ``.headers``."""

    def __init__(self, headers: Optional[Mapping[str, str]]) -> None:
        self.headers = headers


class _Bare:
    """No ``headers`` attribute — the ``getattr(..., None)`` branch."""


def _placeholders(*texts: str) -> List[str]:
    names = set()
    for text in texts:
        names.update(_PLACEHOLDER.findall(text))
    return sorted(names)


def _args_for(key: str) -> Dict[str, str]:
    entry = MESSAGES[key]
    names = _placeholders(*entry.values())
    missing = [name for name in names if name not in REPRESENTATIVE_ARGS]
    if missing:
        raise SystemExit(
            f"{key} uses placeholders {missing} with no representative args; "
            "add them to REPRESENTATIVE_ARGS"
        )
    return {name: REPRESENTATIVE_ARGS[name] for name in names}


def _render_all() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for key in sorted(MESSAGES):
        args = _args_for(key)
        for language in SUPPORTED_LANGUAGES:
            text = translate(key, language, **args)
            rows.append(
                {
                    "args": args,
                    "http_error": {"detail": text},
                    "id": key,
                    "lang": language,
                    "text": text,
                }
            )
    return rows


def _resolve_row(
    case_id: str,
    *,
    bare: bool = False,
    x_lattice_language: Optional[str] = None,
    accept_language: Optional[str] = None,
) -> Dict[str, Any]:
    if bare:
        expected = resolve_language(_Bare())
    else:
        headers: Dict[str, str] = {}
        if x_lattice_language is not None:
            headers[LANGUAGE_HEADER] = x_lattice_language
        if accept_language is not None:
            headers["accept-language"] = accept_language
        expected = resolve_language(_Request(headers))
    return {
        "accept_language": accept_language,
        "bare": bare,
        "expected": expected,
        "id": case_id,
        "x_lattice_language": x_lattice_language,
    }


def _resolve_vectors() -> List[Dict[str, Any]]:
    """Every branch of ``resolve_language`` / ``_normalize``.

    Names are stable ids, not prose: the Rust test reports the id on mismatch.
    Expected values come from the live Python function, never from a restated
    table, so a Python behaviour change fails the next generate-and-compare.
    """
    cases = [
        # getattr(request, "headers", None) is None → default
        _resolve_row("bare_request", bare=True),
        # empty mapping is observationally the same as missing headers
        _resolve_row("empty_headers"),
        # explicit product header
        _resolve_row("explicit_ko", x_lattice_language="ko"),
        _resolve_row("explicit_en", x_lattice_language="en"),
        _resolve_row("explicit_en_uppercase", x_lattice_language="EN"),
        _resolve_row("explicit_en_gb", x_lattice_language="en-GB"),
        _resolve_row("explicit_en_us_underscore", x_lattice_language="en_US"),
        _resolve_row("explicit_ko_kr", x_lattice_language="ko-KR"),
        _resolve_row("explicit_ko_kr_underscore", x_lattice_language="ko_kr"),
        _resolve_row(
            "explicit_wins_over_accept",
            x_lattice_language="en",
            accept_language="ko-KR,ko;q=0.9",
        ),
        _resolve_row(
            "explicit_ko_wins_over_accept_en",
            x_lattice_language="ko",
            accept_language="en",
        ),
        # explicit that _normalize rejects falls through to Accept-Language
        _resolve_row(
            "explicit_unsupported_falls_through",
            x_lattice_language="fr",
            accept_language="en",
        ),
        _resolve_row(
            "explicit_unsupported_no_accept_defaults",
            x_lattice_language="fr",
        ),
        _resolve_row(
            "explicit_empty_falls_through",
            x_lattice_language="",
            accept_language="en",
        ),
        _resolve_row(
            "explicit_whitespace_falls_through",
            x_lattice_language="  ",
            accept_language="en",
        ),
        # explicit does not split on ';', so this is not a language tag
        _resolve_row(
            "explicit_qvalue_not_stripped",
            x_lattice_language="en;q=1",
            accept_language="ko",
        ),
        _resolve_row(
            "explicit_star_falls_through",
            x_lattice_language="*",
            accept_language="en",
        ),
        _resolve_row(
            "explicit_english_word_falls_through",
            x_lattice_language="english",
            accept_language="ko",
        ),
        # Accept-Language: first supported tag in send order, q-values ignored
        _resolve_row(
            "accept_en_gb_then_ko",
            accept_language="en-GB,en;q=0.9,ko;q=0.8",
        ),
        _resolve_row("accept_ko_kr", accept_language="ko-KR,ko;q=0.9"),
        _resolve_row(
            "accept_unsupported_then_en",
            accept_language="fr-FR,fr;q=0.9,en;q=0.8",
        ),
        _resolve_row("accept_zh_cn_defaults", accept_language="zh-CN"),
        _resolve_row("accept_xx_defaults", accept_language="xx"),
        _resolve_row("accept_star_defaults", accept_language="*"),
        _resolve_row("accept_empty_defaults", accept_language=""),
        _resolve_row("accept_whitespace_defaults", accept_language="   "),
        _resolve_row("accept_en_us_underscore", accept_language="en_US"),
        _resolve_row("accept_en_us_mixed_case", accept_language="EN-us"),
        _resolve_row("accept_padded_en", accept_language="  en  "),
        _resolve_row("accept_de_fr_ko", accept_language="de,fr,ko"),
        _resolve_row("accept_de_fr_defaults", accept_language="de,fr"),
        # q-value disagrees with send order — send order wins
        _resolve_row(
            "accept_order_not_qvalue",
            accept_language="ko;q=0.1,en;q=0.9",
        ),
        _resolve_row("accept_en_with_q", accept_language="en;q=0.9"),
        _resolve_row(
            "accept_ja_zh_then_ko",
            accept_language="ja-JP,zh-CN,ko-KR",
        ),
        _resolve_row(
            "accept_space_after_comma",
            accept_language="en-US, en;q=0.9",
        ),
        _resolve_row("accept_en_gb_with_q", accept_language="en-GB;q=0.8"),
        _resolve_row("accept_leading_comma", accept_language=",en"),
        _resolve_row("accept_trailing_comma", accept_language="en,"),
        _resolve_row(
            "accept_fr_then_low_q_en",
            accept_language="fr;q=1,en;q=0.1",
        ),
        _resolve_row("accept_ko_kr_then_en", accept_language="ko-kr;q=0.8,en"),
        _resolve_row("accept_tab_defaults", accept_language="\t"),
        _resolve_row("accept_only_semicolon", accept_language=";q=0.9"),
    ]
    cases.sort(key=lambda row: row["id"])
    return cases


def build() -> Dict[str, Any]:
    catalog = {
        key: {"en": MESSAGES[key]["en"], "ko": MESSAGES[key]["ko"]}
        for key in sorted(MESSAGES)
    }
    return {
        "catalog": catalog,
        "default_language": DEFAULT_LANGUAGE,
        "language_header": LANGUAGE_HEADER,
        "renders": _render_all(),
        "resolve_language": _resolve_vectors(),
        "schema": SCHEMA,
        "supported_languages": list(SUPPORTED_LANGUAGES),
    }


def dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    payload = build()
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = dumps(payload)
    FIXTURE_PATH.write_text(text, encoding="utf-8")
    n_ids = len(payload["catalog"])
    n_renders = len(payload["renders"])
    n_resolve = len(payload["resolve_language"])
    print(
        f"wrote {FIXTURE_PATH.relative_to(REPO_ROOT)} "
        f"({n_ids} ids, {n_renders} renders, {n_resolve} resolve vectors)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
