use std::collections::BTreeSet;
use std::sync::Arc;

use super::*;
use crate::db::Store;
use crate::graph_write::types::{
    ChunkPiece, ConceptSpec, IngestContentRequest, IngestMessageRequest, SemanticSpec,
};
use crate::graph_write::GraphWriter;
use crate::read::{filter_scoped_nodes, workspace_membership_sql, DEFAULT_WORKSPACE_ID};

const ALICE: &str = "alice@acme.test";
const BOB: &str = "bob@acme.test";

struct Fixture {
    _dir: tempfile::TempDir,
    store: Arc<Store>,
    message: String,
    document: String,
    legacy: String,
}

fn turn(content: &str, user: Option<&str>, conversation: &str) -> IngestMessageRequest {
    IngestMessageRequest {
        role: "user".into(),
        content: content.into(),
        user_email: user.map(str::to_string),
        source: Some("web".into()),
        conversation_id: Some(conversation.into()),
        workspace_id: Some(DEFAULT_WORKSPACE_ID.into()),
        chunks: vec![ChunkPiece {
            text: content.into(),
            ..Default::default()
        }],
        concepts: vec![ConceptSpec {
            text: "Zebra".into(),
            node_type: "Concept".into(),
        }],
        semantic: vec![SemanticSpec {
            item_type: "Decision".into(),
            title: format!("decided {content}"),
            summary: content.into(),
            raw: Default::default(),
        }],
        ..Default::default()
    }
}

fn fixture() -> Fixture {
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(Store::open(&dir.path().join("kg.sqlite")).unwrap());
    let writer = GraphWriter::open(Arc::clone(&store), dir.path().join("blobs")).unwrap();
    let message = writer
        .ingest_message(&turn("alice private zebraquartz", Some(ALICE), "conv-a"))
        .unwrap()
        .node_id;
    let legacy = writer
        .ingest_message(&turn("ownerless legacy turn", None, "conv-legacy"))
        .unwrap()
        .node_id;
    let document = writer
        .ingest_content(&IngestContentRequest {
            source_type: "upload".into(),
            title: "Team doc".into(),
            text: "alice shared zebraquartz document".into(),
            owner: Some(ALICE.into()),
            workspace_id: Some(DEFAULT_WORKSPACE_ID.into()),
            chunks: vec![ChunkPiece {
                text: "alice shared zebraquartz document".into(),
                ..Default::default()
            }],
            ..Default::default()
        })
        .unwrap()
        .node_id;
    Fixture {
        _dir: dir,
        store,
        message,
        document,
        legacy,
    }
}

fn all_ids(fixture: &Fixture) -> Vec<(String, String, Option<String>)> {
    let conn = crate::db::open_read_only(fixture.store.path()).unwrap();
    let mut stmt = conn
        .prepare("SELECT id, COALESCE(NULLIF(legacy_type, ''), type), owner_id FROM nodes_v2 ORDER BY id")
        .unwrap();
    let rows = stmt
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))
        .unwrap();
    rows.map(Result::unwrap).collect()
}

fn personal(viewer: Option<&str>) -> BTreeSet<String> {
    with_viewer(BTreeSet::from([DEFAULT_WORKSPACE_ID.to_string()]), viewer)
}

