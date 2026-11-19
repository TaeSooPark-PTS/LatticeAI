use super::*;
use serde_json::json;

fn conversations() -> (tempfile::TempDir, Connection) {
    let dir = tempfile::tempdir().unwrap();
    let conn = Connection::open(dir.path().join("c.sqlite")).unwrap();
    conn.execute_batch(
        "CREATE TABLE conversation_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,
           conversation_id TEXT, role TEXT, content TEXT, user_email TEXT,
           user_nickname TEXT, source TEXT, timestamp TEXT,
           metadata_json TEXT NOT NULL DEFAULT '{}', workspace_id TEXT,
           organization_id TEXT);
         INSERT INTO conversation_messages
           (conversation_id, role, content, user_email, user_nickname, source,
            timestamp, metadata_json, workspace_id, organization_id) VALUES
           ('c1','user','  Hello   there  ','a@x','A','web','2026-01-01T00:00:00',
            '{\"trace\":\"t\"}','w1','o1'),
           ('c1','assistant','Hi','a@x','','', '2026-01-01T00:00:01','{}','w1',NULL),
           (NULL,'user','legacy line',NULL,NULL,NULL,'','{}','',NULL),
           ('c2','user','다른 사람 메시지','b@x','B','web','2026-01-02T00:00:00',
            '{}',NULL,NULL);",
    )
    .unwrap();
    (dir, conn)
}

#[test]
fn rows_keep_only_truthy_optional_columns_and_merge_metadata_flat() {
    let (_dir, conn) = conversations();
    let rows = history(&conn, None, None, &HistoryScope::owner()).unwrap();
    assert_eq!(rows.len(), 4);
    assert_eq!(rows[0]["trace"], "t", "metadata merges flat into the item");
    assert_eq!(rows[0]["user_nickname"], "A");
    assert!(
        rows[1].get("user_nickname").is_none(),
        "an empty string is dropped, not carried as \"\""
    );
    assert!(rows[1].get("source").is_none());
    assert!(rows[1].get("organization_id").is_none());
    assert!(rows[2].get("conversation_id").is_none());
    assert_eq!(rows[2]["timestamp"], "");
    assert!(rows[2].get("workspace_id").is_none(), "'' is falsy");
}

#[test]
fn the_scope_sql_covers_every_branch() {
    let (_dir, conn) = conversations();
    let scoped = |scope: HistoryScope| history(&conn, None, None, &scope).unwrap().len();
    assert_eq!(scoped(HistoryScope::default()), 4, "no scope reads all");
    assert_eq!(
        scoped(HistoryScope {
            user_email: Some("a@x".into()),
            include_legacy_global: true,
            ..Default::default()
        }),
        3,
        "legacy-global admits the ownerless row"
    );
    assert_eq!(
        scoped(HistoryScope {
            user_email: Some("a@x".into()),
            ..Default::default()
        }),
        2
    );
    assert_eq!(
        scoped(HistoryScope {
            allowed_workspaces: Some(vec!["w1".into()]),
            include_legacy_global: true,
            ..Default::default()
        }),
        4
    );
    assert_eq!(
        scoped(HistoryScope {
            allowed_workspaces: Some(vec!["w1".into()]),
            ..Default::default()
        }),
        2
    );
    assert_eq!(
        scoped(HistoryScope {
            allowed_workspaces: Some(vec![]),
            include_legacy_global: true,
            ..Default::default()
        }),
        2,
        "only the unattributed rows"
    );
    assert_eq!(
        scoped(HistoryScope {
            allowed_workspaces: Some(vec![]),
            ..Default::default()
        }),
        0,
        "1=0 — a caller who may read nothing reads nothing"
    );
    // Blank entries are stripped before the IN list, so [\"\"] is an empty set.
    assert_eq!(
        scoped(HistoryScope {
            allowed_workspaces: Some(vec![String::new()]),
            ..Default::default()
        }),
        0
    );
    // NULL/blank workspace_id is personal visibility.
    assert_eq!(
        scoped(HistoryScope {
            allowed_workspaces: Some(vec![lattice_core::DEFAULT_WORKSPACE_ID.into()]),
            ..Default::default()
        }),
        2,
        "personal matches the NULL-workspace row and the blank one"
    );
    assert_eq!(
        scoped(HistoryScope {
            allowed_workspaces: Some(vec!["team".into()]),
            ..Default::default()
        }),
        0,
        "a named workspace does not leak unstamped rows"
    );
}

