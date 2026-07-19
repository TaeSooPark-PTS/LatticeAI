"""Question-driven everyday automation tests (v9.4.0).

Covers pattern mining over conversation history, suggestion generation from
recurring questions and connected knowledge folders, deterministic ids /
idempotent installs, and the consent-first workflow definitions.
"""

from latticeai.services.automation_intelligence import AutomationIntelligenceService


class FakeConversations:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def history(self, **kwargs):
        self.calls.append(kwargs)
        return self.items


class FakeGraph:
    def __init__(self, sources=None):
        self.sources = sources or []

    def local_sources(self):
        return {"sources": self.sources}


class FakeStore:
    def __init__(self, workflows=None):
        self.workflows = workflows or []

    def list_workflows(self, **kwargs):
        return {"workflows": self.workflows}


def _msg(content, *, role="user", ts="2026-07-19T09:00:00"):
    return {"role": role, "content": content, "timestamp": ts}


def _service(conversations=None, graph=None, store=None):
    return AutomationIntelligenceService(
        conversation_store=conversations,
        knowledge_graph=graph,
        store=store or FakeStore(),
        enable_graph=graph is not None,
    )


# ── pattern mining ──────────────────────────────────────────────────────

def test_recurring_questions_cluster_into_patterns():
    conversations = FakeConversations([
        _msg("오늘 할 일 정리해줘", ts="2026-07-17T08:00:00"),
        _msg("오늘 할 일 좀 정리해줘", ts="2026-07-18T08:10:00"),
        _msg("오늘 할 일 정리해줘", ts="2026-07-19T08:05:00"),
        _msg("완전히 다른 일회성 질문이야 어때?", ts="2026-07-19T09:00:00"),
        _msg("assistant reply", role="assistant"),
    ])
    report = _service(conversations).question_patterns()
    assert report["questions_scanned"] == 4
    assert len(report["patterns"]) == 1
    pattern = report["patterns"][0]
    assert pattern["count"] == 3
    assert pattern["last_asked"] == "2026-07-19T08:05:00"
    assert "오늘 할 일" in pattern["representative"]
    assert len(pattern["examples"]) >= 2


def test_intent_rules_map_patterns_to_recipes():
    conversations = FakeConversations([
        _msg("이번 주 프로젝트 진행 상태 알려줘", ts="2026-07-18T10:00:00"),
        _msg("프로젝트 진행 상태 알려줘 이번 주 기준으로", ts="2026-07-19T10:00:00"),
    ])
    report = _service(conversations).question_patterns()
    assert report["patterns"][0]["intent"] == "project_review"
    assert report["patterns"][0]["recipe_id"] == "weekly-project-review"


def test_single_ask_and_commands_are_ignored():
    conversations = FakeConversations([
        _msg("한 번만 묻는 질문이야 어때?"),
        _msg("/clear"),
        _msg("hi"),
    ])
    report = _service(conversations).question_patterns()
    assert report["patterns"] == []


def test_patterns_scope_history_reads():
    conversations = FakeConversations([])
    _service(conversations).question_patterns(user_email="a@b.c", workspace_id="team-1")
    call = conversations.calls[0]
    assert call["user_email"] == "a@b.c"
    assert call["allowed_workspaces"] == {"team-1"}
    assert call["include_legacy_global"] is False


# ── suggestions ─────────────────────────────────────────────────────────

def test_suggestions_from_questions_and_sources_with_stable_ids():
    conversations = FakeConversations([
        _msg("오늘 정리해줘 기억", ts="2026-07-18T08:00:00"),
        _msg("오늘 기억 정리해줘", ts="2026-07-19T08:00:00"),
    ])
    graph = FakeGraph([
        {"id": "src-1", "root_path": "/Users/me/Docs", "label": "내 문서",
         "watch_enabled": True, "file_status": {"indexed": 42}},
        {"id": "src-empty", "root_path": "/tmp/empty", "label": "빈 폴더",
         "watch_enabled": False, "file_status": {}},
    ])
    service = _service(conversations, graph)
    first = service.suggestions()
    second = service.suggestions()
    assert [s["id"] for s in first["suggestions"]] == [s["id"] for s in second["suggestions"]]
    kinds = {s["kind"] for s in first["suggestions"]}
    assert kinds == {"recurring_question", "knowledge_source"}
    source_sug = next(s for s in first["suggestions"] if s["kind"] == "knowledge_source")
    assert source_sug["reason"]["indexed_files"] == 42
    assert source_sug["title"] == "내 문서"
    # Empty folders produce no suggestion.
    assert all("빈 폴더" not in s["title"] for s in first["suggestions"])
    question_sug = next(s for s in first["suggestions"] if s["kind"] == "recurring_question")
    assert question_sug["reason"]["count"] == 2
    assert question_sug["reason"]["examples"]


