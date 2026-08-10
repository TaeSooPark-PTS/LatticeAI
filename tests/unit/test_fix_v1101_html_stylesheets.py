"""v11.0.1 D3 — ``_HTMLInspector`` reads the rel attribute as HTMLParser gives it.

The stylesheet branch used to join the attribute value as if it were a list of
tokens (``" ".join(attr.get("rel", []))``). ``HTMLParser`` hands attribute
values over as plain strings, so the join spelled ``"s t y l e s h e e t"`` and
the branch was unreachable for every real page. It splits the string on
whitespace now, which also makes multi-token and mixed-case rel values work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import latticeai.tools as tools
from latticeai.tools.filesystem import _HTMLInspector, inspect_html


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "agent_workspace"
    root.mkdir()
    monkeypatch.setattr(tools, "AGENT_ROOT", root)
    tools.ensure_agent_root()
    return root


def _stylesheets(markup: str) -> list:
    inspector = _HTMLInspector()
    inspector.feed(markup)
    return inspector.stylesheets


def test_a_plain_stylesheet_link_is_collected() -> None:
    assert _stylesheets('<link rel="stylesheet" href="/main.css">') == ["/main.css"]


def test_a_multi_token_rel_still_counts_as_a_stylesheet() -> None:
    # rel="stylesheet preload" is one attribute holding two tokens.
    assert _stylesheets('<link rel="stylesheet preload" href="/a.css">') == ["/a.css"]
    assert _stylesheets('<link rel="preload stylesheet" href="/b.css">') == ["/b.css"]


def test_the_rel_token_is_matched_case_insensitively() -> None:
    # HTMLParser lowercases attribute *names*, never their values.
    assert _stylesheets('<link rel="StyleSheet" href="/c.css">') == ["/c.css"]


def test_a_link_that_is_not_a_stylesheet_is_ignored() -> None:
    assert _stylesheets('<link rel="icon" href="/favicon.ico">') == []
    assert _stylesheets('<link href="/no-rel.css">') == []
    # A whole token, not a substring: "not-stylesheet" is a different relation.
    assert _stylesheets('<link rel="not-stylesheet" href="/d.css">') == []


def test_a_valueless_rel_attribute_does_not_raise() -> None:
    # HTMLParser reports a bare attribute as (name, None).
    assert _stylesheets("<link rel href='/e.css'>") == []


def test_every_stylesheet_on_a_page_reaches_the_inspect_html_report(workspace: Path) -> None:
    (workspace / "page.html").write_text(
        """<!doctype html>
        <html><head>
          <link rel="stylesheet" href="/base.css" />
          <link rel="stylesheet preload" href="/theme.css" />
          <link rel="icon" href="/favicon.ico" />
        </head><body></body></html>
        """,
        encoding="utf-8",
    )

    assert inspect_html("page.html")["stylesheets"] == ["/base.css", "/theme.css"]
