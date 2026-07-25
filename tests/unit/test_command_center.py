"""Command Center tests (v9.5.0).

Covers the daily briefing (section independence, scoped reads, quick-action
derivation) and the universal search (grouping, conversation dedupe,
workspace scoping, graceful degradation without backends).
"""

from latticeai.services.command_center import CommandCenterService


class FakeConversations:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def history(self, **kwargs):
        self.calls.append(kwargs)
        limit = kwargs.get("limit")
        return self.items[:limit] if limit else self.items


class FakeGraph:
    def __init__(self, nodes=None):
        self.nodes = nodes or []
        self.calls = []

    def graph(self, limit=300, **kwargs):
        self.calls.append({"limit": limit, **kwargs})
        return {"nodes": self.nodes[:limit], "edges": []}


class FakeSearch:
    def __init__(self, results=None):
        self.results = results or []
        self.calls = []

    def keyword_search(self, query, **kwargs):
        self.calls.append({"query": query, **kwargs})
        return {"results": self.results}


class FakeStore:
    def __init__(self, workflows=None):
        self.workflows = workflows or []

    def list_workflows(self, **kwargs):
        return {"workflows": self.workflows}


class FakeReviewQueue:
    def __init__(self, items=None):
        self.items = items or []
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return {"items": self.items}


class FakeBrain:
    def __init__(self, score=90, grade="A"):
        self.score = score
        self.grade = grade

    def health_report(self, **kwargs):
        return {
            "overall": {"score": self.score, "grade": self.grade},
            "recommended_actions": ["review_orphans"],
        }


class FakeAutomation:
    def __init__(self, suggestions=None):
        self.items = suggestions or []

    def suggestions(self, **kwargs):
        return {"suggestions": self.items}


def _msg(content, *, role="user", ts="2026-07-19T09:00:00", conversation_id="c1"):
    return {
        "role": role,
        "content": content,
        "timestamp": ts,
        "conversation_id": conversation_id,
    }


def _node(node_id, title, ts="2026-07-19T08:00:00"):
    return {"id": node_id, "title": title, "type": "note", "updated_at": ts}


def _service(**kwargs):
    graph = kwargs.pop("graph", None)
    return CommandCenterService(
        conversation_store=kwargs.pop("conversations", None),
        knowledge_graph=graph,
        store=kwargs.pop("store", None),
        search_service=kwargs.pop("search", None),
        brain_intelligence=kwargs.pop("brain", None),
        automation_intelligence=kwargs.pop("automation", None),
        review_queue=kwargs.pop("review_queue", None),
        enable_graph=graph is not None,
    )


# ── briefing ────────────────────────────────────────────────────────────

def test_briefing_combines_all_sections():
    service = _service(
        conversations=FakeConversations([
            _msg("오늘 할 일 정리해줘", ts="2026-07-19T08:00:00"),
            _msg("네, 정리했어요", role="assistant", ts="2026-07-19T08:01:00"),
        ]),
        graph=FakeGraph([_node("n1", "회의록"), _node("n2", "계약서")]),
        store=FakeStore([
            {"name": "Daily digest", "metadata": {"automation_state": "enabled"}},
            {"name": "Weekly review", "metadata": {"automation_state": "draft_disabled"}},
        ]),
        review_queue=FakeReviewQueue([{"id": "r1"}]),
        brain=FakeBrain(score=88, grade="B"),
        automation=FakeAutomation([
            {"id": "sug-1", "kind": "recurring_question", "title": "오늘 할 일", "installed": False},
            {"id": "sug-2", "kind": "knowledge_source", "title": "폴더", "installed": True},
        ]),
    )
    briefing = service.briefing(user_email="a@b.c", workspace_id=None)
    sections = briefing["sections"]
    assert sections["knowledge"]["available"] is True
    assert sections["knowledge"]["recent"][0]["title"] == "회의록"
    assert sections["conversations"]["questions"] == 1
    assert sections["conversations"]["last_question"] == "오늘 할 일 정리해줘"
    assert sections["automations"] == {
        "available": True, "total": 2, "enabled": 1, "drafts": 1,
    }
    assert sections["review"]["pending"] == 1
    assert sections["health"]["grade"] == "B"
    # installed suggestions are excluded from the briefing count
    assert sections["suggestions"]["count"] == 1
    assert sections["suggestions"]["top"][0]["id"] == "sug-1"
    assert briefing["quick_actions"]
    assert briefing["generated_at"]


