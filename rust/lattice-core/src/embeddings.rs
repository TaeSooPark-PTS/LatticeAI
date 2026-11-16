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
pub use crate::embedding_contract::{
    EmbeddingIndexContract, DEFAULT_EMBEDDING_MODE, DEFAULT_INDEX_VERSION, NORMALIZATION_L2,
};

/// `LATTICEAI_VECTOR_DIM`, read once per model construction.
pub const VECTOR_DIM_ENV: &str = "LATTICEAI_VECTOR_DIM";
/// `lattice_brain.embeddings.DEFAULT_EMBEDDING_DIM` with nothing configured.
pub const DEFAULT_EMBEDDING_DIM: usize = 384;
/// Primary Hugging Face semantic embedding model (`google/embeddinggemma-2`).
pub const EMBEDDINGGEMMA_2_MODEL_ID: &str = "google/embeddinggemma-2";
/// Output vector dimensionality of `google/embeddinggemma-2`.
pub const EMBEDDINGGEMMA_2_DIM: usize = 768;

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

    /// Explicit offline hash fallback contract for this model.
    pub fn contract(&self) -> EmbeddingIndexContract {
        EmbeddingIndexContract::from_hash(self.model_id.clone(), self.dim)
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
        let (chunks, _) = payload[..count * 4].as_chunks::<4>();
        for chunk in chunks {
            out.push(f32::from_le_bytes(*chunk) as f64);
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

/// Default dimension for `FallbackTextEmbedder` (matches EmbeddingGemma 2 768-d).
pub const DEFAULT_FALLBACK_DIM: usize = EMBEDDINGGEMMA_2_DIM;

/// Model identifier for `FallbackTextEmbedder`.
pub fn fallback_embedding_model_id(dim: usize) -> String {
    format!("lattice-fallback-subword-v2:{dim}")
}

/// Upgraded offline zero-download text embedder using multi-scale character
/// n-grams (3..5-grams + CJK bigrams), word unigrams/bigrams, IDF/positional
/// weighting, 2-probe signed random projection, and L2 normalization.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FallbackTextEmbedder {
    dim: usize,
    model_id: String,
}

impl Default for FallbackTextEmbedder {
    fn default() -> Self {
        Self::new(DEFAULT_FALLBACK_DIM)
    }
}

impl FallbackTextEmbedder {
    /// Construct a `FallbackTextEmbedder` with explicit target dimension (default 768).
    pub fn new(dim: usize) -> Self {
        let resolved = if dim == 0 { DEFAULT_FALLBACK_DIM } else { dim };
        Self {
            dim: resolved,
            model_id: fallback_embedding_model_id(resolved),
        }
    }

    /// Construct from `LATTICEAI_VECTOR_DIM` or default 768.
    pub fn from_env() -> Self {
        let dim = std::env::var(VECTOR_DIM_ENV)
            .ok()
            .and_then(|raw| raw.trim().parse::<usize>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(DEFAULT_FALLBACK_DIM);
        Self::new(dim)
    }

    pub fn dim(&self) -> usize {
        self.dim
    }

    pub fn model_id(&self) -> &str {
        &self.model_id
    }

    /// Explicit offline subword fallback contract for this model.
    pub fn contract(&self) -> EmbeddingIndexContract {
        EmbeddingIndexContract::from_hash(self.model_id.clone(), self.dim)
    }

    /// Deterministically embed `text` into a unit-normalized `dim`-dimensional vector.
    pub fn embed(&self, text: &str) -> Vec<f64> {
        let features = extract_fallback_features(text);
        project_weighted_features(&features, self.dim)
    }

    pub fn embed_batch(&self, texts: &[&str]) -> Vec<Vec<f64>> {
        texts.iter().map(|t| self.embed(t)).collect()
    }

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

    pub fn encode(&self, vector: &[f64]) -> Vec<u8> {
        let mut out = Vec::with_capacity(vector.len() * 4);
        for value in vector {
            out.extend_from_slice(&(*value as f32).to_le_bytes());
        }
        out
    }

    pub fn decode(&self, payload: &[u8], dim: Option<usize>) -> Vec<f64> {
        LocalEmbeddingModel::new(self.dim).decode(payload, dim)
    }
}

const STRIP_PREFIXES: [&str; 4] = [
    "task: search result | query: ",
    "title: none | text: ",
    "query: ",
    "passage: ",
];

const EN_STOPWORDS: [&str; 29] = [
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "for", "from", "has", "have", "in",
    "is", "it", "its", "of", "on", "or", "that", "the", "their", "this", "to", "was", "were",
    "will", "with",
];

const KO_STOPWORDS: [&str; 12] = [
    "있는",
    "없는",
    "하는",
    "되는",
    "그리고",
    "그러나",
    "따라서",
    "경우",
    "통해",
    "위해",
    "대한",
    "관한",
];

const KO_PARTICLES: [&str; 28] = [
    "에서", "으로", "에게", "부터", "까지", "처럼", "대한", "관한", "하는", "하고", "하며", "이고",
    "이나", "이라", "에는", "에도", "은", "는", "이", "가", "을", "를", "의", "에", "로", "와",
    "과", "도",
];

fn is_cjk_ideograph(c: char) -> bool {
    ('\u{4E00}'..='\u{9FFF}').contains(&c) || ('\u{3040}'..='\u{30FF}').contains(&c)
}

fn strip_instruction_prefix(text: &str) -> &str {
    let trimmed = text.trim();
    let lower = trimmed.to_ascii_lowercase();
    for prefix in STRIP_PREFIXES {
        if lower.starts_with(prefix) {
            return trimmed[prefix.len()..].trim();
        }
    }
    trimmed
}

fn strip_korean_particle(token: &str) -> &str {
    let char_count = token.chars().count();
    for suffix in KO_PARTICLES {
        if let Some(stripped) = token.strip_suffix(suffix) {
            let suffix_chars = suffix.chars().count();
            if char_count >= suffix_chars + 2 {
                return stripped;
            }
        }
    }
    if let Some(stripped) = token.strip_suffix("만") {
        if char_count >= 3 {
            return stripped;
        }
    }
    token
}

fn hangul_lead_jamo_key(c: char) -> String {
    let code = c as u32;
    if (0xAC00..=0xD7A3).contains(&code) {
        let offset = code - 0xAC00;
        let cho = offset / (21 * 28);
        let jung = (offset / 28) % 21;
        format!("{cho:02}_{jung:02}")
    } else {
        c.to_string()
    }
}

fn scan_fallback_tokens(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    let mut tokens = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        if is_token_head(chars[i]) {
            let start = i;
            i += 1;
            while i < chars.len() && is_token_tail(chars[i]) {
                i += 1;
            }
            tokens.push(chars[start..i].iter().collect());
        } else if is_hangul(chars[i]) {
            let start = i;
            i += 1;
            while i < chars.len() && is_hangul(chars[i]) {
                i += 1;
            }
            tokens.push(chars[start..i].iter().collect());
        } else if is_cjk_ideograph(chars[i]) {
            let start = i;
            i += 1;
            while i < chars.len() && is_cjk_ideograph(chars[i]) {
                i += 1;
            }
            tokens.push(chars[start..i].iter().collect());
        } else {
            i += 1;
        }
    }
    tokens
}

/// Extract multi-scale subword n-grams (3..5-grams + CJK bigrams), word unigrams/bigrams,
/// and IDF/positional weights for `FallbackTextEmbedder`.
pub fn extract_fallback_features(text: &str) -> Vec<(String, f64)> {
    let cleaned = strip_instruction_prefix(text).to_lowercase();
    if cleaned.is_empty() {
        return Vec::new();
    }
    let raw_tokens = scan_fallback_tokens(&cleaned);
    if raw_tokens.is_empty() {
        return Vec::new();
    }

    let mut order: Vec<String> = Vec::new();
    let mut tf_counts: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
    let mut base_weights: std::collections::HashMap<String, f64> = std::collections::HashMap::new();
    let mut first_pos: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
    let mut content_stems: Vec<(String, usize)> = Vec::new();

    let mut add = |feat: String, weight: f64, pos: usize| {
        if let Some(count) = tf_counts.get_mut(&feat) {
            *count += 1;
            if let Some(existing) = base_weights.get_mut(&feat) {
                if weight > *existing {
                    *existing = weight;
                }
            }
        } else {
            order.push(feat.clone());
            tf_counts.insert(feat.clone(), 1);
            base_weights.insert(feat.clone(), weight);
            first_pos.insert(feat, pos);
        }
    };

    for (pos, tok) in raw_tokens.iter().enumerate() {
        let Some(first_ch) = tok.chars().next() else {
            continue;
        };
        if is_hangul(first_ch) {
            if KO_STOPWORDS.contains(&tok.as_str()) {
                add(format!("tok:{tok}"), 0.20, pos);
                continue;
            }
            let stem = strip_korean_particle(tok);
            let stem_chars: Vec<char> = stem.chars().collect();
            let idf = 1.0 + 0.18 * (stem_chars.len().min(6) as f64);
            add(format!("tok:{tok}"), 0.95 * idf, pos);
            if stem != tok {
                add(format!("stem:{stem}"), 1.35 * idf, pos);
            } else {
                add(format!("stem:{stem}"), 1.20 * idf, pos);
            }
            content_stems.push((stem.to_string(), pos));

            if stem_chars.len() >= 2 {
                for ch in &stem_chars {
                    add(format!("ko1:{ch}"), 0.45, pos);
                }
                for i in 0..stem_chars.len() - 1 {
                    let bg: String = stem_chars[i..i + 2].iter().collect();
                    add(format!("ko2:{bg}"), 1.15 * idf, pos);
                    let j1 = hangul_lead_jamo_key(stem_chars[i]);
                    let j2 = hangul_lead_jamo_key(stem_chars[i + 1]);
                    add(format!("jamo:{j1}:{j2}"), 0.55, pos);
                }
            }
            if stem_chars.len() >= 3 {
                for i in 0..stem_chars.len() - 2 {
                    let tg: String = stem_chars[i..i + 3].iter().collect();
                    add(format!("ko3:{tg}"), 0.95 * idf, pos);
                }
            }
            if stem != tok {
                let tok_chars: Vec<char> = tok.chars().collect();
                if tok_chars.len() >= 2 {
                    for i in 0..tok_chars.len() - 1 {
                        let bg: String = tok_chars[i..i + 2].iter().collect();
                        add(format!("ko2:{bg}"), 0.75, pos);
                    }
                }
            }
        } else if is_cjk_ideograph(first_ch) {
            let cjk_chars: Vec<char> = tok.chars().collect();
            let idf = 1.0 + 0.16 * (cjk_chars.len().min(6) as f64);
            add(format!("tok:{tok}"), 1.10 * idf, pos);
            content_stems.push((tok.clone(), pos));
            for ch in &cjk_chars {
                add(format!("cjk1:{ch}"), 0.55, pos);
            }
            if cjk_chars.len() >= 2 {
                for i in 0..cjk_chars.len() - 1 {
                    let bg: String = cjk_chars[i..i + 2].iter().collect();
                    add(format!("cjk2:{bg}"), 1.15 * idf, pos);
                }
            }
            if cjk_chars.len() >= 3 {
                for i in 0..cjk_chars.len() - 2 {
                    let tg: String = cjk_chars[i..i + 3].iter().collect();
                    add(format!("cjk3:{tg}"), 0.90 * idf, pos);
                }
            }
        } else {
            let subparts: Vec<&str> = tok
                .split(['_', '.', ':', '/', '+', '-'])
                .filter(|s| !s.is_empty())
                .collect();
            if subparts.len() > 1 {
                add(format!("tok:{tok}"), 1.15, pos);
            }
            for part in subparts {
                let part_chars: Vec<char> = part.chars().collect();
                if part_chars.len() < 2 && !part_chars.iter().all(|c| c.is_ascii_digit()) {
                    continue;
                }
                if EN_STOPWORDS.contains(&part) {
                    add(format!("tok:{part}"), 0.18, pos);
                    continue;
                }
                let idf = 1.0 + 0.15 * (part_chars.len().min(8) as f64);
                add(format!("tok:{part}"), 1.20 * idf, pos);
                content_stems.push((part.to_string(), pos));

                if part_chars.iter().any(|c| c.is_ascii_lowercase()) && part_chars.len() >= 3 {
                    let padded: Vec<char> = format!("<{part}>").chars().collect();
                    let plen = padded.len();
                    for (n, n_weight) in [(3usize, 0.65f64), (4usize, 0.75f64), (5usize, 0.70f64)] {
                        if plen >= n {
                            let count = (plen - n + 1).max(1) as f64;
                            let scale = n_weight * idf / count.sqrt();
                            for i in 0..=plen - n {
                                let ng: String = padded[i..i + n].iter().collect();
                                add(format!("g{n}:{ng}"), scale, pos);
                            }
                        }
                    }
                }
            }
        }
    }

    for idx in 0..content_stems.len().saturating_sub(1) {
        let (w1, pos1) = &content_stems[idx];
        let (w2, _) = &content_stems[idx + 1];
        if w1 != w2 {
            add(format!("bi:{w1}_{w2}"), 1.25, *pos1);
            let (lo, hi) = if w1 < w2 { (w1, w2) } else { (w2, w1) };
            add(format!("pair:{lo}_{hi}"), 0.60, *pos1);
        }
    }

    let mut weighted = Vec::with_capacity(order.len());
    for feat in order {
        let tf = *tf_counts.get(&feat).unwrap_or(&1) as f64;
        let base_w = *base_weights.get(&feat).unwrap_or(&1.0);
        let pos = *first_pos.get(&feat).unwrap_or(&0) as f64;
        let tf_factor = 1.0 + tf.ln();
        let pos_boost = 1.0 + 0.25 * (-pos / 12.0).exp();
        weighted.push((feat, base_w * tf_factor * pos_boost));
    }
    weighted
}

/// Project weighted features into a unit-normalized `dim`-dimensional vector
/// using 2-probe signed Blake2b-64 random projection.
pub fn project_weighted_features(features: &[(String, f64)], dim: usize) -> Vec<f64> {
    let mut vector = vec![0.0f64; dim];
    if dim == 0 || features.is_empty() {
        return vector;
    }
    let inv_sqrt2 = std::f64::consts::FRAC_1_SQRT_2;
    for (feat, weight) in features {
        let mut hasher = Blake2b64::new();
        hasher.update(feat.as_bytes());
        let digest = hasher.finalize();
        let h1 = u32::from_be_bytes([digest[0], digest[1], digest[2], digest[3]]) as usize;
        let h2 = u32::from_be_bytes([digest[4], digest[5], digest[6], digest[7]]) as usize;
        let idx1 = h1 % dim;
        let sign1 = if (h1 & 1) == 0 { 1.0 } else { -1.0 };
        vector[idx1] += sign1 * (*weight);

        let idx2 = h2 % dim;
        let sign2 = if (h2 & 1) == 0 { 1.0 } else { -1.0 };
        vector[idx2] += sign2 * (*weight * inv_sqrt2);
    }

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

    #[test]
    fn fallback_text_embedder_upgrades_subword_and_korean_similarity() {
        let embedder = FallbackTextEmbedder::new(768);
        assert_eq!(embedder.dim(), 768);
        assert_eq!(embedder.model_id(), "lattice-fallback-subword-v2:768");

        let q = embedder.embed("지식 그래프 하이브리드 검색을 최적화하는 방법");
        let rel = embedder.embed("지식 그래프에서 하이브리드 검색 성능 최적화 가이드");
        let unrel = embedder.embed("회계 분기별 세금 신고서 양식 제출 안내");

        let sim_rel = embedder.similarity(&q, &rel).unwrap();
        let sim_unrel = embedder.similarity(&q, &unrel).unwrap();
        assert!(
            sim_rel > 0.45 && sim_rel > sim_unrel + 0.30,
            "expected strong Korean morphological/bigram similarity: rel={sim_rel}, unrel={sim_unrel}"
        );

        // English morphological variants (retrieve / retrieval / retrieving) match via 3..5-grams
        let e1 = embedder.embed("retrieving documents from vector index");
        let e2 = embedder.embed("document retrieval with vector indexing");
        let e3 = embedder.embed("css animation keyframe button border");
        let sim_en_rel = embedder.similarity(&e1, &e2).unwrap();
        let sim_en_unrel = embedder.similarity(&e1, &e3).unwrap();
        assert!(
            sim_en_rel > 0.40 && sim_en_rel > sim_en_unrel + 0.25,
            "expected strong English subword n-gram similarity: rel={sim_en_rel}, unrel={sim_en_unrel}"
        );
    }
}
