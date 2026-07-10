from __future__ import annotations

# ruff: noqa: F403,F405

from ._kg_common import *  # noqa: F403,F401


class KnowledgeGraphDocumentsMixin:
    def _ingest_structure_nodes(
        self,
        conn: sqlite3.Connection,
        file_id: str,
        filename: str,
        structure: Dict[str, Any],
        *,
        owner: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> None:
        for slide in structure.get("slides") or []:
            index = slide.get("index")
            slide_id = f"slide:{_sha256_text(f'{file_id}:slide:{index}')[:24]}"
            title = f"{filename} slide {index}"
            summary = "\n".join(slide.get("texts") or [])[:800]
            self._upsert_node(
                conn,
                slide_id,
                "Slide",
                title,
                summary=summary,
                metadata={**slide, "workspace_id": workspace_id},
                owner=owner,
                workspace_id=workspace_id,
            )
            self._upsert_edge(conn, file_id, slide_id, "has_slide")
            for text in slide.get("texts") or []:
                for topic in _topic_candidates(text, limit=4):
                    topic_key = f"{workspace_id}|{topic}" if workspace_id else topic
                    topic_id = f"topic:{_sha256_text(topic_key)[:24]}" if workspace_id else f"topic:{_slug(topic)}"
                    self._upsert_node(
                        conn,
                        topic_id,
                        "Topic",
                        topic,
                        metadata={"auto_extracted": True, "workspace_id": workspace_id},
                        owner=owner,
                        workspace_id=workspace_id,
                    )
                    self._upsert_edge(conn, slide_id, topic_id, "discusses", weight=0.6)

        for page in structure.get("pages") or []:
            index = page.get("index")
            page_id = f"page:{_sha256_text(f'{file_id}:page:{index}')[:24]}"
            title = f"{filename} page {index}"
            self._upsert_node(
                conn,
                page_id,
                "Page",
                title,
                summary=page.get("preview") or "",
                metadata={**page, "workspace_id": workspace_id},
                owner=owner,
                workspace_id=workspace_id,
            )
            self._upsert_edge(conn, file_id, page_id, "has_page")
            for topic in _topic_candidates(page.get("preview") or "", limit=4):
                topic_key = f"{workspace_id}|{topic}" if workspace_id else topic
                topic_id = f"topic:{_sha256_text(topic_key)[:24]}" if workspace_id else f"topic:{_slug(topic)}"
                self._upsert_node(
                    conn,
                    topic_id,
                    "Topic",
                    topic,
                    metadata={"auto_extracted": True, "workspace_id": workspace_id},
                    owner=owner,
                    workspace_id=workspace_id,
                )
                self._upsert_edge(conn, page_id, topic_id, "discusses", weight=0.6)

        for sheet in structure.get("sheets") or []:
            sheet_title = sheet.get("title")
            sheet_id = f"sheet:{_sha256_text(f'{file_id}:sheet:{sheet_title}')[:24]}"
            self._upsert_node(
                conn,
                sheet_id,
                "Sheet",
                f"{filename} / {sheet_title}",
                metadata={**sheet, "workspace_id": workspace_id},
                owner=owner,
                workspace_id=workspace_id,
            )
            self._upsert_edge(conn, file_id, sheet_id, "has_sheet")

        for image in structure.get("images") or []:
            image_key = image.get("sha256") or _sha256_text(
                json.dumps(image, ensure_ascii=False, sort_keys=True)
            )
            scoped_image_key = f"{workspace_id}|{image_key}" if workspace_id else str(image_key)
            image_id = f"image:{_sha256_text(scoped_image_key)[:24]}" if workspace_id else f"image:{str(image_key)[:24]}"
            title_parts = [filename, "image"]
            if image.get("page"):
                title_parts.append(f"page {image.get('page')}")
            if image.get("name"):
                title_parts.append(str(image.get("name")).split("/")[-1])
            self._upsert_node(
                conn,
                image_id,
                "Image",
                " / ".join(title_parts),
                metadata={**image, "workspace_id": workspace_id},
                owner=owner,
                workspace_id=workspace_id,
            )
            self._upsert_edge(conn, file_id, image_id, "contains_image")

    def _document_structure(self, path: Path, ext: str) -> Dict[str, Any]:
        try:
            if ext == ".pptx":
                return self._pptx_structure(path)
            if ext == ".pdf":
                return self._pdf_structure(path)
            if ext == ".docx":
                return self._docx_structure(path)
            if ext == ".xlsx":
                return self._xlsx_structure(path)
        except Exception as exc:
            return {"error": str(exc)}
        return {}

    def _pptx_structure(self, path: Path) -> Dict[str, Any]:
        result: Dict[str, Any] = {"slides": [], "images": []}
        try:
            from PIL import Image
            from pptx import Presentation

            prs = Presentation(str(path))
            for slide_index, slide in enumerate(prs.slides, start=1):
                slide_info = {"index": slide_index, "shapes": [], "texts": []}
                for shape_index, shape in enumerate(slide.shapes, start=1):
                    shape_info = {
                        "index": shape_index,
                        "name": getattr(shape, "name", ""),
                        "shape_type": str(getattr(shape, "shape_type", "")),
                        "bbox": {
                            "left": int(getattr(shape, "left", 0) or 0),
                            "top": int(getattr(shape, "top", 0) or 0),
                            "width": int(getattr(shape, "width", 0) or 0),
                            "height": int(getattr(shape, "height", 0) or 0),
                        },
                    }
                    if getattr(shape, "has_text_frame", False):
                        text = shape.text_frame.text.strip()
                        if text:
                            shape_info["text"] = text[:1000]
                            slide_info["texts"].append(text)
                    slide_info["shapes"].append(shape_info)
                result["slides"].append(slide_info)
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    if not name.startswith("ppt/media/"):
                        continue
                    data = zf.read(name)
                    image_info: Dict[str, Any] = {
                        "name": name,
                        "bytes": len(data),
                        "sha256": _sha256_bytes(data),
                    }
                    try:
                        from io import BytesIO

                        with Image.open(BytesIO(data)) as img:
                            image_info.update(
                                {
                                    "width": img.width,
                                    "height": img.height,
                                    "format": img.format,
                                }
                            )
                    except Exception:
                        pass
                    result["images"].append(image_info)
        except Exception as exc:
            result["error"] = str(exc)
        return result

    def _pdf_structure(self, path: Path) -> Dict[str, Any]:
        result: Dict[str, Any] = {"pages": [], "images": []}
        try:
            import pdfplumber

            with pdfplumber.open(str(path)) as pdf:
                metadata = dict(pdf.metadata or {})
                result["metadata"] = {str(k): str(v) for k, v in metadata.items()}
                for page_index, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text() or ""
                    page_info = {
                        "index": page_index,
                        "width": float(page.width or 0),
                        "height": float(page.height or 0),
                        "chars": len(text),
                        "preview": _clean_text(text)[:500],
                        "image_count": len(page.images or []),
                    }
                    result["pages"].append(page_info)
                    for image_index, image in enumerate(page.images or [], start=1):
                        result["images"].append(
                            {
                                "page": page_index,
                                "index": image_index,
                                "name": image.get("name"),
                                "width": image.get("width"),
                                "height": image.get("height"),
                                "bbox": {
                                    "x0": image.get("x0"),
                                    "top": image.get("top"),
                                    "x1": image.get("x1"),
                                    "bottom": image.get("bottom"),
                                },
                            }
                        )
        except Exception as exc:
            result["error"] = str(exc)
        return result

    def _docx_structure(self, path: Path) -> Dict[str, Any]:
        from docx import Document

        doc = Document(str(path))
        headings = []
        paragraphs = 0
        for p in doc.paragraphs:
            text = p.text.strip()
            if not text:
                continue
            paragraphs += 1
            style = getattr(p.style, "name", "")
            if style.lower().startswith("heading"):
                headings.append({"style": style, "text": text[:240]})
        return {
            "paragraphs": paragraphs,
            "headings": headings[:80],
            "tables": len(doc.tables),
        }

    def _xlsx_structure(self, path: Path) -> Dict[str, Any]:
        from openpyxl import load_workbook

        wb = load_workbook(str(path), read_only=True, data_only=True)
        sheets = []
        for ws in wb.worksheets:
            sheets.append(
                {"title": ws.title, "max_row": ws.max_row, "max_column": ws.max_column}
            )
        return {"sheets": sheets}
