//! Port of `lattice_brain/embeddings.py` — the deterministic local embedder.
//!
//! Bit-for-bit parity is the whole contract here: the vectors in
//! `vector_embeddings` were written by the Python model, and a Rust query vector
//! that differs in the last ulp produces a different ranking. Three places
//! decide that and each is called out below: the tokenizer's feature list, the
//! blake2b-8 hash (digest length is a BLAKE2 *parameter*, not a truncation), and
//! the accumulation **order** of the dot product.

use blake2::digest::consts::U8;
use blake2::{Blake2b, Digest};

use crate::db::CoreError;

/// `LATTICEAI_VECTOR_DIM`, read once per model construction.
pub const VECTOR_DIM_ENV: &str = "LATTICEAI_VECTOR_DIM";
/// `lattice_brain.embeddings.DEFAULT_EMBEDDING_DIM` with nothing configured.
pub const DEFAULT_EMBEDDING_DIM: usize = 384;

type Blake2b64 = Blake2b<U8>;

/// `lattice_brain.embeddings.embedding_model_id`.
pub fn embedding_model_id(dim: usize) -> String {
    format!("lattice-local-hash-v1:{dim}")
}

/// The deterministic offline embedder Python writes the index with.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalEmbeddingModel {
    dim: usize,
    model_id: String,
}

impl Default for LocalEmbeddingModel {
    fn default() -> Self {
        Self::from_env()
    }
}

impl LocalEmbeddingModel {
    /// A model of an explicit dimension.
    pub fn new(dim: usize) -> Self {
        Self {
            dim,
            model_id: embedding_model_id(dim),
        }
    }

