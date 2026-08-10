"""v11.2.0 F1 — Notion / Git / mail / calendar, through the one ingestion gate.

11.1.0 said it plainly: Obsidian was the only interop bridge, and Notion,
email, calendar and Git were *scoped out rather than stubbed*. These are the
promises that make closing that honest rather than a checkbox:

* **One door.** Every bridge builds ``IngestionItem`` values and hands them to
  ``IngestionPipeline.ingest`` — the same hashing, hooks, provenance and
  workspace scoping as a folder scan. No second write path.
* **Local files, no vendor APIs.** A Notion *export*, a repository *path*, an
  ``.eml`` on disk. Nothing needs a token and nothing leaves the machine — and
  the parts that remain out of scope (IMAP, a system calendar) say so.
* **Honest failure.** A missing ``git``, an unreadable file, an unresolvable
  link: reported, never dropped and never guessed at.
* **``dry_run`` writes nothing**, and says what a real run would have touched.

The git binary is seamed; this suite never runs a subprocess.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import latticeai.services.interop_bridges as bridges  # noqa: E402
from lattice_brain.graph.store import KnowledgeGraphStore  # noqa: E402
from lattice_brain.ingestion import IngestionPipeline  # noqa: E402
from latticeai.api.local_files import create_local_files_router  # noqa: E402
from latticeai.api.permissions import create_permissions_router  # noqa: E402
from latticeai.services.interop_bridges import (  # noqa: E402
    GIT_UNAVAILABLE_DETAIL,
    GitHistoryBridge,
    MailCalendarBridge,
    NotionExportBridge,
    bridge_status,
    build_bridge,
    email_body,
    notion_key,
    notion_links,
    notion_title,
    parse_git_log,
    parse_ics,
)

PAGE_ID = "1a2b3c4d5e6f70819aabbccddeeff001"
OTHER_ID = "0f1e2d3c4b5a69788877665544332211"

EML = """From: Alice <alice@example.com>
To: Bob <bob@example.com>
Subject: Release plan
Date: Mon, 4 Aug 2026 09:00:00 +0900
Message-ID: <plan-1@example.com>
Content-Type: text/plain; charset="utf-8"

릴리스는 금요일에 나갑니다. 준비해 주세요.
""".encode()

HTML_ONLY = b"""From: Alice <alice@example.com>
Subject: Newsletter
Content-Type: text/html; charset="utf-8"

<html><body><p>hello</p></body></html>
"""

ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:evt-1@example.com
SUMMARY:릴리스 리뷰
DTSTART;TZID=Asia/Seoul:20260804T090000
DTEND:20260804T100000
LOCATION:회의실 A
DESCRIPTION:릴리스 노트를 함께 봅니다.\\n체크리스트 포함
END:VEVENT
BEGIN:VEVENT
SUMMARY:이름 없는 일정
END:VEVENT
BEGIN:VEVENT
SUMMARY:버려질 일정
END:VCALENDAR
"""


@pytest.fixture
def store(tmp_path):
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


@pytest.fixture
def pipeline(store):
    return IngestionPipeline(store)


def _notion_export(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"Roadmap {PAGE_ID}.md").write_text(
        f"# Roadmap\n\n다음은 [Retro](Retro%20{OTHER_ID}.md) 문서를 보세요.\n"
        "외부는 [사이트](https://example.com) 이고 그림은 [로고](logo.png) 입니다.\n"
        f"자기 자신 [Roadmap](Roadmap%20{PAGE_ID}.md) 링크도 있습니다.\n"
        "없는 문서 [Ghost](Ghost.md) 도 있습니다.\n",
        encoding="utf-8",
    )
    (root / f"Retro {OTHER_ID}.md").write_text(
        "지난 릴리스 회고입니다. 배포는 순조로웠습니다.\n", encoding="utf-8",
    )
    (root / "Tasks.csv").write_text("name,status\n작업,done\n", encoding="utf-8")
    (root / "empty.md").write_text("   \n", encoding="utf-8")
    (root / ".hidden.md").write_text("숨김", encoding="utf-8")
    nested = root / ".obsidian"
    nested.mkdir()
    (nested / "config.md").write_text("무시", encoding="utf-8")
    return root