def test_briefing_quick_actions_follow_state():
    service = _service(
        store=FakeStore([
            {"name": "w", "metadata": {"automation_state": "draft_disabled"}},
        ]),
        review_queue=FakeReviewQueue([{"id": "r1"}, {"id": "r2"}]),
    )
    actions = service.briefing()["quick_actions"]
    ids = [action["id"] for action in actions]
    assert ids[0] == "review-pending"
    assert actions[0]["count"] == 2
    assert actions[0]["target"] == "/act/review"
    assert "enable-drafts" in ids


def test_briefing_defaults_to_ask_brain_when_nothing_pending():
    actions = _service().briefing()["quick_actions"]
    assert [action["id"] for action in actions] == ["ask-brain"]


def test_briefing_flags_low_health():
    service = _service(brain=FakeBrain(score=40, grade="D"))
    ids = [action["id"] for action in service.briefing()["quick_actions"]]
    assert "check-health" in ids


def test_briefing_degrades_without_backends():
    briefing = _service().briefing()
    sections = briefing["sections"]
    assert sections["knowledge"]["available"] is False
    assert sections["automations"]["available"] is False
    assert sections["review"]["available"] is False
    assert sections["health"]["available"] is False
    assert sections["suggestions"]["available"] is False


def test_briefing_uses_scoped_history_reads():
    conversations = FakeConversations([_msg("질문 하나 해줘")])
    service = _service(conversations=conversations)
    service.briefing(user_email="a@b.c", workspace_id="team-1")
    call = conversations.calls[0]
    assert call["user_email"] == "a@b.c"
    assert call["allowed_workspaces"] == {"team-1"}
    assert call["include_legacy_global"] is False


# ── hygiene advisory (review 2026-07-25 Wave 2.5) ───────────────────────


