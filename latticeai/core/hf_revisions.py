from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional

HF_REVISIONS_ENV = "LATTICEAI_HF_REVISIONS"
HF_ALLOW_UNPINNED_ENV = "LATTICEAI_HF_ALLOW_UNPINNED"
HF_REQUIRE_PINNED_ENV = "LATTICEAI_HF_REQUIRE_PINNED"
HF_SHA256_ENV = "LATTICEAI_HF_SHA256"
HF_REQUIRE_SHA256_ENV = "LATTICEAI_HF_REQUIRE_SHA256"

REVISION_PINNED = "pinned"
REVISION_OVERRIDE = "override"
REVISION_UNPINNED = "unpinned"
REVISION_CACHED_FALLBACK = "cached_fallback"
REVISION_LOCAL_PATH = "local_path"

PINNED_HF_SHA256: Dict[str, str] = {}

PINNED_HF_REVISIONS: Dict[str, str] = {
    "BAAI/bge-reranker-v2-m3": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
    "LiquidAI/LFM2.5-2.6B": "7141b56cd4650af95c7c7b86983443aed050c96a",
    "LiquidAI/LFM2.5-2.6B-GGUF": "e7caca5d835a3901a8e0d63e94009429bafafdfc",
    "Qwen/Qwen3.5-9B": "c202236235762e1c871ad0ccb60c8ee5ba337b9a",
    "Qwen/Qwen3.6-27B": "1cda14dd71a3f47115850eb790f416f165da1cb5",
    "Qwen/Qwen3.6-35B-A3B": "345d467b117d1fd5eda9b879a5988de9ae5f47a7",
    "Qwen/Qwen3.8-27B": "37561816e66b5b9153b1b64c10968c18c1e6f4a0",
    "ggml-org/Qwen3.6-27B-GGUF": "2a220fd65bf206d69f087f315309b5e031892227",
    "ggml-org/Qwen3.6-35B-A3B-GGUF": "52f9f9b8052fb9c8b69bba4a95eda0f319ac3d2a",
    "ggml-org/Qwen3.8-27B-GGUF": "c1869413dece09109e55b7dcc2c77ef382b0d0bd",
    "ggml-org/embeddinggemma-2-GGUF": "6ea3e3a309d12a4779cfda98b8c19800d2d5da71",
    "ggml-org/embeddinggemma-300M-GGUF": "0f741b5a6585bd53aeb15cd1372c56f2a0f65e12",
    "ggml-org/gemma-4-12B-it-GGUF": "501d13dba70e3c02b1b12c78fd9e6b08ad413960",
    "ggml-org/gemma-4-26B-A4B-it-GGUF": "1ca99aae748b4b01f609fa51eb23682ee5f00b85",
    "ggml-org/gemma-4-31B-it-GGUF": "f39d380138390ea246513ff0f087352f3cfc3a1b",
    "ggml-org/gemma-4-E2B-it-GGUF": "2a8538c2e2d4687953a86c422c7c623cfd022880",
    "ggml-org/gemma-4-E4B-it-GGUF": "8903cb408c7df39cde2ed15fac397e6d7be92951",
    "ggml-org/gpt-oss-20b-GGUF": "41e8a94d15d62070b36cf4553a0250717ecf46b8",
    "google/embeddinggemma-2": "f9972ac22e696a105c482202646979e33e4b5a1e",
    "google/embeddinggemma-300m": "57c266a740f537b4dc058e1b0cda161fd15afa75",
    "google/gemma-4-12B-it": "43c9d014b231474fa7212dbb48a762566c2b255d",
    "google/gemma-4-26B-A4B-it": "4d7ae4984b7db7de8f8457170b3f1a419ee76d52",
    "google/gemma-4-26B-A4B-it-qat-q4_0-gguf": (
        "d1c082be9cf3c8a514acf63b8761f4b41935842e"
    ),
    "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized": (
        "f1e06dc520982d9b9edd76859fdb7ab209449949"
    ),
    "google/gemma-4-31B-it": "842da3794eaa0b77d5f08bae87a17459d91ff475",
    "google/gemma-4-E2B-it": "4adf15748de9e1a0097b6274cee9c15836c41c37",
    "google/gemma-4-E4B-it": "4ecc2ba63bcac23ba4a767fc87025b1450f84c9c",
    "mlx-community/LFM2.5-2.6B-4bit": "72a08228cf703cd7dc0187abf4edbbd8e935f864",
    "mlx-community/Qwen3.5-9B-MLX-4bit": "938d8919941c6e7efd3c7150eff7fe9d12afa631",
    "mlx-community/Qwen3.6-27B-4bit": "c000ac2c2057d94be3fa931000c31723aac53282",
    "mlx-community/Qwen3.6-35B-A3B-4bit": "38740b847e4cb78f352aba30aa41c76e08e6eb46",
    "mlx-community/Qwen3.8-27B-4bit": "b935172e4bb5dcd0543774a34748fb92422e3027",
    "mlx-community/embeddinggemma-2-4bit": "8ab839e127cb8cb305edd71fb7a02c82f2d49ab3",
    "mlx-community/embeddinggemma-2-8bit": "7505ef2f8ddef45efef6d060865f27989b3c9cec",
    "mlx-community/embeddinggemma-300m-4bit": (
        "5d9ef074df3957afc5c77127f208fddbc3c54187"
    ),
    "mlx-community/embeddinggemma-300m-8bit": (
        "c8316c9e35cf8830541b04d315398c67faa6e497"
    ),
    "mlx-community/gemma-4-12B-it-4bit": "73bcf09092aa277861d5a191b989b666f7f32e8f",
    "mlx-community/gemma-4-26B-A4B-it-qat-4bit": (
        "0e3cbab38ce568cf6e23543010d08d03b731910c"
    ),
    "mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit": (
        "0d71c5e2edc2a95391106a6cf6a328694f2420c8"
    ),
    "mlx-community/gemma-4-26b-a4b-it-4bit": (
        "0d77464eeb233a2da68ebf9d7dc4edaac7db956d"
    ),
    "mlx-community/gemma-4-31b-it-4bit": "696d436c404745a59f30e4939a658162b0a9e57f",
    "mlx-community/gemma-4-e2b-it-4bit": "238767527555cb75a05732a84dff5d6ba0dd6809",
    "mlx-community/gemma-4-e4b-it-4bit": "475b9088d29754a3379866cf5aeb6b41acd313c2",
    "mlx-community/gpt-oss-20b-MXFP4-Q8": "773a7da77e569019bb0fd17a554b263738d669a3",
    "mlx-community/multilingual-e5-base-mlx": (
        "576fdf3eab52a419f6d126a0c4d7c59b3882ffde"
    ),
    "mlx-community/multilingual-e5-large-mlx": (
        "b4bd2129cc43ee99a7e91d3dc58374d33a3ce03b"
    ),
    "mlx-community/multilingual-e5-small-mlx": (
        "5030c7625865046d350eeea28f427d80353d0ac0"
    ),
    "mlx-community/snowflake-arctic-embed-l-v2.0-8bit": (
        "98033f618b7503c73aa857bc0656f763a1587e73"
    ),
    "onnx-community/embeddinggemma-300m-ONNX": (
        "5090578d9565bb06545b4552f76e6bc2c93e4a66"
    ),
    "openai/gpt-oss-20b": "6cee5e81ee83917806bbde320786a8fb61efebee",
    "unsloth/Qwen3.5-9B-GGUF": "3885219b6810b007914f3a7950a8d1b469d598a5",
    "unsloth/Qwen3.8-27B-GGUF": "4af51b3542f775b0a8e4b310bfb3f17e01c306c9",
    "unsloth/gemma-4-26B-A4B-it-GGUF": "c099eb48e663fd284577b04978a94ffccb261841",
}