# ── Notion filename normalization ────────────────────────────────────────────
def test_the_export_id_suffix_is_split_off_the_title_not_lost():
    title, page_id = notion_title(f"Roadmap {PAGE_ID}")
    assert (title, page_id) == ("Roadmap", PAGE_ID)
    assert notion_title("Plain note") == ("Plain note", None)
    # A file whose whole name is an id keeps the id as its title rather than
    # becoming an empty string.
    assert notion_title(PAGE_ID) == (PAGE_ID, PAGE_ID)


def test_two_exports_of_one_page_resolve_to_the_same_key():
    assert notion_key(f"Roadmap {PAGE_ID}.md") == notion_key(f"Roadmap {OTHER_ID}.md")
    assert notion_key(f"./Docs/Roadmap {PAGE_ID}.md") == "docs/roadmap"


def test_only_relative_page_links_become_edges():
    body = (
        f"[a](Retro%20{OTHER_ID}.md) [b](https://x.test) [c](mailto:a@b.test) "
        f"[d](logo.png) [e](Retro%20{OTHER_ID}.md) [f]() [g](Notes.csv)"
    )
    assert notion_links(body) == ["retro", "notes"]


# ── the Notion bridge ────────────────────────────────────────────────────────
def test_a_notion_export_lands_as_pages_with_reference_edges(tmp_path, pipeline, store):
    root = _notion_export(tmp_path / "export")
    bridge = NotionExportBridge(pipeline=pipeline, knowledge_graph=store)

    preview = bridge.sync(root, workspace_id="ws-1", dry_run=True)
    assert preview["status"] == "dry_run"
    assert preview["items"] == 3            # two pages plus the database csv
    assert preview["scanned"] == 4          # the empty page is scanned, not kept
    assert preview["skipped"]["empty"] == 1
    assert preview["links"]["resolved"] == 1  # self-link and Ghost do not count
    assert preview["ingested"] == 0

    summary = bridge.sync(root, workspace_id="ws-1", owner="me@local")
    assert summary["status"] == "ok"
    assert summary["ingested"] == 3
    assert summary["edges"]["status"] == "written"
    assert summary["edges"]["references"] == 1
    assert summary["links"]["written"] == 1

    again = bridge.sync(root, workspace_id="ws-1", owner="me@local")
    assert again["duplicate"] == 3 and again["ingested"] == 0


def test_a_zip_export_is_read_the_same_way(tmp_path, pipeline, store):
    root = _notion_export(tmp_path / "export")
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(root).as_posix())

    summary = NotionExportBridge(pipeline=pipeline, knowledge_graph=store).sync(
        archive, workspace_id="ws-1", dry_run=True,
    )
    assert summary["status"] == "dry_run" and summary["items"] == 3


def test_an_unsafe_or_broken_zip_is_refused_with_a_reason(tmp_path, pipeline):
    bridge = NotionExportBridge(pipeline=pipeline)
    escaping = tmp_path / "escape.zip"
    with zipfile.ZipFile(escaping, "w") as zf:
        zf.writestr("../outside.md", "nope")
    refused = bridge.sync(escaping)
    assert refused["status"] == "failed" and "unsafe path" in refused["detail"]

    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(b"PK not really")
    broken = bridge.sync(corrupt)
    assert broken["status"] == "failed" and "could not be read" in broken["detail"]


def test_a_missing_or_nonsensical_export_path_is_reported(tmp_path, pipeline):
    bridge = NotionExportBridge(pipeline=pipeline)
    assert bridge.sync(tmp_path / "nope")["status"] == "failed"
    assert "invalid export path" in bridge.sync(7)["detail"]


