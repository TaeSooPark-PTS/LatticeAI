"""Compatibility redirects from retired legacy pages into the v4 SPA."""

from __future__ import annotations

from typing import Optional

from fastapi import Request
from fastapi.responses import RedirectResponse


def app_redirect(fragment: str, request: Optional[Request] = None) -> RedirectResponse:
    """Redirect a legacy GET route to the equivalent /app hash route.

    Existing browser bookmarks keep working while the legacy HTML/JS/CSS pages
    are removed from the shipped artifact. Query strings are preserved after the
    hash so SPA route params remain addressable.
    """

    frag = fragment.strip("/")
    query = ""
    if request is not None and request.url.query:
        query = f"?{request.url.query}"
    return RedirectResponse(url=f"/app#/{frag}{query}", status_code=308)


__all__ = ["app_redirect"]
