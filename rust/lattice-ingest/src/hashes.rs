use lattice_core::clean_text;
pub use lattice_core::{sha256_bytes, sha256_text};

pub const ID_DIGEST_CHARS: usize = 24;

pub fn chunk_id(source_node_id: &str, index: usize, chunk_text: &str) -> String {
    let digest = sha256_text(&format!("{source_node_id}:{index}:{chunk_text}"));
    format!("chunk:{}", &digest[..ID_DIGEST_CHARS])
}

pub fn file_content_hash(data: &[u8]) -> String {
    sha256_bytes(data)
}

pub fn text_content_hash(source_type: &str, source_uri: Option<&str>, text: &str) -> String {
    let uri = source_uri.unwrap_or("");
    sha256_text(&format!("{source_type}|{uri}|{text}"))
}

pub fn identity_hash(workspace_id: Option<&str>, content_hash: &str) -> String {
    let workspace = match workspace_id {
        Some(id) if !id.is_empty() => id,
        _ => "legacy-global",
    };
    sha256_text(&format!("{workspace}|{content_hash}"))
}

pub fn content_id(identity_hash: &str) -> String {
    let head: String = identity_hash.chars().take(ID_DIGEST_CHARS).collect();
    format!("webdoc:{head}")
}

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