def test_oversize_and_unreadable_pages_are_counted_not_dropped_silently(
    tmp_path, pipeline, monkeypatch,
):
    root = tmp_path / "export"
    root.mkdir()
    (root / "big.md").write_text("가" * 200, encoding="utf-8")
    (root / "ok.md").write_text("작은 문서입니다.", encoding="utf-8")

    tiny = NotionExportBridge(pipeline=pipeline, max_file_bytes=10)
    assert tiny.scan(root)["skipped"]["too_large"] == 2

    real_read = Path.read_text

    def _sometimes_broken(self, *args, **kwargs):
        if self.name == "ok.md":
            raise OSError("disk went away")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _sometimes_broken)
    report = NotionExportBridge(pipeline=pipeline).scan(root)
    assert report["skipped"]["unreadable"] == 1
    assert report["errors"][0]["status"] == "unreadable"


def test_a_huge_export_reports_that_it_was_truncated(tmp_path, pipeline):
    root = tmp_path / "export"
    root.mkdir()
    for index in range(3):
        (root / f"page-{index}.md").write_text(f"내용 {index}", encoding="utf-8")
    report = NotionExportBridge(pipeline=pipeline, max_items=2).scan(root)
    assert report["truncated"] is True and len(report["items"]) == 2


# ── the git bridge ───────────────────────────────────────────────────────────
def _git_output(commits: List[Dict[str, Any]]) -> str:
    parts = []
    for commit in commits:
        header = "\x1f".join([
            commit["sha"], commit["author"], commit["mail"],
            commit["date"], commit["subject"], commit["body"],
        ])
        parts.append(f"\x00{header}\x00" + "\n".join(commit["files"]))
    return "".join(parts)


def test_the_log_stream_parses_bodies_and_filenames_containing_newlines():
    output = _git_output([
        {
            "sha": "a" * 40, "author": "Alice", "mail": "a@x.test",
            "date": "2026-08-01T09:00:00+09:00", "subject": "fix: 검색 정확도",
            "body": "본문 첫 줄\n본문 둘째 줄", "files": ["a.py", "docs/b.md"],
        },
        {
            "sha": "b" * 40, "author": "Bob", "mail": "b@x.test",
            "date": "2026-08-02T09:00:00+09:00", "subject": "docs", "body": "",
            "files": [],
        },
    ])
    commits = parse_git_log(output)
    assert [c["sha"] for c in commits] == ["a" * 40, "b" * 40]
    assert commits[0]["files"] == ["a.py", "docs/b.md"]
    assert commits[0]["body"].splitlines() == ["본문 첫 줄", "본문 둘째 줄"]
    # A record that does not parse into six fields is skipped, never guessed at.
    assert parse_git_log("\x00only-one-field\x00") == []
    assert parse_git_log("") == []


def test_a_repository_becomes_commits_linked_to_the_files_they_touched(
    tmp_path, pipeline, store, monkeypatch,
):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    output = _git_output([{
        "sha": "c" * 40, "author": "Alice", "mail": "a@x.test",
        "date": "2026-08-01T09:00:00+09:00", "subject": "feat: 비디오 인제스트",
        "body": "키프레임과 자막을 함께 넣습니다.", "files": ["lattice_brain/multimodal.py"],
    }])
    monkeypatch.setattr(bridges, "_which_git", lambda: "/usr/bin/git")
    monkeypatch.setattr(bridges, "_run_git", lambda binary, args, cwd: (0, output))

    summary = GitHistoryBridge(pipeline=pipeline, knowledge_graph=store).sync(
        repo, workspace_id="ws-1", owner="me@local", max_commits=10,
    )
    assert summary["status"] == "ok" and summary["ingested"] == 1
    assert summary["edges"]["topics"] == 1

    with store._connect() as conn:
        topic = conn.execute(
            "SELECT title FROM nodes WHERE type='Topic'"
        ).fetchone()
        provenance = conn.execute(
            "SELECT source_type, source_uri FROM ingestion_provenance"
        ).fetchone()
    assert topic["title"] == "lattice_brain/multimodal.py"
    assert provenance["source_type"] == "git_commit"
    assert provenance["source_uri"].endswith("#" + "c" * 40)


