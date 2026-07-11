"""Pure filesystem / path / hash / file-classification helpers.

Split out of _kg_common so that module keeps only the text/NLP extraction
logic. These depend only on stdlib and the static tables in _kg_constants —
no dependency back on _kg_common — so the import graph stays linear
(_kg_constants ← _kg_fsutil ← _kg_common). _kg_common re-exports every name
here via ``from ._kg_fsutil import *`` (its computed __all__ then forwards
them to the graph mixins), so existing call sites are unaffected.
"""

from __future__ import annotations

# ruff: noqa: F403,F405

import hashlib
import math
import os
import platform
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ._kg_constants import *  # noqa: F401,F403
from ..utils import now_iso as _now, parse_iso as _parse_iso


def _recency_score(
    updated_at: Optional[str],
    *,
    now: Optional[datetime] = None,
    half_life_days: float = 14.0,
) -> float:
    stamp = _parse_iso(updated_at)
    if not stamp:
        return 0.0
    now = now or datetime.now()
    age_days = max(0.0, (now - stamp).total_seconds() / 86400.0)
    decay = math.log(2) / max(0.1, half_life_days)
    return math.exp(-decay * age_days)


def _slug(text: str, max_len: int = 96) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    value = re.sub(r"[^0-9a-zA-Z가-힣._:@/-]+", "-", value).strip("-")
    return (value or "untitled")[:max_len]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _safe_iso_from_stat_mtime(mtime: float) -> str:
    try:
        return datetime.fromtimestamp(float(mtime)).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _path_fingerprint(path: Path) -> str:
    return _sha256_text(str(path.expanduser().resolve()))[:24]


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _path_parts_lower(path: Path) -> List[str]:
    return [
        part.lower()
        for part in path.parts
        if part and part not in {os.sep, path.anchor}
    ]


def _current_os_type() -> str:
    system = platform.system().lower()
    if system.startswith("darwin"):
        return "macos"
    if system.startswith("windows"):
        return "windows"
    if system.startswith("linux"):
        return "linux"
    return system or "unknown"


def _drive_id_for_path(path: Path) -> str:
    resolved = path.expanduser().resolve()
    if resolved.drive:
        return resolved.drive.upper()
    parts = resolved.parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        return f"/Volumes/{parts[2]}"
    if len(parts) >= 3 and parts[1] == "media":
        return f"/media/{parts[2]}"
    if len(parts) >= 3 and parts[1] == "mnt":
        return f"/mnt/{parts[2]}"
    return resolved.anchor or "/"


def _file_category(ext: str) -> str:
    ext = (ext or "").lower()
    if ext in LOCAL_CODE_EXTENSIONS:
        return "code"
    if ext in LOCAL_TEXT_EXTENSIONS:
        return "text"
    if ext == ".pdf":
        return "pdf"
    if ext in LOCAL_DOCUMENT_EXTENSIONS:
        return "document"
    if ext in LOCAL_SPREADSHEET_EXTENSIONS:
        return "spreadsheet"
    if ext in LOCAL_SLIDE_EXTENSIONS:
        return "slide_deck"
    if ext in LOCAL_IMAGE_EXTENSIONS:
        return "image"
    return "unsupported"


def _node_type_for_category(category: str) -> str:
    return {
        "code": "CodeFile",
        "spreadsheet": "Spreadsheet",
        "slide_deck": "SlideDeck",
        "image": "Image",
        "unsupported": "File",
    }.get(category, "Document")


def _parser_type_for_category(category: str, ext: str) -> str:
    if category in {"text", "code"}:
        return "plain_text"
    if category == "spreadsheet" and ext == ".csv":
        return "csv_text"
    if category == "image":
        return "image_ocr"
    return ext.lstrip(".") or category


def _size_limit_for_category(category: str) -> int:
    return LOCAL_SIZE_LIMITS.get(category, LOCAL_SIZE_LIMITS["document"])


def _is_hidden_path(path: Path, root: Optional[Path] = None) -> bool:
    parts: Iterable[str]
    if root is not None:
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            parts = path.parts
    else:
        parts = path.parts
    return any(part.startswith(".") and part not in {".", ".."} for part in parts)


