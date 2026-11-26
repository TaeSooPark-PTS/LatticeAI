from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from latticeai.core.quiet import quiet
from latticeai.tools import _CJK_FONT_CANDIDATES, ToolError
from latticeai.tools.documents import _body_to_str


def build_docx_bytes(title: str, body: Any) -> bytes:
    try:
        from docx import Document
    except Exception as exc:
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
    try:
        from openpyxl import Workbook
    except Exception as exc:
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
    try:
        from pptx import Presentation
    except Exception as exc:
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
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFError, TTFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except Exception as exc:
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
            except (OSError, RuntimeError, ValueError, LookupError, TTFError):
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


class EmbedRequest(BaseModel):
    texts: List[str] = Field(default_factory=list)
    kind: str = "passage"


class ParseRequest(BaseModel):
    filename: str
    content_b64: str


class RenderDocxRequest(BaseModel):
    title: str = ""
    body: str = ""
    filename: str = "document.docx"


class RenderXlsxRequest(BaseModel):
    rows: List[List] = Field(default_factory=list)
    filename: str = "spreadsheet.xlsx"
    sheet_name: str = "Sheet1"


class RenderPptxRequest(BaseModel):
    title: str = ""
    slides: List[Dict] = Field(default_factory=list)
    filename: str = "presentation.pptx"


class RenderPdfRequest(BaseModel):
    title: str = ""
    body: str = ""
    filename: str = "document.pdf"


class AsrRequest(BaseModel):
    audio_b64: str
    mime: Optional[str] = None
    filename: Optional[str] = None


class ExtractRequest(BaseModel):
    text: str = ""
    kind: str = "message"


class RerankRequest(BaseModel):
    query: str = ""
    documents: List[str] = Field(default_factory=list)


class VectorQueryRequest(BaseModel):
    workspace: Optional[str] = None
    embedding_model: str
    embedding_dim: int
    vector: List[float] = Field(default_factory=list)
    k: int = 10