def test_git_refuses_clearly_when_it_cannot_read_a_history(tmp_path, pipeline, monkeypatch):
    bridge = GitHistoryBridge(pipeline=pipeline)
    assert "invalid repository path" in bridge.sync(7)["detail"]
    assert "not a directory" in bridge.sync(tmp_path / "nope")["detail"]

    plain = tmp_path / "plain"
    plain.mkdir()
    assert "no .git" in bridge.sync(plain)["detail"]

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(bridges, "_which_git", lambda: None)
    assert bridge.sync(repo)["detail"] == GIT_UNAVAILABLE_DETAIL

    monkeypatch.setattr(bridges, "_which_git", lambda: "/usr/bin/git")
    monkeypatch.setattr(bridges, "_run_git", lambda *a: (128, ""))
    assert "status 128" in bridge.sync(repo)["detail"]

    def _boom(*_a):
        raise OSError("git vanished")

    monkeypatch.setattr(bridges, "_run_git", _boom)
    assert "git log failed" in bridge.sync(repo)["detail"]


def test_a_long_history_is_capped_and_says_so(tmp_path, pipeline, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    output = _git_output([
        {
            "sha": chr(97 + i) * 40, "author": "A", "mail": "a@x.test",
            "date": "2026-08-01T09:00:00+09:00", "subject": f"c{i}",
            "body": "", "files": [],
        }
        for i in range(3)
    ])
    monkeypatch.setattr(bridges, "_which_git", lambda: "/usr/bin/git")
    monkeypatch.setattr(bridges, "_run_git", lambda *a: (0, output))
    report = GitHistoryBridge(pipeline=pipeline, max_items=2).scan(repo)
    assert report["truncated"] is True and len(report["items"]) == 2


def test_the_git_argv_is_a_list_and_never_a_shell_string(tmp_path, monkeypatch):
    seen: Dict[str, Any] = {}

    class _Completed:
        returncode = 0
        stdout = ""

    def _fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return _Completed()

    monkeypatch.setattr(bridges.subprocess, "run", _fake_run)
    code, output = bridges._run_git("/usr/bin/git", ["log"], tmp_path)
    assert (code, output) == (0, "")
    assert seen["argv"] == ["/usr/bin/git", "log"]
    assert "shell" not in seen["kwargs"]


# ── mail and calendar ────────────────────────────────────────────────────────
def test_an_ics_file_yields_one_item_per_terminated_event():
    events = parse_ics(ICS)
    assert len(events) == 2  # the unterminated third block is dropped
    assert events[0]["SUMMARY"] == "릴리스 리뷰"
    assert events[0]["DTSTART"] == "20260804T090000"   # the TZID param is not the value
    assert events[0]["DESCRIPTION"].splitlines()[1] == "체크리스트 포함"
    assert "LOCATION" not in events[1]


def test_folded_lines_and_stray_text_outside_an_event_are_handled():
    folded = (
        "BEGIN:VCALENDAR\r\n"
        "X-WR-CALNAME:ignored\r\n"
        "BEGIN:VEVENT\r\n"
        "SUMMARY:아주 긴 제목이\r\n  이어집니다\r\n"
        "no-colon-line\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    events = parse_ics(folded)
    assert events == [{"SUMMARY": "아주 긴 제목이 이어집니다"}]


def test_email_bodies_report_what_they_could_not_read():
    class _NoBody:
        def get_body(self, preferencelist=()):
            return None if "plain" in preferencelist else object()

    class _Silent:
        def get_body(self, preferencelist=()):
            return None

    class _Raising:
        def get_body(self, preferencelist=()):
            raise ValueError("malformed")

    class _Undecodable:
        def get_body(self, preferencelist=()):
            return self

        def get_content(self):
            raise LookupError("unknown charset")

    class _Blank:
        def get_body(self, preferencelist=()):
            return self

        def get_content(self):
            return "   "

    assert email_body(_NoBody()) == ("", "html_only")
    assert email_body(_Silent()) == ("", "empty")
    assert email_body(_Raising()) == ("", "empty")
    assert email_body(_Undecodable())[1].startswith("undecodable")
    assert email_body(_Blank()) == ("", "empty")


def test_a_folder_of_messages_and_calendars_lands_through_the_one_gate(
    tmp_path, pipeline, store,
):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "plan.eml").write_bytes(EML)
    (inbox / "news.eml").write_bytes(HTML_ONLY)
    (inbox / "team.ics").write_text(ICS, encoding="utf-8")
    (inbox / "notes.txt").write_text("무시됨", encoding="utf-8")
    (inbox / ".hidden.eml").write_bytes(EML)

    bridge = MailCalendarBridge(pipeline=pipeline, knowledge_graph=store)
    summary = bridge.sync(inbox, workspace_id="ws-1", owner="me@local")
    assert summary["status"] == "ok"
    assert summary["scanned"] == 3
    assert summary["ingested"] == 4  # two messages + two calendar events
    assert summary["edges"]["topics"] == 1  # the located event only

    with store._connect() as conn:
        rows = {
            row["source_type"]
            for row in conn.execute("SELECT source_type FROM ingestion_provenance")
        }
    assert rows == {"email", "calendar_event"}


def test_a_single_file_is_accepted_and_anything_else_is_refused(tmp_path, pipeline):
    message = tmp_path / "one.eml"
    message.write_bytes(EML)
    bridge = MailCalendarBridge(pipeline=pipeline)
    assert bridge.sync(message)["ingested"] == 1

    other = tmp_path / "one.txt"
    other.write_text("nope", encoding="utf-8")
    assert "not an .eml or .ics file" in bridge.sync(other)["detail"]
    assert "no such file or folder" in bridge.sync(tmp_path / "gone")["detail"]
    assert "invalid path" in bridge.sync(7)["detail"]


def test_unreadable_oversize_and_unparseable_mail_are_each_their_own_state(
    tmp_path, pipeline, monkeypatch,
):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "plan.eml").write_bytes(EML)
    (inbox / "blank.ics").write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n", encoding="utf-8")

    tiny = MailCalendarBridge(pipeline=pipeline, max_file_bytes=10)
    assert tiny.scan(inbox)["skipped"]["too_large"] == 2

    # An .ics with no events is empty, not an error.
    assert MailCalendarBridge(pipeline=pipeline).scan(inbox)["skipped"]["empty"] == 1

    def _boom(self, *args, **kwargs):
        raise OSError("gone")

    monkeypatch.setattr(Path, "read_bytes", _boom)
    report = MailCalendarBridge(pipeline=pipeline).scan(inbox)
    assert report["skipped"]["unreadable"] == 2
    assert report["errors"][0]["status"] == "unreadable"