def _excluded_directory_reason(
    path: Path, *, root: Optional[Path] = None, os_type: Optional[str] = None
) -> Optional[str]:
    os_type = os_type or _current_os_type()
    name = path.name.lower()
    if name in COMMON_EXCLUDED_DIRS:
        return "excluded_folder"
    if _is_hidden_path(path, root):
        return "hidden_folder"
    parts = _path_parts_lower(path)
    if os_type == "windows" and any(part in WINDOWS_EXCLUDED_NAMES for part in parts):
        return "system_folder"
    normalized = path.as_posix()
    root_normalized = root.as_posix() if root else ""

    def _prefix_blocks(prefixes: Tuple[str, ...]) -> bool:
        for prefix in prefixes:
            path_under_prefix = normalized == prefix or normalized.startswith(
                f"{prefix}/"
            )
            root_under_prefix = bool(root_normalized) and (
                root_normalized == prefix or root_normalized.startswith(f"{prefix}/")
            )
            if path_under_prefix and not root_under_prefix:
                return True
        return False

    if os_type == "macos":
        home_library = Path.home() / "Library"
        try:
            root_is_library = bool(root) and _is_relative_to(
                root.expanduser().resolve(), home_library.expanduser().resolve()
            )
            if (
                _is_relative_to(
                    path.expanduser().resolve(), home_library.expanduser().resolve()
                )
                and not root_is_library
            ):
                return "user_library"
        except OSError:
            pass
        if _prefix_blocks(MACOS_EXCLUDED_PREFIXES):
            return "system_folder"
    if os_type == "linux":
        if _prefix_blocks(LINUX_EXCLUDED_PREFIXES):
            return "system_folder"
    return None


def _sensitive_file_reason(path: Path, *, root: Optional[Path] = None) -> Optional[str]:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name in COMMON_EXCLUDED_FILE_NAMES or suffix in COMMON_EXCLUDED_FILE_SUFFIXES:
        return "sensitive_or_excluded_file"
    try:
        rel_text = (
            path.relative_to(root).as_posix().lower()
            if root
            else path.as_posix().lower()
        )
    except ValueError:
        rel_text = path.as_posix().lower()
    tokens = re.split(r"[^0-9a-zA-Z_가-힣]+", rel_text)
    if any(token in SENSITIVE_PATH_KEYWORDS for token in tokens):
        return "sensitive_name"
    return None


def _root_warning(path: Path, os_type: str) -> Optional[str]:
    resolved = path.expanduser().resolve()
    home = Path.home().expanduser().resolve()
    if os_type == "macos" and resolved == home:
        return "홈 전체에는 설정/숨김 폴더가 포함될 수 있습니다. 문서, 데스크탑, 다운로드, 프로젝트 폴더부터 추가하는 것을 권장합니다."
    if os_type == "linux" and resolved.as_posix() == "/":
        return "루트 디렉터리에는 시스템 파일이 포함되어 있습니다. 일반 사용자 폴더나 마운트된 데이터 폴더를 권장합니다."
    if os_type == "windows" and str(resolved).rstrip("\\/").upper() in {"C:", "C:\\"}:
        return "C드라이브에는 Windows 시스템 파일과 앱 설정 파일이 포함되어 있습니다. 하위 폴더를 선택하는 것을 권장합니다."
    return None


def _sample_file(
    path: Path, root: Path, status: str, reason: str = ""
) -> Dict[str, Any]:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.name
    try:
        stat = path.stat()
        size = stat.st_size if path.is_file() else None
        modified_at = _safe_iso_from_stat_mtime(stat.st_mtime)
    except OSError:
        size = None
        modified_at = ""
    return {
        "path": str(path),
        "relative_path": rel,
        "name": path.name,
        "extension": path.suffix.lower(),
        "status": status,
        "reason": reason,
        "size_bytes": size,
        "modified_at": modified_at,
    }


__all__ = [
    "_now",
    "_parse_iso",
    "_recency_score",
    "_slug",
    "_sha256_bytes",
    "_sha256_text",
    "_safe_iso_from_stat_mtime",
    "_path_fingerprint",
    "_is_relative_to",
    "_path_parts_lower",
    "_current_os_type",
    "_drive_id_for_path",
    "_file_category",
    "_node_type_for_category",
    "_parser_type_for_category",
    "_size_limit_for_category",
    "_is_hidden_path",
    "_excluded_directory_reason",
    "_sensitive_file_reason",
    "_root_warning",
    "_sample_file",
]