    /// Resolve the dimension from `LATTICEAI_VECTOR_DIM` (default 384).
    ///
    /// Deviation from Python, stated rather than hidden: `int(os.getenv(...))`
    /// raises at import on a malformed value and takes the process down. A
    /// library cannot do that to its host, so an unparseable value falls back to
    /// the documented default here.
    pub fn from_env() -> Self {
        let dim = std::env::var(VECTOR_DIM_ENV)
            .ok()
            .and_then(|raw| raw.trim().parse::<usize>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(DEFAULT_EMBEDDING_DIM);
        Self::new(dim)
    }

    /// The vector width this model emits.
    pub fn dim(&self) -> usize {
        self.dim
    }

    /// `lattice-local-hash-v1:{dim}` — the value stored in `embedding_model`.
    pub fn model_id(&self) -> &str {
        &self.model_id
    }

    /// `LocalEmbeddingModel.embed` — hashed bag of features, L2 normalized.
    pub fn embed(&self, text: &str) -> Vec<f64> {
        let mut vector = vec![0.0f64; self.dim];
        let features = tokenize(text);
        if features.is_empty() {
            return vector;
        }
        for feature in &features {
            let (index, sign) = hash_to_index(feature, self.dim);
            vector[index] += sign;
        }
        // `math.sqrt(sum(v * v for v in vector))` — index order, f64 accumulator.
        let mut total = 0.0f64;
        for value in &vector {
            total += value * value;
        }
        let norm = total.sqrt();
        if norm <= 0.0 {
            return vector;
        }
        for value in vector.iter_mut() {
            *value /= norm;
        }
        vector
    }

    /// `LocalEmbeddingModel.similarity` — plain dot product, strict on width.
    ///
    /// The accumulation is sequential and in index order because Python's
    /// `sum(...)` is: any reassociation (chunking, SIMD, Kahan) changes the last
    /// bits and therefore changes tie-breaks in the ranking above.
    pub fn similarity(&self, left: &[f64], right: &[f64]) -> Result<f64, CoreError> {
        if left.len() != right.len() {
            return Err(CoreError::DimensionMismatch {
                left: left.len(),
                right: right.len(),
            });
        }
        let mut total = 0.0f64;
        for (a, b) in left.iter().zip(right.iter()) {
            total += a * b;
        }
        Ok(total)
    }

    /// `LocalEmbeddingModel.encode` — little-endian f32, no header.
    pub fn encode(&self, vector: &[f64]) -> Vec<u8> {
        let mut out = Vec::with_capacity(vector.len() * 4);
        for value in vector {
            out.extend_from_slice(&(*value as f32).to_le_bytes());
        }
        out
    }

    /// `LocalEmbeddingModel.decode` — including its recount on a length mismatch.
    pub fn decode(&self, payload: &[u8], dim: Option<usize>) -> Vec<f64> {
        if payload.is_empty() {
            return Vec::new();
        }
        let mut count = match dim {
            Some(value) if value > 0 => value,
            _ => self.dim,
        };
        if payload.len() != count * 4 {
            count = payload.len() / 4;
        }
        let mut out = Vec::with_capacity(count);
        for chunk in payload[..count * 4].chunks_exact(4) {
            let bits = [chunk[0], chunk[1], chunk[2], chunk[3]];
            out.push(f32::from_le_bytes(bits) as f64);
        }
        out
    }
}

/// `lattice_brain.embeddings._hash_to_index` — blake2b(8) → (index, sign).
pub fn hash_to_index(feature: &str, dim: usize) -> (usize, f64) {
    let mut hasher = Blake2b64::new();
    hasher.update(feature.as_bytes());
    let digest = hasher.finalize();
    let value = u64::from_be_bytes([
        digest[0], digest[1], digest[2], digest[3], digest[4], digest[5], digest[6], digest[7],
    ]);
    let sign = if value & 1 == 0 { 1.0 } else { -1.0 };
    ((value % dim as u64) as usize, sign)
}

fn is_token_head(c: char) -> bool {
    c.is_ascii_lowercase() || c.is_ascii_digit()
}

fn is_token_tail(c: char) -> bool {
    is_token_head(c) || matches!(c, '_' | '.' | ':' | '/' | '+' | '-')
}

fn is_hangul(c: char) -> bool {
    ('\u{AC00}'..='\u{D7A3}').contains(&c)
}

/// `lattice_brain.embeddings._tokenize`.
///
/// Hand-scanned rather than regex-driven: the pattern
/// `[a-z0-9][a-z0-9_.:/+-]{1,}|[가-힣]{2,}` has two disjoint alternatives, both
/// greedy with nothing following them, so the leftmost-first match at each
/// position is simply "the longest run", and a scanner states that without
/// dragging a regex engine into the crate that must stay smallest.
pub fn tokenize(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.to_lowercase().chars().collect();
    let mut features: Vec<String> = Vec::new();
    let mut i = 0usize;
    while i < chars.len() {
        let start = i;
        if is_token_head(chars[i]) {
            let mut j = i + 1;
            while j < chars.len() && is_token_tail(chars[j]) {
                j += 1;
            }
            if j - start >= 2 {
                push_features(&chars[start..j], &mut features);
                i = j;
                continue;
            }
        } else if is_hangul(chars[i]) {
            let mut j = i;
            while j < chars.len() && is_hangul(chars[j]) {
                j += 1;
            }
            if j - start >= 2 {
                push_features(&chars[start..j], &mut features);
                i = j;
                continue;
            }
        }
        i += 1;
    }
    features
}

fn push_features(token: &[char], features: &mut Vec<String>) {
    let text: String = token.iter().collect();
    features.push(format!("tok:{text}"));
    if token.len() >= 5 && token.iter().any(|c| c.is_ascii_lowercase()) {
        for i in 0..token.len() - 2 {
            features.push(format!(
                "tri:{}",
                token[i..i + 3].iter().collect::<String>()
            ));
        }
    }
    if token.iter().any(|c| is_hangul(*c)) && token.len() >= 3 {
        for i in 0..token.len() - 1 {
            features.push(format!("ko:{}", token[i..i + 2].iter().collect::<String>()));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn model_id_tracks_the_dimension() {
        assert_eq!(
            LocalEmbeddingModel::new(384).model_id(),
            "lattice-local-hash-v1:384"
        );
        assert_eq!(embedding_model_id(768), "lattice-local-hash-v1:768");
        assert_eq!(LocalEmbeddingModel::new(7).dim(), 7);
        assert_eq!(
            LocalEmbeddingModel::default().dim(),
            LocalEmbeddingModel::from_env().dim()
        );
        assert_eq!(LocalEmbeddingModel::new(384), LocalEmbeddingModel::new(384));
        assert!(format!("{:?}", LocalEmbeddingModel::new(4)).contains("dim"));
    }

    #[test]
    fn tokenizer_matches_the_python_feature_shapes() {
        // Latin token >= 5 chars gets trigrams; the short one does not.
        assert_eq!(tokenize("Hi"), vec!["tok:hi"]);
        let long = tokenize("Lattice");
        assert_eq!(long[0], "tok:lattice");
        assert_eq!(long.len(), 1 + "lattice".len() - 2);
        assert_eq!(long[1], "tri:lat");
        // A single leading char with no tail is not a token at all.
        assert!(tokenize("a").is_empty());
        assert!(tokenize("").is_empty());
        // Punctuation is part of the tail class, so paths stay one token.
        assert_eq!(tokenize("a/b")[0], "tok:a/b");
    }

    #[test]
    fn korean_bigrams_need_three_syllables() {
        assert_eq!(tokenize("회의"), vec!["tok:회의"]);
        let three = tokenize("회의록");
        assert_eq!(three, vec!["tok:회의록", "ko:회의", "ko:의록"]);
        // One lone syllable is not a token.
        assert!(tokenize("회").is_empty());
        assert!(tokenize("회 a").is_empty());
    }

    #[test]
    fn embed_is_unit_length_and_stable() {
        let model = LocalEmbeddingModel::new(384);
        let vector = model.embed("hybrid retrieval");
        let norm = model.similarity(&vector, &vector).unwrap();
        assert!((norm - 1.0).abs() < 1e-12, "not unit length: {norm}");
        assert_eq!(vector.len(), 384);
        assert_eq!(model.embed("hybrid retrieval"), vector);
        // No features at all → the zero vector, never a NaN from 0/0.
        assert!(model.embed("!!!").iter().all(|v| *v == 0.0));
    }

    #[test]
    fn encode_decode_round_trips_through_f32() {
        let model = LocalEmbeddingModel::new(384);
        let vector = model.embed("회의록 결정 사항");
        let blob = model.encode(&vector);
        assert_eq!(blob.len(), 384 * 4);
        let back = model.decode(&blob, Some(384));
        assert_eq!(back.len(), 384);
        for (a, b) in vector.iter().zip(back.iter()) {
            assert_eq!(*b, *a as f32 as f64);
        }
        // A wrong declared dim recomputes the count from the payload length.
        assert_eq!(model.decode(&blob, Some(7)).len(), 384);
        assert_eq!(model.decode(&blob, None).len(), 384);
        assert_eq!(model.decode(&blob, Some(0)).len(), 384);
        assert!(model.decode(&[], Some(384)).is_empty());
    }

    #[test]
    fn similarity_refuses_a_dimension_mismatch() {
        let model = LocalEmbeddingModel::new(4);
        let err = model.similarity(&[1.0, 2.0], &[1.0, 2.0, 3.0]).unwrap_err();
        assert!(format!("{err}").contains("dimension mismatch"));
        assert_eq!(model.similarity(&[1.0, 2.0], &[3.0, 4.0]).unwrap(), 11.0);
    }

    #[test]
    fn hash_sign_comes_from_the_low_bit() {
        let (index, sign) = hash_to_index("tok:lattice", 384);
        assert!(index < 384);
        assert!(sign == 1.0 || sign == -1.0);
    }
}