def test_a_message_that_will_not_parse_is_still_remembered(tmp_path, pipeline, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "broken.eml").write_bytes(EML)

    def _boom(*_a, **_k):
        raise ValueError("not a message")

    monkeypatch.setattr(bridges.email, "message_from_bytes", _boom)
    report = MailCalendarBridge(pipeline=pipeline).scan(inbox)
    item = report["items"][0].item
    assert item.metadata["body_status"] == "unreadable"
    assert item.metadata["searchable"] is False


def test_a_flood_of_messages_is_capped(tmp_path, pipeline):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for index in range(3):
        (inbox / f"m{index}.eml").write_bytes(EML)
    report = MailCalendarBridge(pipeline=pipeline, max_items=2).scan(inbox)
    assert report["truncated"] is True


# ── the shared skeleton ──────────────────────────────────────────────────────
def test_a_disabled_graph_refuses_every_bridge_the_same_way(store, tmp_path):
    disabled = IngestionPipeline(store, enable_graph=False)
    summary = NotionExportBridge(pipeline=disabled).sync(tmp_path)
    assert summary["status"] == "unavailable"
    assert "LATTICEAI_ENABLE_GRAPH" in summary["detail"]
    assert NotionExportBridge(pipeline=None).available() is False


def test_a_failing_ingest_is_counted_and_reported_per_item(tmp_path, pipeline, store):
    class _Failing:
        def available(self):
            return True

        def ingest(self, item, **_):
            from lattice_brain.ingestion import IngestionResult

            return IngestionResult(status="failed", source_type="notion", detail="nope")

    summary = NotionExportBridge(pipeline=_Failing()).sync(_notion_export(tmp_path / "e"))
    assert summary["status"] == "partial"
    assert summary["failed"] == 3
    assert summary["errors"][0]["detail"] == "nope"
    # Nothing landed, so there is nothing to relate.
    assert summary["edges"]["status"] == "none"


