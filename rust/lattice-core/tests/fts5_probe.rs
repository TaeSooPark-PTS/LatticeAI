//! Empirical FTS5 probe.
#[test]
fn bundled_sqlite_has_fts5_trigram() {
    let dir = tempfile::tempdir().unwrap();
    let conn = rusqlite::Connection::open(dir.path().join("probe.sqlite")).unwrap();
    conn.execute_batch("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
        .expect("bundled rusqlite must ship FTS5 with the trigram tokenizer");
    conn.execute("INSERT INTO t(x) VALUES ('hello world')", [])
        .unwrap();
    let n: i64 = conn
        .query_row("SELECT count(*) FROM t WHERE t MATCH '\"ell\"'", [], |r| {
            r.get(0)
        })
        .unwrap();
    assert_eq!(n, 1);
    let v: String = conn
        .query_row("SELECT sqlite_version()", [], |r| r.get(0))
        .unwrap();
    println!("bundled sqlite_version = {v}");
}
