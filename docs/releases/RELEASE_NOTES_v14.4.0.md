[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v14.4.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v14.4.0.md)

# Lattice AI v14.4.0 — Measured Retrieval

Release date: **2026-11-27**

`v14.4.0` is the first release whose retrieval was measured with the real EmbeddingGemma 2 and `BAAI/bge-reranker-v2-m3` weights. Measuring it exposed several defects that the previous hash-only gates could not see; they are fixed here, together with the two 3D view items deferred from v14.3.0.

## Highlights

### 1. Retrieval Fixes

- **"Latest" questions no longer lose the answer:** chat passed its 20-candidate rerank pool to the recency sort instead of the 5-document answer size, so newer off-topic documents could displace the correct one. Recency now only reorders the answer.
- **Workspace scope is applied before the top-k cut:** the no-worker chat vector lane, hybrid search, memory recall and the approximate (HNSW) index ranked the whole brain first and filtered by workspace afterwards, which could drop every in-scope hit.
- **`LATTICEAI_RERANK_CANDIDATES` above 20 now works** (up to 64).
- **The reranker is warmed at worker start** in a background thread, so the first queries no longer exceed the 2 s rerank timeout and silently keep the fused order. A cached reranker is therefore loaded into memory at start; `LATTICEAI_RERANKER=off` disables it.
- **Concurrent cold requests load the embedding model once** (Hugging Face and MLX providers).

### 2. EmbeddingGemma 2 Setup

- `pip install "ltcai[rag]"` now installs `torchvision`, which the EmbeddingGemma 2 processor imports even for text. Without it the model could not load and the reported reason was empty.

### 3. Evaluation Honesty

- **The v14.3.0 hash gates were measured with the vector lane inactive.** The evaluation seeded 8-byte values that the retriever read as 4-byte values, so every vector row was dropped. The seeding is fixed, the floors are re-measured and raised, and the gates now fail if the vector lane returns nothing.
- The chat-path evaluation now uses the product's query planning, embedded query text, evidence gate, rerank candidate rules and passage text.
- The nightly workflow measures the product retriever instead of a separate lexical baseline.
- New `scripts/run_neural_retrieval_eval.py` evaluates cached real weights offline.

### 4. 3D Living Brain

- Node labels in the 3D view follow the label mode, with overlap culling sized for Korean and other wide glyphs.
- Losing the WebGL context pauses rendering, announces the state, and resumes on restore.
- Hover picking runs at most once per frame.

## Real-Model Retrieval Evaluation

`datasets/rag_eval_v1.json` (`rag-eval-v1.0.0`, 40 documents, 320 queries, 248 answerable), Apple Silicon (MPS), `sentence-transformers` 6.3.0, `transformers` 5.24.1, `torch` 2.15.0.

| Mode | Retriever | Recall@5 | MRR | nDCG@10 |
| --- | --- | :---: | :---: | :---: |
| Constant timestamps | Hash embedder | 0.9839 | 0.9026 | 0.9009 |
| Constant timestamps | EmbeddingGemma 2 | 1.0000 | 0.8770 | 0.8924 |
| Constant timestamps | EmbeddingGemma 2 + reranker | 1.0000 | 0.9859 | 0.9806 |
| `valid_from` timestamps | Hash embedder | 0.9859 | 0.8858 | 0.8887 |
| `valid_from` timestamps | EmbeddingGemma 2 | 1.0000 | 0.8575 | 0.8785 |
| `valid_from` timestamps | EmbeddingGemma 2 + reranker | 1.0000 | 0.9671 | 0.9666 |

Workspace boundary leaks were 0 and the superseded exclusion rate 1.0 in all six runs.

Latency on the same machine: query embedding p95 20 ms (312 ms in an earlier run), reranking 19 candidates p50 313 ms and p95 349 ms, first rerank call 1.6–1.9 s.

## Known Limitations

- The dataset is small and synthetic; the numbers compare retriever configurations and do not predict quality on a personal brain.
- Without the reranker, EmbeddingGemma 2 ranks the first correct document lower on average than the hash embedder does on this dataset (MRR), although it finds all of them within 5.
- For "latest" questions a newer document about the same subject can be placed above the one that answers, including an unverified draft; the date sort does not know provenance.
- The real-model evaluation runs manually on cached weights; CI gates remain deterministic and hash-based.
- Documents inside a workspace are not private per user.

## Distribution Artifacts

- `dist/ltcai-14.4.0-py3-none-any.whl`
- `dist/ltcai-14.4.0.tar.gz`
- `ltcai-14.4.0.tgz`
- `dist/ltcai-14.4.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_14.4.0_aarch64.dmg`