def test_without_a_store_the_items_still_land_and_the_edges_say_they_did_not(
    tmp_path, pipeline,
):
    summary = NotionExportBridge(pipeline=pipeline).sync(_notion_export(tmp_path / "e"))
    assert summary["ingested"] == 3
    assert summary["edges"]["status"] == "skipped"
    assert "without relations" in summary["edges"]["detail"]


def test_a_store_that_refuses_the_edges_never_loses_the_items(tmp_path, pipeline):
    class _Hostile:
        def import_graph_data(self, *_a, **_k):
            raise RuntimeError("read-only database")

    summary = NotionExportBridge(pipeline=pipeline, knowledge_graph=_Hostile()).sync(
        _notion_export(tmp_path / "e"),
    )
    assert summary["ingested"] == 3
    assert summary["status"] == "partial"
    assert "read-only database" in summary["edges"]["detail"]


def test_the_failure_list_is_a_capped_sample_and_the_count_stays_exact(
    tmp_path, pipeline,
):
    from lattice_brain.ingestion import IngestionResult

    class _AlwaysFails:
        def available(self):
            return True

        def ingest(self, item, **_):
            return IngestionResult(status="failed", source_type="notion", detail="nope")

    root = tmp_path / "export"
    root.mkdir()
    for index in range(30):
        (root / f"page-{index}.md").write_text(f"내용 {index}", encoding="utf-8")

    summary = NotionExportBridge(pipeline=_AlwaysFails()).sync(root)
    assert summary["failed"] == 30           # the count is exact…
    assert len(summary["errors"]) == 25      # …the report is a sample


def test_an_ingest_that_returns_no_node_id_still_counts_but_cannot_be_linked(
    tmp_path, pipeline,
):
    from lattice_brain.ingestion import IngestionResult

    class _Anonymous:
        def available(self):
            return True

        def ingest(self, item, **_):
            return IngestionResult(status="ok", source_type="notion", node_id=None)

    summary = NotionExportBridge(
        pipeline=_Anonymous(), knowledge_graph=object(),
    ).sync(_notion_export(tmp_path / "e"))
    assert summary["ingested"] == 3
    # Nothing has an id, so there is nothing to relate — and no edge is invented.
    assert summary["edges"]["status"] == "none"


def test_a_link_whose_target_failed_to_land_is_simply_not_written(tmp_path, store):
    """The page survives; the relation into a node that is not there does not."""
    from lattice_brain.ingestion import IngestionResult

    real = IngestionPipeline(store)

    class _DropsRetro:
        def available(self):
            return True

        def ingest(self, item, **kwargs):
            if "Retro" in str(item.source_uri):
                return IngestionResult(status="failed", source_type="notion", detail="no")
            return real.ingest(item, **kwargs)

    summary = NotionExportBridge(
        pipeline=_DropsRetro(), knowledge_graph=store,
    ).sync(_notion_export(tmp_path / "e"))
    assert summary["links"]["resolved"] == 1
    assert summary["links"]["written"] == 0
    assert summary["failed"] == 1


def test_a_stray_end_event_closes_nothing():
    assert parse_ics("END:VEVENT\nBEGIN:VEVENT\nSUMMARY:x\nEND:VEVENT") == [{"SUMMARY": "x"}]


def test_the_registry_names_what_exists_and_refuses_what_does_not(pipeline, monkeypatch):
    assert isinstance(build_bridge("notion", pipeline=pipeline), NotionExportBridge)
    assert isinstance(build_bridge(" GIT ", pipeline=pipeline), GitHistoryBridge)
    assert isinstance(build_bridge("mail", pipeline=pipeline), MailCalendarBridge)
    with pytest.raises(ValueError, match="unknown interop source"):
        build_bridge("dropbox", pipeline=pipeline)

    monkeypatch.setattr(bridges, "_which_git", lambda: None)
    status = bridge_status()
    assert status["sources"]["git"]["available"] is False
    assert status["sources"]["git"]["detail"] == GIT_UNAVAILABLE_DETAIL
    assert "out of scope" in status["sources"]["mail"]["detail"]

    monkeypatch.setattr(bridges, "_which_git", lambda: "/usr/bin/git")
    assert bridge_status()["sources"]["git"]["available"] is True