_PINNED_BY_KEY: Dict[str, str] = {
    repo.lower(): sha for repo, sha in PINNED_HF_REVISIONS.items()
}

_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256_HEX = re.compile(r"[0-9a-fA-F]{64}")
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def is_commit_sha(value: Optional[str]) -> bool:
    return bool(value) and _COMMIT_SHA.fullmatch(str(value)) is not None


def is_sha256_hex(value: Optional[str]) -> bool:
    return bool(value) and _SHA256_HEX.fullmatch(str(value).strip()) is not None


def allow_unpinned(env: Optional[Mapping[str, str]] = None) -> bool:
    source = env if env is not None else os.environ
    require_raw = str(source.get(HF_REQUIRE_PINNED_ENV) or "").strip().lower()
    if require_raw in _TRUTHY:
        return False
    allow_raw = str(source.get(HF_ALLOW_UNPINNED_ENV) or "").strip().lower()
    if allow_raw in _FALSY:
        return False
    if allow_raw in _TRUTHY:
        return True
    return True


def require_sha256_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    source = env if env is not None else os.environ
    raw = str(source.get(HF_REQUIRE_SHA256_ENV) or "").strip().lower()
    return raw in _TRUTHY


def pinned_revision(repo_id: str) -> Optional[str]:
    return _PINNED_BY_KEY.get(str(repo_id or "").strip().lower())