def test_installed_suggestions_are_marked():
    conversations = FakeConversations([
        _msg("오늘 기억 정리해줘", ts="2026-07-18T08:00:00"),
        _msg("오늘 기억 정리해줘 부탁", ts="2026-07-19T08:00:00"),
    ])
    service = _service(conversations)
    suggestion_id = service.suggestions()["suggestions"][0]["id"]
    store = FakeStore([
        {"id": "wf-1", "metadata": {"created_from": "automation_suggestion", "suggestion_id": suggestion_id}},
    ])
    service = _service(conversations, store=store)
    suggestion = service.suggestions()["suggestions"][0]
    assert suggestion["installed"] is True
    assert suggestion["workflow_id"] == "wf-1"


# ── workflow building ───────────────────────────────────────────────────

def test_question_suggestion_builds_consent_first_scheduled_workflow():
    service = _service(FakeConversations([]))
    suggestion = {
        "id": "sug-q-abc", "kind": "recurring_question",
        "title": "오늘 할 일 정리해줘", "reason": {"count": 3},
    }
    definition = service.build_suggestion_workflow(suggestion)
    trigger = definition["nodes"][0]["config"]
    assert trigger["trigger"] == "interval"
    assert trigger["enabled"] is False
    assert trigger["review_queue"] is True and trigger["consent_required"] is True
    assert "오늘 할 일 정리해줘" in definition["nodes"][1]["config"]["prompt"]
    meta = definition["metadata"]
    assert meta["created_from"] == "automation_suggestion"
    assert meta["suggestion_id"] == "sug-q-abc"
    assert meta["automation_state"] == "draft_disabled"
    assert meta["requires_user_enable"] is True


def test_source_suggestion_builds_brain_event_workflow():
    service = _service(FakeConversations([]))
    suggestion = {
        "id": "sug-src-xyz", "kind": "knowledge_source",
        "title": "내 문서", "reason": {"root_path": "/Users/me/Docs"},
    }
    definition = service.build_suggestion_workflow(suggestion, enabled=True)
    trigger = definition["nodes"][0]["config"]
    assert trigger["trigger"] == "brain_event"
    assert trigger["enabled"] is True
    assert "/Users/me/Docs" in definition["nodes"][1]["config"]["prompt"]
    assert definition["metadata"]["automation_state"] == "enabled"


# ── overview ────────────────────────────────────────────────────────────

def test_overview_combines_suggestions_and_installed_automations():
    conversations = FakeConversations([
        _msg("오늘 기억 정리해줘", ts="2026-07-18T08:00:00"),
        _msg("오늘 기억 정리해줘 부탁", ts="2026-07-19T08:00:00"),
    ])
    store = FakeStore([
        {"id": "wf-1", "name": "Daily Memory Digest",
         "metadata": {"created_from": "brain_automation_recipe", "recipe_id": "daily-memory-digest",
                      "automation_state": "enabled", "requires_user_enable": False, "creates": ["digest"]}},
        {"id": "wf-2", "name": "Unrelated manual workflow", "metadata": {}},
    ])
    overview = _service(conversations, store=store).overview()
    assert len(overview["suggestions"]) == 1
    assert len(overview["installed"]) == 1
    installed = overview["installed"][0]
    assert installed["recipe_id"] == "daily-memory-digest"
    assert installed["enabled"] is True
    assert overview["consent"]["requires_user_enable"] is True


def test_degrades_without_backends():
    service = AutomationIntelligenceService()
    assert service.question_patterns()["patterns"] == []
    report = service.suggestions()
    assert report["suggestions"] == []
    assert service.overview()["installed"] == []