# ── the route ────────────────────────────────────────────────────────────────
def _client(tmp_path, pipeline, store):
    app = FastAPI()

    def _require_user(request: Request) -> str:
        return "me@local"

    permissions_router, gateway = create_permissions_router(
        config=SimpleNamespace(
            discord_permission_webhook="",
            discord_bot_token="",
            discord_permission_channel="",
            permission_monitor_secret="",
            port=4825,
        ),
        data_dir=tmp_path / "perm",
        require_user=_require_user,
        require_admin=_require_user,
        get_current_user=lambda request: "me@local",
    )
    app.include_router(permissions_router)
    app.include_router(create_local_files_router(
        require_user=_require_user,
        require_admin=_require_user,
        tool_response=lambda fn, *args: fn(*args),
        permission_gateway=gateway,
        knowledge_graph=store,
        require_graph=lambda: store,
        static_dir=tmp_path / "static",
        local_kg_watcher=None,
        ingestion_pipeline=pipeline,
        data_dir=tmp_path / "data",
    ))
    return TestClient(app), gateway


def _approve(gateway, client, path: str) -> str:
    """Walk the real approval dance: request a token, then approve it."""
    payload = gateway.local_permission_response(path, "read", "me@local")
    token = payload["approval_token"]
    approved = client.post(f"/permissions/approve/{token}")
    assert approved.status_code == 200, approved.text
    return token


def test_the_route_reports_status_and_ingests_an_approved_export(tmp_path, pipeline, store):
    client, gateway = _client(tmp_path, pipeline, store)
    root = _notion_export(tmp_path / "export")

    status = client.get("/api/ingestion/interop")
    assert status.status_code == 200
    assert set(status.json()["sources"]) == {"notion", "git", "mail"}

    # First call: the approval dance, exactly like every other local read.
    first = client.post(
        "/api/ingestion/interop", json={"source": "notion", "path": str(root)},
    )
    assert first.json()["permission_required"] is True

    token = _approve(gateway, client, str(root))
    done = client.post(
        "/api/ingestion/interop",
        json={
            "source": "notion", "path": str(root), "dry_run": True,
            "approved": True, "approval_token": token,
        },
    )
    assert done.status_code == 200
    assert done.json()["status"] == "dry_run" and done.json()["items"] == 3


def test_the_route_refuses_a_missing_path_or_an_unknown_source(tmp_path, pipeline, store):
    client, _ = _client(tmp_path, pipeline, store)
    blank = client.post("/api/ingestion/interop", json={"source": "notion", "path": "  "})
    assert blank.status_code == 400

    unknown = client.post(
        "/api/ingestion/interop", json={"source": "dropbox", "path": str(tmp_path)},
    )
    assert unknown.status_code == 400
    assert "dropbox" in unknown.json()["detail"]


def test_the_route_passes_the_commit_limit_through(tmp_path, pipeline, store, monkeypatch):
    client, gateway = _client(tmp_path, pipeline, store)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    seen: Dict[str, Any] = {}

    def _fake_run(binary, args, cwd):
        seen["args"] = args
        return 0, ""

    monkeypatch.setattr(bridges, "_which_git", lambda: "/usr/bin/git")
    monkeypatch.setattr(bridges, "_run_git", _fake_run)
    token = _approve(gateway, client, str(repo))
    response = client.post(
        "/api/ingestion/interop",
        json={
            "source": "git", "path": str(repo), "max_commits": 5,
            "approved": True, "approval_token": token,
        },
    )
    assert response.status_code == 200
    assert "--max-count=5" in seen["args"]


def test_the_route_needs_a_working_pipeline(tmp_path, store):
    client, _ = _client(tmp_path, IngestionPipeline(store, enable_graph=False), store)
    response = client.post(
        "/api/ingestion/interop", json={"source": "notion", "path": str(tmp_path)},
    )
    assert response.status_code == 503


def _unused(*_a: Any, **_k: Any) -> Optional[Any]:  # pragma: no cover - import guard
    return None