#[test]
fn a_missing_conversation_table_is_an_empty_history() {
    let dir = tempfile::tempdir().unwrap();
    let conn = Connection::open(dir.path().join("empty.sqlite")).unwrap();
    let rows = history(&conn, None, None, &HistoryScope::owner()).unwrap();
    assert!(rows.is_empty());
}

#[test]
fn conversation_and_limit_filters_are_three_valued() {
    let (_dir, conn) = conversations();
    let scope = HistoryScope::owner();
    assert_eq!(history(&conn, Some("c1"), None, &scope).unwrap().len(), 2);
    assert_eq!(
        history(&conn, Some(""), None, &scope).unwrap().len(),
        1,
        "the empty string means IS NULL"
    );
    assert!(history(&conn, Some("nope"), None, &scope)
        .unwrap()
        .is_empty());
    assert_eq!(history(&conn, None, Some(2), &scope).unwrap().len(), 2);
    assert_eq!(
        history(&conn, None, Some(0), &scope).unwrap().len(),
        1,
        "a zero limit clamps to one row, never to none"
    );
}

#[test]
fn titles_collapse_truncate_and_fall_back() {
    assert_eq!(conversation_title(&json!({"content": " a \n b "})), "a b");
    assert_eq!(conversation_title(&json!({"content": "   "})), "새 대화");
    assert_eq!(conversation_title(&json!({})), "새 대화");
    let long = "가".repeat(60);
    assert_eq!(
        conversation_title(&json!({"content": long}))
            .chars()
            .count(),
        48
    );
}

#[test]
fn grouping_names_upgrades_and_orders_conversations() {
    let history = vec![
        json!({"conversation_id": "c1", "role": "user", "content": "   ",
               "timestamp": "2026-01-01T00:00:00", "source": "web"}),
        json!({"conversation_id": "c1", "role": "assistant", "content": "hi",
               "timestamp": "2026-01-01T00:00:01"}),
        json!({"conversation_id": "c1", "role": "user", "content": "real title",
               "timestamp": "2026-01-01T00:00:02"}),
        json!({"role": "user", "content": "old", "timestamp": "2026-02-02T00:00:00"}),
        json!({"role": "user", "content": "older", "timestamp": ""}),
    ];
    let groups = group_conversations(&history);
    assert_eq!(groups.len(), 2);
    // The legacy bucket is newest, and keeps its fixed name.
    assert_eq!(groups[0]["id"], LEGACY_CONVERSATION_ID);
    assert_eq!(groups[0]["title"], LEGACY_CONVERSATION_TITLE);
    assert_eq!(groups[0]["message_count"], 2);
    assert_eq!(
        groups[0]["updated_at"], "2026-02-02T00:00:00",
        "an empty timestamp never overwrites a real one"
    );
    assert_eq!(
        groups[1]["title"], "real title",
        "a user message renames it"
    );
    assert_eq!(
        groups[1]["source"], "web",
        "the first row decides the source"
    );
    assert_eq!(groups[1]["created_at"], "2026-01-01T00:00:00");
    assert_eq!(groups[1]["last_message"], "real title");
    assert!(group_conversations(&[]).is_empty());
}

#[test]
fn an_assistant_first_conversation_keeps_its_name() {
    let history = vec![
        json!({"conversation_id": "c", "role": "assistant", "content": "opening",
               "timestamp": "2026-01-01T00:00:00"}),
        json!({"conversation_id": "c", "role": "user", "content": "later",
               "timestamp": "2026-01-01T00:00:01"}),
    ];
    assert_eq!(group_conversations(&history)[0]["title"], "opening");
}

#[test]
fn conversation_messages_filter_both_buckets() {
    let history = vec![
        json!({"conversation_id": "c1", "content": "a"}),
        json!({"content": "b"}),
        json!({"conversation_id": "", "content": "c"}),
    ];
    assert_eq!(conversation_messages(&history, "c1").len(), 1);
    assert_eq!(
        conversation_messages(&history, LEGACY_CONVERSATION_ID).len(),
        2,
        "NULL and empty both land in the legacy bucket"
    );
    assert!(conversation_messages(&history, "nope").is_empty());
}

