"""Office/PDF binary builders and Pydantic request schemas for worker_compute."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from latticeai.core.quiet import quiet
from latticeai.tools import _CJK_FONT_CANDIDATES, ToolError
from latticeai.tools.documents import _body_to_str

# ── document builders: create_* minus the filesystem ────────────────────────


def build_docx_bytes(title: str, body: Any) -> bytes:
    """``tools.documents.create_docx``'s document, returned instead of saved."""
    try:
        from docx import Document
    except Exception as exc:  # noqa: BLE001 — an absent library is a configuration
        raise ToolError(
            "python-docx is not installed. Run `pip install -r requirements.txt`."
        ) from exc

    document = Document()
    if title:
        document.add_heading(str(title), level=1)
    for block in _body_to_str(body).split("\n\n"):
        text = block.strip()
        if text:
            document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def build_xlsx_bytes(rows: Any, sheet_name: str = "Sheet1") -> bytes:
    """``tools.documents.create_xlsx``'s workbook, returned instead of saved."""
    try:
        from openpyxl import Workbook
    except Exception as exc:  # noqa: BLE001 — an absent library is a configuration
        raise ToolError(
            "openpyxl is not installed. Run `pip install -r requirements.txt`."
        ) from exc

    if not isinstance(rows, list) or not all(isinstance(row, list) for row in rows):
        raise ToolError("Rows must be a list of lists.")

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = (sheet_name or "Sheet1")[:31]
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def build_pptx_bytes(title: str, slides: Any) -> bytes:
    """``tools.documents.create_pptx``'s deck, returned instead of saved."""
    try:
        from pptx import Presentation
    except Exception as exc:  # noqa: BLE001 — an absent library is a configuration
        raise ToolError(
            "python-pptx is not installed. Run `pip install -r requirements.txt`."
        ) from exc

    presentation = Presentation()
    first_layout = presentation.slide_layouts[0]
    first = presentation.slides.add_slide(first_layout)
    first.shapes.title.text = title or "Presentation"
    first.placeholders[1].text = ""

    content_layout = presentation.slide_layouts[1]
    for slide_data in slides or []:
        slide = presentation.slides.add_slide(content_layout)
        slide.shapes.title.text = str(slide_data.get("title") or "Slide")
        body = slide.placeholders[1].text_frame
        body.clear()
        bullets = slide_data.get("bullets") or []
        if isinstance(bullets, str):
            bullets = [bullets]
        for index, bullet in enumerate(bullets):
            paragraph = body.paragraphs[0] if index == 0 else body.add_paragraph()
            paragraph.text = str(bullet)
            paragraph.level = 0

    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def build_pdf_bytes(title: str, body: Any) -> bytes:
    """``tools.documents.create_pdf``'s PDF, returned instead of saved.

    The CJK font hunt is kept verbatim: without it a Korean document renders as
    a page of empty boxes, and that is not a cosmetic difference.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except Exception as exc:  # noqa: BLE001 — an absent library is a configuration
        raise ToolError(
            "reportlab is not installed. Run `pip install reportlab`."
        ) from exc

    font_name = "Helvetica"
    from . import worker_compute as _wc

    for font_path in getattr(_wc, "CJK_FONT_CANDIDATES", _CJK_FONT_CANDIDATES):
        if Path(font_path).exists():
            try:
                pdfmetrics.registerFont(TTFont("KoreanFont", font_path))
                font_name = "KoreanFont"
            except Exception:  # noqa: BLE001 — a broken font file is not a failed render
                quiet("CJK font registration")
            break

    title_style = ParagraphStyle(
        "Title", fontName=font_name, fontSize=18, spaceAfter=8, leading=24
    )
    body_style = ParagraphStyle(
        "Body", fontName=font_name, fontSize=11, spaceAfter=6, leading=16
    )

    story: List[Any] = []
    if title:
        story.append(Paragraph(str(title), title_style))
        story.append(Spacer(1, 4 * mm))

    for block in _body_to_str(body).split("\n\n"):
        text = block.strip()
        if text:
            safe_text = (
                text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            story.append(Paragraph(safe_text, body_style))
            story.append(Spacer(1, 2 * mm))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )
    doc.build(story)
    return buffer.getvalue()


# ── request bodies ──────────────────────────────────────────────────────────


class EmbedRequest(BaseModel):
    """Texts in, vectors out. ``kind`` selects the write-path clamp."""

    texts: List[str] = Field(default_factory=list)
    kind: str = "passage"


class ParseRequest(BaseModel):
    """One document, by name and by bytes. ``filename`` picks the parser."""

    filename: str
    content_b64: str


class RenderDocxRequest(BaseModel):
    """``ToolDocxRequest``, verbatim — W3 forwards its body unchanged."""

    title: str = ""
    body: str = ""
    filename: str = "document.docx"


class RenderXlsxRequest(BaseModel):
    """``ToolXlsxRequest``, verbatim."""

    rows: List[List] = Field(default_factory=list)
    filename: str = "spreadsheet.xlsx"
    sheet_name: str = "Sheet1"


class RenderPptxRequest(BaseModel):
    """``ToolPptxRequest``, verbatim."""

    title: str = ""
    slides: List[Dict] = Field(default_factory=list)
    filename: str = "presentation.pptx"


class RenderPdfRequest(BaseModel):
    """``ToolPdfRequest``, verbatim."""

    title: str = ""
    body: str = ""
    filename: str = "document.pdf"


class AsrRequest(BaseModel):
    """One recording. ``filename``/``mime`` only choose the temp suffix."""

    audio_b64: str
    mime: Optional[str] = None
    filename: Optional[str] = None


class ExtractRequest(BaseModel):
    """The text one ingest door would hand ``_extract_concepts``."""

    text: str = ""
    kind: str = "message"


class VectorQueryRequest(BaseModel):
    """Approximate neighbours from the HNSW sidecar. ``k`` is capped at 200."""

    workspace: Optional[str] = None
    embedding_model: str
    embedding_dim: int
    vector: List[float] = Field(default_factory=list)
    k: int = 10
