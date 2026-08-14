"""The document parser routes — the two ``/tools/*`` that are pure compute.

This module used to be the direct tool surface: ~30 ``/tools/*`` routes, the
upload door, the MCP catalogue, computer-use, the permission gateway and the
local-file browser, all composed into one router. Every one of those is a write,
an OS actuator, an exec, or platform state, and v11.6.0 serves them from
``lattice-host``.

Two survive, because parsing a document is compute and nothing else:

``POST /tools/read_document``
    Text plus metadata out of a ``.pdf`` / ``.docx`` / ``.xlsx`` / ``.pptx`` /
    plain-text file. The same parser matrix ``POST /worker/parse`` wraps.

``GET /tools/pdf_pages``
    Up to twenty PDF pages rasterised to base64 PNG through pypdfium2.

**The approval hop is gone, and the confinement is now absolute.** Both routes
used to accept an approval token minted by ``/permissions/*`` and, with one,
read a file anywhere on the disk. ``/permissions/*`` is a native platform route
now and the approval queue is native state, so a token this process is handed is
one it cannot verify — and a check against an always-empty local queue is not a
weaker guard, it is a guard that refuses everything while looking like it works.
Both routes therefore refuse outright outside ``AGENT_ROOT``. Reading an
arbitrary path on the user's behalf is the host's call to make, with the
approval record it actually holds.
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from lattice_brain.runtime.hooks import dispatch_tool
from latticeai.core.messages import http_error, resolve_language
from latticeai.tools import AGENT_ROOT, ToolError, read_document


class ToolPathRequest(BaseModel):
    path: str = "."
    approval_token: Optional[str] = None


def create_tools_router(*, require_user) -> APIRouter:
    router = APIRouter()

    def _confined(request: Request, raw: str) -> Path:
        """Resolve ``raw`` under ``AGENT_ROOT`` or refuse.

        Relative paths are taken from the workspace root, which is what every
        caller of these two routes sends. An absolute path is accepted only when
        it is already inside the workspace, so ``../`` and ``/etc/passwd`` reach
        the same refusal by the same rule.
        """
        candidate = Path(raw).expanduser()
        target = (
            candidate.resolve()
            if candidate.is_absolute()
            else (AGENT_ROOT / candidate).resolve()
        )
        if target != AGENT_ROOT and AGENT_ROOT not in target.parents:
            raise http_error(403, "tools.path_outside_workspace", resolve_language(request))
        return target

    @router.post("/tools/read_document")
    async def tools_read_document(req: ToolPathRequest, request: Request):
        current_user = require_user(request)
        target = _confined(request, req.path)
        try:
            result = dispatch_tool(
                None,
                "read_document",
                {"path": str(target)},
                lambda: read_document(str(target)),
                user_email=current_user,
                source="workspace",
            )
        except ToolError as exc:
            raise http_error(400, "worker_compute.parse_failed", resolve_language(request)) from exc
        return {"status": "ok", "workspace": str(AGENT_ROOT), "result": result}

    @router.get("/tools/pdf_pages")
    async def tools_pdf_pages(path: str, request: Request, approval_token: Optional[str] = None):
        """Render PDF pages as base64 PNG images using pypdfium2 (Apache-2.0)."""
        require_user(request)
        # Accepted and ignored: the host still sends the token it minted, and
        # rejecting the request for carrying one would break a caller for a
        # field this process no longer has any way to check.
        _ = approval_token
        target = _confined(request, path)
        if not target.exists() or not target.is_file():
            raise http_error(404, "common.file_not_found", resolve_language(request))
        import pypdfium2 as pdfium
        doc = None
        try:
            doc = pdfium.PdfDocument(str(target))
            total = len(doc)
            pages = []
            for i in range(min(total, 20)):  # 최대 20페이지
                page = doc[i]
                bitmap = page.render(scale=1.5)
                pil_image = bitmap.to_pil()
                buf = io.BytesIO()
                pil_image.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode()
                pages.append({"page": i + 1, "b64": b64})
            return {"total": total, "pages": pages}
        except Exception as e:
            raise http_error(500, "tools.pdf_render_failed", resolve_language(request), reason=e)
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception as e:
                    logging.warning("pypdfium2 doc close failed: %s", e)

    return router


__all__ = ["ToolPathRequest", "create_tools_router"]