#[test]
fn another_users_conversation_nodes_are_hidden_and_shared_content_is_not() {
    let fixture = fixture();
    let conn = crate::db::open_read_only(fixture.store.path()).unwrap();
    let rows = all_ids(&fixture);
    let ids: Vec<String> = rows.iter().map(|(id, _, _)| id.clone()).collect();
    let hidden = foreign_conversation_ids(&conn, &ids, BOB).unwrap();
    for (id, node_type, owner) in &rows {
        let alice_owned = owner.as_deref() == Some(ALICE);
        let conversational = matches!(
            node_type.as_str(),
            "Chat" | "Message" | "Decision" | "Person"
        );
        if alice_owned && conversational {
            assert!(
                hidden.contains(id),
                "{node_type} {id} must be hidden from bob"
            );
        }
        if !alice_owned {
            assert!(
                !hidden.contains(id),
                "ownerless {node_type} {id} stays visible"
            );
        }
        if node_type == "Document" || node_type == "Concept" {
            assert!(!hidden.contains(id), "{node_type} stays workspace scoped");
        }
    }
    assert!(hidden.contains(&fixture.message));
    assert!(!hidden.contains(&fixture.document));
    assert!(!hidden.contains(&fixture.legacy));
    let message_chunks: Vec<String> = conn
        .prepare("SELECT id FROM chunks WHERE source_node = ?")
        .unwrap()
        .query_map([&fixture.message], |row| row.get(0))
        .unwrap()
        .map(Result::unwrap)
        .collect();
    assert!(!message_chunks.is_empty());
    for chunk in &message_chunks {
        assert!(
            hidden.contains(chunk),
            "message chunk {chunk} must be hidden"
        );
    }
    let document_chunks: Vec<String> = conn
        .prepare("SELECT id FROM chunks WHERE source_node = ?")
        .unwrap()
        .query_map([&fixture.document], |row| row.get(0))
        .unwrap()
        .map(Result::unwrap)
        .collect();
    for chunk in &document_chunks {
        assert!(
            !hidden.contains(chunk),
            "document chunk {chunk} stays visible"
        );
    }
    assert!(foreign_conversation_ids(&conn, &ids, ALICE)
        .unwrap()
        .is_empty());
    assert!(
        foreign_conversation_ids(&conn, &ids, &normalize_viewer(" Alice@ACME.test ").unwrap())
            .unwrap()
            .is_empty()
    );
}

#[test]
fn the_shared_filters_apply_the_viewer_and_leave_unviewed_scopes_alone() {
    let fixture = fixture();
    let conn = crate::db::open_read_only(fixture.store.path()).unwrap();
    let ids: Vec<String> = all_ids(&fixture).into_iter().map(|(id, _, _)| id).collect();
    let id_of = |id: &String| id.clone();

    let unviewed = personal(None);
    let everyone = filter_scoped_nodes(&conn, ids.clone(), Some(&unviewed), false, id_of).unwrap();
    assert!(
        everyone.contains(&fixture.message),
        "no viewer keeps the old behaviour"
    );

    let bob = personal(Some(BOB));
    let bob_sees = filter_scoped_nodes(&conn, ids.clone(), Some(&bob), false, id_of).unwrap();
    assert!(!bob_sees.contains(&fixture.message));
    assert!(bob_sees.contains(&fixture.document));
    assert!(bob_sees.contains(&fixture.legacy));

    let alice = personal(Some(ALICE));
    let alice_sees = filter_scoped_nodes(&conn, ids.clone(), Some(&alice), false, id_of).unwrap();
    assert_eq!(alice_sees.len(), ids.len());

    assert_eq!(
        filter_scoped_nodes(&conn, ids.clone(), None, false, id_of)
            .unwrap()
            .len(),
        ids.len(),
        "an unscoped read stays unscoped"
    );

    let (predicate, params) = workspace_membership_sql(Some(&bob), false).unwrap();
    assert_eq!(params.last().map(String::as_str), Some(BOB));
    let sql = format!("SELECT id FROM nodes_v2 WHERE {predicate}");
    let visible: Vec<String> = conn
        .prepare(&sql)
        .unwrap()
        .query_map(rusqlite::params_from_iter(params.iter()), |row| row.get(0))
        .unwrap()
        .map(Result::unwrap)
        .collect();
    assert!(!visible.contains(&fixture.message));
    assert!(visible.contains(&fixture.document));
    assert_eq!(visible.len(), bob_sees.len());

    let (plain, plain_params) = workspace_membership_sql(Some(&unviewed), false).unwrap();
    assert!(!plain.contains("owner_id"), "{plain}");
    assert_eq!(plain_params, vec![DEFAULT_WORKSPACE_ID.to_string()]);
}