def revision_overrides(env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    source = env if env is not None else os.environ
    raw = str(source.get(HF_REVISIONS_ENV) or "").strip()
    overrides: Dict[str, str] = {}
    for entry in raw.split(","):
        item = entry.strip()
        if not item:
            continue
        repo, sep, revision = item.partition("=")
        if not sep or not repo.strip() or not revision.strip():
            raise ValueError(
                f"{HF_REVISIONS_ENV} entry {item!r} must look like org/repo=<revision>"
            )
        overrides[repo.strip().lower()] = revision.strip()
    return overrides


@dataclass(frozen=True)
class RevisionRequest:
    repo_id: str
    revision: Optional[str]
    source: str

    @property
    def explicit(self) -> bool:
        return self.source == REVISION_OVERRIDE


def resolve_revision(
    repo_id: str,
    override: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> RevisionRequest:
    repo = str(repo_id or "").strip()
    explicit = str(override or "").strip()
    if explicit:
        return RevisionRequest(repo, explicit, REVISION_OVERRIDE)
    configured = revision_overrides(env).get(repo.lower())
    if configured:
        return RevisionRequest(repo, configured, REVISION_OVERRIDE)
    pinned = pinned_revision(repo)
    if pinned:
        return RevisionRequest(repo, pinned, REVISION_PINNED)
    return RevisionRequest(repo, None, REVISION_UNPINNED)


@dataclass(frozen=True)
class CachedSnapshot:
    path: Path
    revision: Optional[str]
    source: str


Completeness = Callable[[Path], bool]


def _read_ref(repo_dir: Path, ref: str) -> Optional[str]:
    ref_file = repo_dir / "refs" / ref
    if not ref_file.is_file():
        return None
    value = ref_file.read_text(encoding="utf-8").strip()
    return value or None


def _requested_snapshot(
    repo_dir: Path, revision: Optional[str], is_complete: Completeness
) -> Optional[Path]:
    commit = (
        revision
        if revision and is_commit_sha(revision)
        else _read_ref(repo_dir, revision or "main")
    )
    if not commit:
        return None
    candidate = repo_dir / "snapshots" / commit
    return candidate if is_complete(candidate) else None


def _newest_first(snapshots: Path) -> List[Path]:
    return sorted(
        (item for item in snapshots.iterdir() if item.is_dir()),
        key=lambda item: (item.stat().st_mtime, item.name),
        reverse=True,
    )


def _fallback_snapshot(
    repo_dir: Path, is_complete: Completeness
) -> Optional[CachedSnapshot]:
    main = _requested_snapshot(repo_dir, None, is_complete)
    if main is not None:
        return CachedSnapshot(main, main.name, REVISION_CACHED_FALLBACK)
    snapshots = repo_dir / "snapshots"
    if not snapshots.is_dir():
        return None
    for snapshot in _newest_first(snapshots):
        if is_complete(snapshot):
            return CachedSnapshot(snapshot, snapshot.name, REVISION_CACHED_FALLBACK)
    return None


def find_cached_snapshot(
    request: RevisionRequest,
    roots: Iterable[Path],
    is_complete: Completeness,
) -> Optional[CachedSnapshot]:
    flattened = "models--" + request.repo_id.replace("/", "--")
    root_list = list(roots)
    for root in root_list:
        exact = _requested_snapshot(root / flattened, request.revision, is_complete)
        if exact is not None:
            return CachedSnapshot(exact, exact.name, request.source)
    if request.explicit:
        return None
    for root in root_list:
        fallback = _fallback_snapshot(root / flattened, is_complete)
        if fallback is not None:
            return fallback
        for direct in (root / request.repo_id, root / flattened):
            if is_complete(direct):
                return CachedSnapshot(direct, None, REVISION_LOCAL_PATH)
    return None


def fetch_pinned_snapshot(
    request: RevisionRequest,
    env: Optional[Mapping[str, str]] = None,
) -> CachedSnapshot:
    require_pinned_revision(request, env=env)
    from huggingface_hub import snapshot_download

    raw_path = (
        snapshot_download(repo_id=request.repo_id, revision=request.revision)
        if request.revision
        else snapshot_download(repo_id=request.repo_id)
    )
    path = Path(raw_path)
    verify_snapshot_integrity(path, request.repo_id, env=env)
    revision = path.name if is_commit_sha(path.name) else request.revision
    return CachedSnapshot(path, revision, request.source)


def require_pinned_revision(
    request: RevisionRequest,
    env: Optional[Mapping[str, str]] = None,
) -> RevisionRequest:
    if request.source == REVISION_UNPINNED and not allow_unpinned(env):
        raise ValueError(
            f"Unpinned Hugging Face repo {request.repo_id!r} refused; "
            f"set {HF_REVISIONS_ENV}='{request.repo_id}=<40-char-sha>' "
            f"or opt in with {HF_ALLOW_UNPINNED_ENV}=1"
        )
    return request


def sha256_overrides(env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    source = env if env is not None else os.environ
    raw = str(source.get(HF_SHA256_ENV) or "").strip()
    overrides: Dict[str, str] = {}
    for entry in raw.split(","):
        item = entry.strip()
        if not item:
            continue
        target, sep, digest = item.partition("=")
        clean_target = target.strip().lower()
        clean_digest = digest.strip().lower()
        if not sep or not clean_target or not is_sha256_hex(clean_digest):
            raise ValueError(
                f"{HF_SHA256_ENV} entry {item!r} must look like org/repo/file=<64-hex-sha256>"
            )
        overrides[clean_target] = clean_digest
    return overrides


def expected_sha256(
    repo_id: str,
    filename: str,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    repo_key = str(repo_id or "").strip().lower()
    file_key = str(filename or "").strip().lstrip("/").lower()
    overrides = sha256_overrides(env)
    if repo_key and file_key:
        scoped = f"{repo_key}/{file_key}"
        if scoped in overrides:
            return overrides[scoped]
        if scoped in PINNED_HF_SHA256:
            return PINNED_HF_SHA256[scoped].lower()
    if file_key and file_key in overrides:
        return overrides[file_key]
    return None


def compute_file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_file_sha256(
    path: Path,
    expected: Optional[str] = None,
    *,
    repo_id: Optional[str] = None,
    filename: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    require_digest: Optional[bool] = None,
) -> str:
    if not path.is_file():
        raise ValueError(f"Cannot verify SHA-256 of missing file: {path}")
    digest_expected = expected.strip().lower() if expected else None
    if digest_expected is None and repo_id and filename:
        digest_expected = expected_sha256(repo_id, filename, env=env)
    strict = (
        require_sha256_enabled(env) if require_digest is None else bool(require_digest)
    )
    if digest_expected is None and strict:
        raise ValueError(
            f"SHA-256 verification required for {path.name}, but no expected digest was configured"
        )
    if digest_expected is not None and not is_sha256_hex(digest_expected):
        raise ValueError(
            f"Invalid expected SHA-256 digest for {path.name}: {digest_expected!r}"
        )
    actual = compute_file_sha256(path)
    if digest_expected is not None and actual != digest_expected:
        raise ValueError(
            f"SHA-256 mismatch for {path.name}: expected {digest_expected}, got {actual}"
        )
    return actual


def verify_snapshot_integrity(
    snapshot_dir: Path,
    repo_id: str,
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    verified: Dict[str, str] = {}
    if not snapshot_dir.is_dir():
        return verified
    overrides = sha256_overrides(env)
    repo_prefix = f"{str(repo_id or '').strip().lower()}/"
    has_configured = any(
        key.startswith(repo_prefix) or "/" not in key
        for key in (*overrides.keys(), *PINNED_HF_SHA256.keys())
    )
    strict = require_sha256_enabled(env)
    if not has_configured and not strict:
        return verified
    for file_path in sorted(p for p in snapshot_dir.rglob("*") if p.is_file()):
        rel = file_path.relative_to(snapshot_dir).as_posix()
        digest_expected = expected_sha256(repo_id, rel, env=env)
        if digest_expected is not None or (
            strict and rel.endswith((".safetensors", ".bin", ".gguf", ".onnx"))
        ):
            verified[rel] = verify_file_sha256(
                file_path,
                digest_expected,
                require_digest=strict,
                env=env,
            )
    return verified


__all__ = [
    "HF_ALLOW_UNPINNED_ENV",
    "HF_REQUIRE_PINNED_ENV",
    "HF_REQUIRE_SHA256_ENV",
    "HF_REVISIONS_ENV",
    "HF_SHA256_ENV",
    "PINNED_HF_REVISIONS",
    "PINNED_HF_SHA256",
    "REVISION_CACHED_FALLBACK",
    "REVISION_LOCAL_PATH",
    "REVISION_OVERRIDE",
    "REVISION_PINNED",
    "REVISION_UNPINNED",
    "CachedSnapshot",
    "RevisionRequest",
    "allow_unpinned",
    "compute_file_sha256",
    "expected_sha256",
    "fetch_pinned_snapshot",
    "find_cached_snapshot",
    "is_commit_sha",
    "is_sha256_hex",
    "pinned_revision",
    "require_pinned_revision",
    "require_sha256_enabled",
    "resolve_revision",
    "revision_overrides",
    "sha256_overrides",
    "verify_file_sha256",
    "verify_snapshot_integrity",
]