class FakeHygieneGraph:
    """Graph stub exposing only what the hygiene section reads."""

    def __init__(self, node_count=250, last=None, raise_stats=False):
        self.node_count = node_count
        self.last = last
        self.raise_stats = raise_stats

    def stats(self):
        if self.raise_stats:
            raise RuntimeError("graph unavailable")
        return {"nodes": {"Document": self.node_count // 2,
                          "Concept": self.node_count - self.node_count // 2}}

    def last_noise_curate_at(self):
        return self.last


def _days_ago(days):
    from datetime import datetime, timedelta

    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


def test_briefing_hygiene_absent_suggestion_without_graph():
    # No graph at all → section present, unavailable, never raises.
    briefing = _service().briefing()
    hygiene = briefing["sections"]["hygiene"]
    assert hygiene["available"] is False
    assert hygiene["suggest_noise_curate"] is False
    assert "curate-noise" not in [a["id"] for a in briefing["quick_actions"]]
    # A graph without stats()/last_noise_curate_at() degrades the same way.
    briefing = _service(graph=FakeGraph([_node("n1", "회의록")])).briefing()
    assert briefing["sections"]["hygiene"]["suggest_noise_curate"] is False


def test_briefing_hygiene_suggests_dry_run_curate_when_stale():
    service = _service(graph=FakeHygieneGraph(node_count=250, last=None))
    briefing = service.briefing()
    hygiene = briefing["sections"]["hygiene"]
    assert hygiene["available"] is True
    assert hygiene["node_count"] == 250
    assert hygiene["last_noise_curate_at"] is None
    assert hygiene["suggest_noise_curate"] is True
    assert hygiene["reason"]
    action = {a["id"]: a for a in briefing["quick_actions"]}["curate-noise"]
    assert action["kind"] == "hygiene"
    assert action["count"] == 250
    assert action["endpoint"] == "/knowledge-graph/curate/noise"


def test_briefing_hygiene_stale_timestamp_still_suggests():
    service = _service(graph=FakeHygieneGraph(node_count=300, last=_days_ago(8)))
    hygiene = service.briefing()["sections"]["hygiene"]
    assert hygiene["suggest_noise_curate"] is True


def test_briefing_hygiene_recent_curate_silences_suggestion():
    service = _service(graph=FakeHygieneGraph(node_count=300, last=_days_ago(1)))
    briefing = service.briefing()
    hygiene = briefing["sections"]["hygiene"]
    assert hygiene["available"] is True
    assert hygiene["suggest_noise_curate"] is False
    assert "curate-noise" not in [a["id"] for a in briefing["quick_actions"]]


def test_briefing_hygiene_small_graph_never_suggests():
    service = _service(graph=FakeHygieneGraph(node_count=50, last=None))
    hygiene = service.briefing()["sections"]["hygiene"]
    assert hygiene["available"] is True
    assert hygiene["suggest_noise_curate"] is False


def test_briefing_hygiene_backend_error_degrades_not_raises():
    service = _service(graph=FakeHygieneGraph(raise_stats=True))
    briefing = service.briefing()  # must not raise
    hygiene = briefing["sections"]["hygiene"]
    assert hygiene["available"] is False
    assert hygiene["suggest_noise_curate"] is False


# ── universal search ────────────────────────────────────────────────────

def test_search_groups_knowledge_conversations_and_automations():
    service = _service(
        conversations=FakeConversations([
            _msg("계약서 검토해줘", conversation_id="c1"),
            _msg("계약서 조항 질문", conversation_id="c2", ts="2026-07-19T10:00:00"),
        ]),
        graph=FakeGraph(),
        search=FakeSearch([
            {"id": "n1", "title": "계약서 초안", "summary": "요약", "type": "document"},
        ]),
        store=FakeStore([
            {"id": "w1", "name": "계약서 digest", "metadata": {"automation_state": "enabled"}},
            {"id": "w2", "name": "unrelated", "metadata": {}},
        ]),
    )
    result = service.search("계약서", user_email="a@b.c")
    kinds = {group["kind"]: group["items"] for group in result["groups"]}
    assert kinds["knowledge"][0]["id"] == "n1"
    assert len(kinds["conversation"]) == 2
    assert kinds["automation"][0]["id"] == "w1"
    assert kinds["automation"][0]["enabled"] is True
    assert result["total"] == 4


def test_search_dedupes_conversations_and_prefers_recent():
    service = _service(
        conversations=FakeConversations([
            _msg("계약서 첫 질문", conversation_id="c1", ts="2026-07-18T09:00:00"),
            _msg("계약서 두번째 질문", conversation_id="c1", ts="2026-07-19T09:00:00"),
        ]),
    )
    result = service.search("계약서")
    items = result["groups"][0]["items"]
    assert len(items) == 1
    assert items[0]["snippet"] == "계약서 두번째 질문"


def test_search_scopes_knowledge_to_workspace():
    search = FakeSearch()
    service = _service(graph=FakeGraph(), search=search)
    service.search("meeting", workspace_id="team-1")
    call = search.calls[0]
    assert call["allowed_workspaces"] == {"team-1"}
    assert call["include_legacy_global"] is False


def test_search_empty_query_returns_no_groups():
    result = _service().search("   ")
    assert result["groups"] == []
    assert result["query"] == ""


def test_search_degrades_without_backends():
    result = _service().search("anything")
    assert result["groups"] == []
    assert result["total"] == 0
