"""Orphan i18n key gate.

Keys defined in ``frontend/src/i18n`` but never referenced anywhere else in
``frontend/src`` are dead copy. The gate is bidirectional — new orphans fail,
and stale grandfather entries fail too — so the baseline can only shrink.
"""

from __future__ import annotations

from pathlib import Path

from ._layout_rebuild_common import (
    I18N_DYNAMIC_PREFIX_ALLOWLIST,
    I18N_ORPHAN_FIXTURE_CAP,
    _discover_defined_i18n_keys,
    _discover_orphan_i18n_keys,
    _frontend_src_blob_excluding_i18n_defs,
    _load_known_orphan_baseline,
)


def test_no_new_orphan_i18n_keys():
    """Keys defined in i18n but never referenced in frontend/src are orphans.

    Bidirectional freeze:
      - NEW orphans (panel deleted, keys left in i18n) fail.
      - STALE fixture entries (key re-wired or deleted from i18n) fail so the
        grandfather list must shrink — never only grow.
      - Fixture size is capped by I18N_ORPHAN_FIXTURE_CAP; raising the cap
        requires JUSTIFY comments in the fixture (see file header).
    """
    repo = Path(__file__).resolve().parents[2]
    orphans = _discover_orphan_i18n_keys(repo)
    known = _load_known_orphan_baseline(repo)

    new_orphans = sorted(orphans - known)
    assert not new_orphans, (
        "new orphan i18n keys (defined in frontend/src/i18n but unreferenced "
        "in frontend/src outside definition tables). Either wire them up, "
        "delete them from i18n, or — only when intentional — add them to "
        "tests/unit/fixtures/i18n_known_orphans.txt with a # JUSTIFY: line "
        "and raise I18N_ORPHAN_FIXTURE_CAP in tests/unit/_layout_rebuild_common.py.\n"
        + "\n".join(f"  - {key}" for key in new_orphans)
    )

    stale = sorted(known - orphans)
    assert not stale, (
        "stale entries in tests/unit/fixtures/i18n_known_orphans.txt "
        "(no longer orphans — key was re-wired or removed from i18n). "
        "Remove them from the fixture (and lower I18N_ORPHAN_FIXTURE_CAP).\n"
        + "\n".join(f"  - {key}" for key in stale)
    )

    assert len(known) <= I18N_ORPHAN_FIXTURE_CAP, (
        f"orphan fixture has {len(known)} keys; cap is {I18N_ORPHAN_FIXTURE_CAP}. "
        "Do not grow the fixture without a # JUSTIFY: comment per key and an "
        "explicit raise of I18N_ORPHAN_FIXTURE_CAP in "
        "tests/unit/_layout_rebuild_common.py."
    )
    assert len(orphans) == len(known), (
        f"orphan set size {len(orphans)} != fixture size {len(known)}"
    )

    # Fixture header must document the growth policy (review gate).
    fixture_text = (
        repo / "tests" / "unit" / "fixtures" / "i18n_known_orphans.txt"
    ).read_text(encoding="utf-8")
    assert "GROWTH POLICY" in fixture_text
    assert "JUSTIFY" in fixture_text

    # Allowlist must stay explicit and non-empty so a wiped list is not silent.
    assert len(I18N_DYNAMIC_PREFIX_ALLOWLIST) >= 10
    assert all(p.endswith(".") for p in I18N_DYNAMIC_PREFIX_ALLOWLIST), (
        "dynamic prefixes must end with '.' so they only match assembled keys"
    )

    # Sanity: a clearly used key is never reported as orphan.
    assert "shell.route.brain" not in orphans
    assert "shell.localBadge" not in orphans


def test_orphan_gate_detects_deleted_panel_keys(tmp_path: Path):
    """Synthetic failure: drop all references to 8 keys → gate sees 8 new orphans."""
    repo = Path(__file__).resolve().parents[2]
    known = _load_known_orphan_baseline(repo)
    defined = _discover_defined_i18n_keys(repo)
    blob = _frontend_src_blob_excluding_i18n_defs(repo)

    # Only exact-referenced keys that are not dynamic-prefix-covered and not
    # already grandfathered. Dynamic-prefix keys never surface as orphans.
    def _is_exact_referenced(key: str) -> bool:
        return f'"{key}"' in blob or f"'{key}'" in blob or f"`{key}`" in blob

    def _is_dynamic(key: str) -> bool:
        return any(key.startswith(prefix) for prefix in I18N_DYNAMIC_PREFIX_ALLOWLIST)

    live = [
        key
        for key in sorted(defined)
        if _is_exact_referenced(key) and not _is_dynamic(key) and key not in known
    ]
    # Prefer a single panel namespace so the scenario matches "panel deleted".
    candidates = [k for k in live if k.startswith("act.")] or live
    assert len(candidates) >= 8, "need at least 8 live keys to simulate panel deletion"
    doomed = set(candidates[:8])

    scrubbed = blob
    for key in doomed:
        scrubbed = scrubbed.replace(f'"{key}"', '""').replace(f"'{key}'", "''")

    orphans: set[str] = set()
    for key in defined:
        if _is_dynamic(key):
            continue
        if f'"{key}"' in scrubbed or f"'{key}'" in scrubbed or f"`{key}`" in scrubbed:
            continue
        orphans.add(key)
    new_orphans = orphans - known
    assert doomed.issubset(new_orphans), (
        f"gate must flag scrubbed keys as new orphans; missing "
        f"{sorted(doomed - new_orphans)}"
    )