#[test]
fn search_is_case_insensitive_and_keeps_the_last_groups() {
    let history: Vec<Value> = (0..4)
        .map(|index| {
            json!({"conversation_id": format!("c{index}"), "content": "Ranking",
                            "role": "user"})
        })
        .collect();
    assert!(search_history(&history, "   ", 30).is_empty());
    assert!(search_history(&history, "zz", 30).is_empty());
    let all = search_history(&history, "RANKING", 30);
    assert_eq!(all.len(), 4);
    assert_eq!(all[0]["conversation_id"], "c0");
    assert_eq!(all[0]["title"], "Ranking");
    assert_eq!(all[0]["messages"].as_array().unwrap().len(), 1);
    // The LAST groups in insertion order, which is what the product returns.
    let tail = search_history(&history, "ranking", 2);
    assert_eq!(tail.len(), 2);
    assert_eq!(tail[0]["conversation_id"], "c2");
    assert_eq!(search_history(&history, "ranking", 0).len(), 1);
    let legacy = search_history(&[json!({"content": "ranking"})], "ranking", 30);
    assert_eq!(legacy[0]["conversation_id"], "legacy");
}

#[test]
fn pairing_never_leaks_another_users_reply() {
    let history = vec![
        json!({"role": "user", "content": "mine", "user_email": "a@x"}),
        json!({"role": "assistant", "content": "theirs", "user_email": "b@x"}),
        json!({"role": "assistant", "content": "mine too", "user_email": "a@x"}),
        json!({"role": "user", "content": "not mine", "user_email": "b@x"}),
        json!({"role": "assistant", "content": "dropped", "user_email": "a@x"}),
        json!({"role": "user", "content": "mine again", "user_email": "a@x"}),
        json!({"role": "assistant", "content": "ownerless"}),
    ];
    let paired = pair_user_history(&history, "a@x");
    let contents: Vec<&str> = paired
        .iter()
        .map(|item| item["content"].as_str().unwrap())
        .collect();
    assert_eq!(contents, ["mine", "mine too", "mine again", "ownerless"]);
}

#[test]
fn recent_chat_context_labels_and_trims() {
    let history = vec![
        json!({"role": "user", "content": "one", "source": "web", "workspace_id": "w1"}),
        json!({"role": "assistant", "content": "two"}),
        json!({"role": "user", "content": "three", "conversation_id": "c"}),
    ];
    let all = recent_chat_context(&history, &RecentChatOptions::default());
    assert_eq!(all, "user (web): one\nassistant: two\nuser: three");
    let trimmed = recent_chat_context(
        &history,
        &RecentChatOptions {
            limit: 1,
            ..Default::default()
        },
    );
    assert_eq!(trimmed, "user: three");
    let scoped = recent_chat_context(
        &history,
        &RecentChatOptions {
            workspace_id: Some("personal".into()),
            ..Default::default()
        },
    );
    assert_eq!(
        scoped, "assistant: two\nuser: three",
        "a row with no workspace belongs to \"personal\""
    );
    let conversation = recent_chat_context(
        &history,
        &RecentChatOptions {
            conversation_id: Some("c".into()),
            ..Default::default()
        },
    );
    assert_eq!(conversation, "user: three");
    // `history[-0:]` is `history[0:]`: a zero limit keeps everything.
    // 11.5.2 found this with a golden; the port had answered "" and no
    // test on either side had ever asked.
    assert_eq!(
        recent_chat_context(
            &history,
            &RecentChatOptions {
                limit: 0,
                ..Default::default()
            }
        ),
        "user (web): one\nassistant: two\nuser: three"
    );
    // `history[-(-2):]` is `history[2:]` — a negative limit drops rows
    // off the *front*.
    assert_eq!(
        recent_chat_context(
            &history,
            &RecentChatOptions {
                limit: -2,
                ..Default::default()
            }
        ),
        "user: three"
    );
}

#[test]
fn image_missing_replies_can_be_dropped() {
    let history = vec![
        json!({"role": "assistant", "content": "이미지를 업로드해 주세요"}),
        json!({"role": "assistant", "content": "이미지 설명입니다"}),
        json!({"role": "user", "content": "이미지 업로드"}),
    ];
    let kept = recent_chat_context(&history, &RecentChatOptions::default());
    assert_eq!(kept.lines().count(), 3);
    let filtered = recent_chat_context(
        &history,
        &RecentChatOptions {
            include_image_missing_replies: false,
            ..Default::default()
        },
    );
    assert_eq!(filtered.lines().count(), 2);
    assert!(!filtered.contains("업로드해"));
    assert!(format!("{:?}", RecentChatOptions::default()).contains("limit"));
    assert_eq!(HistoryScope::default(), HistoryScope::default().clone());
}
