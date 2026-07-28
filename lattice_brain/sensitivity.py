"""Which local sources are never allowed to leave the machine.

This lives in Brain Core rather than in the app layer because it is a property
of the *data*, not of any transport: a node stamped never-leaves stays that way
whether it is read by the cloud path, an export, or a future surface nobody has
written yet. Brain Core cannot import from ``latticeai`` (see
``tests/unit/test_import_guard.py``), so the app-layer network boundary imports
this, not the other way round.

Deliberately narrow. These are locations whose *entire purpose* is to hold
secrets, so a match is a fact about the path rather than a guess about content.
A broader heuristic — scanning text for things that look like keys — would
produce false confidence in both directions: it would miss secrets in unusual
shapes and quarantine ordinary notes that merely discuss credentials.
"""

from __future__ import annotations

from typing import Any, Optional

# Path fragments checked case-insensitively against the POSIX form of the path.
SENSITIVE_PATH_FRAGMENTS: tuple = (
    "/.ssh/",
    "/.gnupg/",
    "/.aws/",
    "/.kube/",
    "/.docker/config.json",
    "/.netrc",
    "/.npmrc",
    "/.pypirc",
    "/.git-credentials",
    "id_rsa",
    "id_ed25519",
    ".pem",
    ".p12",
    ".pfx",
    ".keystore",
)

# Exact filenames (in any directory) that are secret-bearing by convention.
SENSITIVE_FILENAMES: frozenset = frozenset({
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "credentials",
    "secrets.yaml",
    "secrets.yml",
    "secrets.json",
})

#: Metadata key stamped on a node that must never leave the machine.
LOCAL_ONLY_FLAG = "local_only"
#: Companion key holding the human-readable reason.
LOCAL_ONLY_REASON = "local_only_reason"


def sensitive_reason_for_path(path: Any) -> Optional[str]:
    """Return why ``path`` is never-leaves, or ``None`` if it is ordinary."""
    if not path:
        return None
    lowered = str(path).replace("\\", "/").lower()
    name = lowered.rsplit("/", 1)[-1]
    if name in SENSITIVE_FILENAMES:
        return f"{name!r} is a secret-bearing filename"
    for fragment in SENSITIVE_PATH_FRAGMENTS:
        if fragment in lowered:
            return f"path contains {fragment!r}"
    return None


def stamp_sensitivity(metadata: dict, path: Any) -> Optional[str]:
    """Stamp never-leaves onto ``metadata`` when ``path`` warrants it.

    Returns the reason it stamped, or ``None``. Never clears an existing flag —
    a user or another rule may have set it for a reason this function cannot
    see, and downgrading a never-leaves marker is not a decision code should
    make on its own.
    """
    reason = sensitive_reason_for_path(path)
    if reason:
        metadata[LOCAL_ONLY_FLAG] = True
        metadata.setdefault(LOCAL_ONLY_REASON, reason)
    return reason


__all__ = [
    "SENSITIVE_PATH_FRAGMENTS",
    "SENSITIVE_FILENAMES",
    "LOCAL_ONLY_FLAG",
    "LOCAL_ONLY_REASON",
    "sensitive_reason_for_path",
    "stamp_sensitivity",
]
