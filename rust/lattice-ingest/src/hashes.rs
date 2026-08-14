//! Chunk ids and content hashes, exactly as the Python write path spells them.
//!
//! Three conventions, and they are not interchangeable:
//!
//! * a **chunk id** hashes `"{source_node}:{index}:{chunk_text}"`, so a moved
//!   chunk boundary re-keys the chunk. That is why the plain strategy's
//!   boundaries are a compatibility contract rather than an implementation
//!   detail (`lattice_brain/graph/ingest.py:150,399,746`).
//! * a **file** content hash is sha256 over the file's *bytes*
//!   (`ingest.py:252`), because a file's identity is what is on disk.
//! * a **text / web** content hash is sha256 over
//!   `"{source_type}|{source_uri}|{text}"` and the identity hash wraps it with
//!   the workspace (`ingest.py:660-662`), because two workspaces holding the
//!   same page are two nodes, not one.
//!
//! Python encodes with `errors="replace"`, which only differs from a plain
//! UTF-8 encode for lone surrogates — a `&str` cannot hold one, so the two are
//! the same function here.

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use sha2::{Digest, Sha256};

use lattice_core::clean_text;

/// How many hex characters of a digest an id carries.
pub const ID_DIGEST_CHARS: usize = 24;

/// `_sha256_text` — lowercase hex sha256 of the UTF-8 encoding.
pub fn sha256_text(text: &str) -> String {
    sha256_bytes(text.as_bytes())
}

/// `_sha256_bytes` — lowercase hex sha256 of raw bytes.
pub fn sha256_bytes(data: &[u8]) -> String {
    let digest = Sha256::digest(data);
    let mut out = String::with_capacity(digest.len() * 2);
    for byte in digest {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

/// `chunk:{sha256(f"{source_node_id}:{index}:{chunk_text}")[:24]}`.
pub fn chunk_id(source_node_id: &str, index: usize, chunk_text: &str) -> String {
    let digest = sha256_text(&format!("{source_node_id}:{index}:{chunk_text}"));
    format!("chunk:{}", &digest[..ID_DIGEST_CHARS])
}

/// A file's content hash: sha256 over its bytes.
pub fn file_content_hash(data: &[u8]) -> String {
    sha256_bytes(data)
}

/// A text/web source's content hash: `sha256("{type}|{uri}|{text}")`.
///
/// An absent *and* an empty URI both render as the empty string — Python's
/// `source_uri or ''` treats them the same, and so must this.
pub fn text_content_hash(source_type: &str, source_uri: Option<&str>, text: &str) -> String {
    let uri = source_uri.unwrap_or("");
    sha256_text(&format!("{source_type}|{uri}|{text}"))
}

/// `sha256("{workspace_id or 'legacy-global'}|{content_hash}")`.
///
/// An empty workspace id is falsy in Python, so it means "legacy global" too.
pub fn identity_hash(workspace_id: Option<&str>, content_hash: &str) -> String {
    let workspace = match workspace_id {
        Some(id) if !id.is_empty() => id,
        _ => "legacy-global",
    };
    sha256_text(&format!("{workspace}|{content_hash}"))
}

/// `webdoc:{identity_hash[:24]}` — the content node id for a text/web source.
pub fn content_id(identity_hash: &str) -> String {
    let head: String = identity_hash.chars().take(ID_DIGEST_CHARS).collect();
    format!("webdoc:{head}")
}

/// The vector index's `text_hash`: sha256 over the **cleaned** text.
///
/// `_upsert_vector_item` collapses whitespace before hashing (`clean_text`),
/// unlike chunking, which strips only. Two chunks that differ solely in
/// internal whitespace therefore share an embedding row but not a chunk id.
pub fn vector_text_hash(text: &str) -> String {
    sha256_text(&clean_text(text))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sha256_matches_the_published_vectors() {
        assert_eq!(
            sha256_text(""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            sha256_text("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(sha256_bytes(b"abc"), sha256_text("abc"));
        assert_eq!(sha256_text("회의").len(), 64);
    }

    #[test]
    fn a_chunk_id_is_the_node_the_index_and_the_text() {
        let id = chunk_id("file:a", 0, "hello");
        assert!(id.starts_with("chunk:"));
        assert_eq!(id.len(), "chunk:".len() + ID_DIGEST_CHARS);
        assert_eq!(
            id,
            format!("chunk:{}", &sha256_text("file:a:0:hello")[..24])
        );
        assert_ne!(id, chunk_id("file:a", 1, "hello"));
        assert_ne!(id, chunk_id("file:b", 0, "hello"));
        assert_ne!(id, chunk_id("file:a", 0, "hello "));
    }

    #[test]
    fn an_absent_uri_and_an_empty_uri_hash_the_same() {
        assert_eq!(
            text_content_hash("note", None, "x"),
            text_content_hash("note", Some(""), "x")
        );
        assert_ne!(
            text_content_hash("note", Some("u"), "x"),
            text_content_hash("note", None, "x")
        );
    }

    #[test]
    fn an_empty_workspace_is_the_legacy_global_bucket() {
        let content = text_content_hash("note", None, "x");
        assert_eq!(
            identity_hash(None, &content),
            identity_hash(Some(""), &content)
        );
        assert_eq!(
            identity_hash(None, &content),
            sha256_text(&format!("legacy-global|{content}"))
        );
        assert_ne!(
            identity_hash(Some("ws"), &content),
            identity_hash(None, &content)
        );
        let id = content_id(&identity_hash(Some("ws"), &content));
        assert!(id.starts_with("webdoc:"));
        assert_eq!(id.len(), "webdoc:".len() + ID_DIGEST_CHARS);
    }

    #[test]
    fn the_vector_hash_collapses_whitespace_and_the_chunk_id_does_not() {
        assert_eq!(vector_text_hash("  a   b "), vector_text_hash("a b"));
        assert_eq!(vector_text_hash("a b"), sha256_text("a b"));
        assert_ne!(chunk_id("n", 0, "a   b"), chunk_id("n", 0, "a b"));
    }
}
