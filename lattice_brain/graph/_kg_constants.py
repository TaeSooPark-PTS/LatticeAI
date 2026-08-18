"""Static constants for the SQLite knowledge graph.

Extracted verbatim from ``_kg_common`` so the grab-bag module keeps only logic.
These are pure literals (projection/format versions, the local-ingestion file
classification tables, and the OS exclusion lists). ``_kg_common`` re-exports
every name here, so existing ``from ._kg_common import <CONST>`` sites are
unaffected.
"""

from __future__ import annotations

# Bump when the v2 projection layout changes (columns, normalization rules).
# On init, a stale projection is dropped and rebuilt from the authoritative
# legacy tables — safe because nodes_v2/edges_v2 only ever hold a derived view.
# v4: summary nullable + verbatim (byte-faithful) projection of legacy values.
_PROJECTION_VERSION = 4
_KG_DB_FORMAT_VERSION = 4
_KG_DB_FORMAT_KEY = "db_format_version"
_V2_WRITE_MASTER_KEY = "v2_write_mastered_at"

GRAPH_SCHEMA_VERSION = 1

LOCAL_TEXT_EXTENSIONS = {".txt", ".md"}
LOCAL_CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    # v12.0.0: `.htm` sat beside `.html` in every other table (the chunker's
    # prose list, the parser matrix) and was missing only here, so a folder of
    # `.htm` pages was scanned past in silence. `.rs` was missing outright —
    # this repository's own Rust half was invisible to its own folder ingest.
    ".htm",
    ".rs",
    ".go",
    ".css",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".sql",
    ".sh",
    ".zsh",
    ".toml",
    ".ini",
}
LOCAL_DOCUMENT_EXTENSIONS = {".pdf", ".docx"}
LOCAL_SPREADSHEET_EXTENSIONS = {".xlsx", ".csv"}
LOCAL_SLIDE_EXTENSIONS = {".pptx"}
LOCAL_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
LOCAL_SUPPORTED_EXTENSIONS = (
    LOCAL_TEXT_EXTENSIONS
    | LOCAL_CODE_EXTENSIONS
    | LOCAL_DOCUMENT_EXTENSIONS
    | LOCAL_SPREADSHEET_EXTENSIONS
    | LOCAL_SLIDE_EXTENSIONS
    | LOCAL_IMAGE_EXTENSIONS
)

LOCAL_SIZE_LIMITS = {
    "text": 4_000_000,
    "code": 4_000_000,
    "pdf": 50_000_000,
    "document": 50_000_000,
    "spreadsheet": 50_000_000,
    "slide_deck": 50_000_000,
    "image": 100_000_000,
}

COMMON_EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    ".turbo",
    "dist",
    "build",
    "target",
    "out",
    "coverage",
    ".cache",
    ".config",
    ".ssh",
    ".gnupg",
    ".docker",
    ".kube",
    ".aws",
    ".azure",
    ".npm",
    ".pnpm-store",
    ".yarn",
    ".bun",
    ".cargo",
    ".rustup",
    ".pyenv",
    ".conda",
    ".local",
    ".claude",
    ".codex",
    ".cursor",
    ".copilot",
    ".antigravity",
    ".antigravity-ide",
}

COMMON_EXCLUDED_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "id_rsa",
    "id_ed25519",
    "authorized_keys",
    "known_hosts",
    "credentials.json",
    "service-account.json",
    "token.json",
    "secrets.json",
    "cookies",
    "login data",
    "history",
    "web data",
    ".ds_store",
    "thumbs.db",
}
COMMON_EXCLUDED_FILE_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".kdbx",
    ".wallet",
    ".sqlite",
    ".db",
    ".exe",
    ".dll",
    ".sys",
    ".msi",
    ".dmg",
    ".pkg",
    ".app",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".rar",
    ".mp4",
    ".mov",
    ".mp3",
    ".wav",
    ".tmp",
    ".bak",
    ".lock",
}
SENSITIVE_PATH_KEYWORDS = {
    "secret",
    "secrets",
    "token",
    "password",
    "passwd",
    "credential",
    "credentials",
    "private",
    "key",
    "wallet",
    "recovery",
    "seed",
    "mnemonic",
    "cookie",
    "session",
    "auth",
    "oauth",
    "certificate",
    "cert",
    "api_key",
    "apikey",
}

MACOS_EXCLUDED_PREFIXES = (
    "/System",
    "/Library",
    "/Applications",
    "/private",
    "/tmp",
    "/var",
)
WINDOWS_EXCLUDED_NAMES = {
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "appdata",
    "$recycle.bin",
    "system volume information",
    "recovery",
    "perflogs",
    "intel",
    "amd",
    "nvidia",
}
LINUX_EXCLUDED_PREFIXES = (
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib64",
    "/proc",
    "/root",
    "/run",
    "/sbin",
    "/sys",
    "/tmp",
    "/usr",
    "/var",
    "/snap",
    "/lost+found",
)

__all__ = [
    "_PROJECTION_VERSION",
    "_KG_DB_FORMAT_VERSION",
    "_KG_DB_FORMAT_KEY",
    "_V2_WRITE_MASTER_KEY",
    "GRAPH_SCHEMA_VERSION",
    "LOCAL_TEXT_EXTENSIONS",
    "LOCAL_CODE_EXTENSIONS",
    "LOCAL_DOCUMENT_EXTENSIONS",
    "LOCAL_SPREADSHEET_EXTENSIONS",
    "LOCAL_SLIDE_EXTENSIONS",
    "LOCAL_IMAGE_EXTENSIONS",
    "LOCAL_SUPPORTED_EXTENSIONS",
    "LOCAL_SIZE_LIMITS",
    "COMMON_EXCLUDED_DIRS",
    "COMMON_EXCLUDED_FILE_NAMES",
    "COMMON_EXCLUDED_FILE_SUFFIXES",
    "SENSITIVE_PATH_KEYWORDS",
    "MACOS_EXCLUDED_PREFIXES",
    "WINDOWS_EXCLUDED_NAMES",
    "LINUX_EXCLUDED_PREFIXES",
]