#[test]
fn the_viewer_marker_is_never_a_workspace_and_cannot_be_forged() {
    let forged = BTreeSet::from([
        DEFAULT_WORKSPACE_ID.to_string(),
        format!("{VIEWER_SCOPE_PREFIX}{ALICE}"),
    ]);
    let scoped = with_viewer(forged.clone(), Some(BOB));
    assert_eq!(scope_viewer(Some(&scoped)), Some(BOB));
    assert_eq!(
        workspace_entries(&scoped).cloned().collect::<Vec<_>>(),
        vec![DEFAULT_WORKSPACE_ID.to_string()]
    );
    assert_eq!(scope_viewer(Some(&with_viewer(forged.clone(), None))), None);

    let mut two = scoped.clone();
    two.insert(format!("{VIEWER_SCOPE_PREFIX}{ALICE}"));
    assert_eq!(
        scope_viewer(Some(&two)),
        Some(""),
        "ambiguous viewers fail closed"
    );
    assert_eq!(scope_viewer(None), None);

    assert_eq!(normalize_viewer("  "), None);
    assert_eq!(normalize_viewer(" Bob@Acme.TEST "), Some(BOB.to_string()));
    assert_eq!(workspace_scope(None, Some(BOB)), None);
    let team = workspace_scope(Some("team"), Some(BOB)).unwrap();
    assert!(team.contains("team"));
    assert_eq!(scope_viewer(Some(&team)), Some(BOB));
    assert_eq!(
        workspace_scope(Some("team"), Some(" ")).unwrap(),
        BTreeSet::from(["team".to_string()])
    );
}

#[test]
fn an_empty_viewer_hides_every_owned_conversation_and_nothing_else() {
    let fixture = fixture();
    let conn = crate::db::open_read_only(fixture.store.path()).unwrap();
    let ids = vec![
        fixture.message.clone(),
        fixture.document.clone(),
        fixture.legacy.clone(),
        String::new(),
    ];
    let hidden = foreign_conversation_ids(&conn, &ids, "").unwrap();
    assert_eq!(hidden, HashSet::from([fixture.message.clone()]));
    let kept = retain_visible_to_viewer(
        &conn,
        Vec::<String>::new(),
        Some(&personal(Some(BOB))),
        |id| id.clone(),
    )
    .unwrap();
    assert!(kept.is_empty());
}

#[test]
fn legacy_uuid_owner_ids_resolve_via_metadata_email_and_keep_ownerless_rows_visible() {
    let conn = Connection::open_in_memory().unwrap();
    conn.execute_batch(
        "CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, type TEXT, legacy_type TEXT, owner_id TEXT, attrs TEXT);
         INSERT INTO nodes_v2 VALUES
           ('msg-alice', 'MESSAGE', 'Message', 'user:0198427b-23da-56fa-9c5a-9b650f7bf690', '{\"user_email\":\"alice@acme.test\"}'),
           ('chat-legacy', 'CHAT', 'Chat', 'user:0198427b-23da-56fa-9c5a-9b650f7bf690', '{\"conversation_id\":\"c1\"}'),
           ('person-alice', 'PERSON', 'Person', 'user:0198427b-23da-56fa-9c5a-9b650f7bf690', '{\"email\":\"alice@acme.test\"}');",
    )
    .unwrap();
    let ids = vec![
        "msg-alice".to_string(),
        "chat-legacy".to_string(),
        "person-alice".to_string(),
    ];
    let bob_hidden = foreign_conversation_ids(&conn, &ids, BOB).unwrap();
    assert!(bob_hidden.contains("msg-alice"));
    assert!(bob_hidden.contains("person-alice"));
    assert!(!bob_hidden.contains("chat-legacy"));
    assert!(foreign_conversation_ids(&conn, &ids, ALICE)
        .unwrap()
        .is_empty());
}

#[test]
fn private_documents_are_hidden_from_other_users_while_workspace_docs_remain_visible() {
    let conn = Connection::open_in_memory().unwrap();
    conn.execute_batch(
        "CREATE TABLE nodes_v2(id TEXT PRIMARY KEY, type TEXT, legacy_type TEXT, owner_id TEXT, visibility TEXT, attrs TEXT);
         INSERT INTO nodes_v2 VALUES
           ('doc-private', 'DOCUMENT', 'Document', 'alice@acme.test', 'private', '{\"visibility\":\"private\"}'),
           ('doc-shared', 'DOCUMENT', 'Document', 'alice@acme.test', 'workspace', '{\"visibility\":\"workspace\"}');",
    )
    .unwrap();
    let ids = vec!["doc-private".to_string(), "doc-shared".to_string()];
    let bob_hidden = foreign_conversation_ids(&conn, &ids, BOB).unwrap();
    assert_eq!(bob_hidden, HashSet::from(["doc-private".to_string()]));
    assert!(foreign_conversation_ids(&conn, &ids, ALICE)
        .unwrap()
        .is_empty());
}
