use std::path::Path;

use lattice_auth::OrderedMap;
use serde_json::json;

pub(crate) fn seed_users(dir: &Path) {
    let mut owner = OrderedMap::new();
    owner.insert("password", json!("x"));
    owner.insert("name", json!("Fixture Owner"));
    owner.insert("nickname", json!("owner"));
    owner.insert("role", json!("admin"));
    owner.insert("disabled", json!(false));
    owner.insert(
        "id",
        json!(lattice_auth::stable_user_id("owner@lattice.test")),
    );
    owner.insert("email", json!("owner@lattice.test"));
    let mut member = OrderedMap::new();
    member.insert("password", json!("x"));
    member.insert("name", json!("Fixture Member"));
    member.insert("nickname", json!("member"));
    member.insert("role", json!("user"));
    member.insert("disabled", json!(false));
    member.insert(
        "id",
        json!(lattice_auth::stable_user_id("member@lattice.test")),
    );
    member.insert("email", json!("member@lattice.test"));
    let mut users = OrderedMap::new();
    users.insert("owner@lattice.test", serde_json::to_value(owner).unwrap());
    users.insert("member@lattice.test", serde_json::to_value(member).unwrap());
    std::fs::write(
        dir.join("users.json"),
        lattice_auth::pyjson::dumps_indent2(&users).expect("users"),
    )
    .expect("write users");
}

pub(crate) fn seed_chat(dir: &Path) {
    let history = json!([
        {
            "role": "user",
            "content": "배포 키는 sk-fixture1234567890abcdefghij 이고 주민번호는 900101-1234567 이야",
            "timestamp": "2026-08-01T09:00:00+00:00",
            "user_email": "owner@lattice.test",
            "user_nickname": "owner",
            "conversation_id": "conv-fixture-001"
        },
        {
            "role": "assistant",
            "content": "민감정보는 저장하지 않겠습니다.",
            "timestamp": "2026-08-01T09:00:05+00:00",
            "user_email": "owner@lattice.test",
            "user_nickname": "owner",
            "conversation_id": "conv-fixture-001"
        }
    ]);
    std::fs::write(
        dir.join("chat_history.json"),
        serde_json::to_string_pretty(&history).unwrap(),
    )
    .expect("chat");
}

pub(crate) fn seed_audit(dir: &Path) {
    let events = json!([{
        "event_type": "document_upload",
        "timestamp": "2026-08-01T09:05:00+00:00",
        "user_email": "owner@lattice.test",
        "user_nickname": "owner",
        "filename": "fixture-contract.txt",
        "ext": ".txt",
        "bytes": 128,
        "sensitivity": "high",
        "sensitive_labels": ["secret"],
        "content_preview": "API key: sk-fixture1234567890abcdefghij",
        "extracted_text": "API key: sk-fixture1234567890abcdefghij / 계약 조건"
    }]);
    std::fs::write(
        dir.join("audit_log.json"),
        serde_json::to_string_pretty(&events).unwrap(),
    )
    .expect("audit");
}
