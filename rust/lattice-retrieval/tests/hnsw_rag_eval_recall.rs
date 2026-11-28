use std::collections::{BTreeMap, HashSet};
use std::path::PathBuf;

use lattice_core::LocalEmbeddingModel;
use lattice_retrieval::native_hnsw::NativeHnswGraph;
use serde::Deserialize;

const K: usize = 10;
const CHURN_ROUNDS: usize = 5;
const RECALL_FLOOR: f64 = 0.90;

#[derive(Debug, Deserialize)]
struct EvalDataset {
    documents: Vec<EvalDocument>,
    queries: Vec<EvalQuery>,
}

#[derive(Debug, Deserialize)]
struct EvalDocument {
    doc_id: String,
    title: String,
    text: String,
}

#[derive(Debug, Deserialize)]
struct EvalQuery {
    query: String,
}

fn load_dataset() -> EvalDataset {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let path = manifest_dir
        .join("..")
        .join("..")
        .join("datasets")
        .join("rag_eval_v1.json");
    let raw = std::fs::read_to_string(&path)
        .unwrap_or_else(|err| panic!("failed to read {}: {err}", path.display()));
    serde_json::from_str(&raw).expect("valid rag_eval_v1.json")
}

fn to_f32(vec: &[f64]) -> Vec<f32> {
    vec.iter().map(|v| *v as f32).collect()
}

fn exact_top_k(corpus: &BTreeMap<String, Vec<f32>>, query: &[f32], k: usize) -> HashSet<String> {
    let mut scored: Vec<(f32, &String)> = corpus
        .iter()
        .map(|(id, vector)| {
            let score: f32 = vector.iter().zip(query).map(|(a, b)| a * b).sum();
            (score, id)
        })
        .collect();
    scored.sort_by(|a, b| b.0.total_cmp(&a.0).then_with(|| a.1.cmp(b.1)));
    scored
        .into_iter()
        .take(k)
        .map(|(_, id)| id.clone())
        .collect()
}

fn measure_recall_at_k(
    graph: &NativeHnswGraph,
    corpus: &BTreeMap<String, Vec<f32>>,
    queries: &[(Vec<f64>, Vec<f32>)],
) -> f64 {
    let k = K.min(corpus.len());
    if k == 0 || queries.is_empty() {
        return 1.0;
    }
    let mut hits = 0usize;
    let mut total = 0usize;
    for (q_f64, q_f32) in queries {
        let approx = graph.query(q_f64, k);
        assert!(approx.iter().all(|id| corpus.contains_key(id)));
        let unique: HashSet<&String> = approx.iter().collect();
        assert_eq!(unique.len(), approx.len());
        let truth = exact_top_k(corpus, q_f32, k);
        hits += approx.iter().filter(|id| truth.contains(*id)).count();
        total += truth.len();
    }
    hits as f64 / total as f64
}

#[test]
fn hnsw_recall_at_10_on_rag_eval_v1_meets_floor_across_ingest_delete_churn() {
    let dataset = load_dataset();
    assert_eq!(dataset.documents.len(), 40);
    assert_eq!(dataset.queries.len(), 320);

    let model = LocalEmbeddingModel::default();
    let dim = model.dim();
    let mut graph = NativeHnswGraph::new(dim);
    let mut corpus: BTreeMap<String, Vec<f32>> = BTreeMap::new();
    let mut doc_records: Vec<(String, Vec<f32>)> = Vec::with_capacity(dataset.documents.len());

    for (idx, doc) in dataset.documents.iter().enumerate() {
        let content = format!("{}\n{}", doc.title, doc.text);
        let vec = to_f32(&model.embed(&content));
        graph.upsert(doc.doc_id.clone(), format!("v0-{idx}"), &vec);
        corpus.insert(doc.doc_id.clone(), vec.clone());
        doc_records.push((doc.doc_id.clone(), vec));
    }

    let queries: Vec<(Vec<f64>, Vec<f32>)> = dataset
        .queries
        .iter()
        .map(|q| {
            let v64 = model.embed(&q.query);
            let v32 = to_f32(&v64);
            (v64, v32)
        })
        .collect();

    let baseline = measure_recall_at_k(&graph, &corpus, &queries);
    let mut round_recalls = Vec::with_capacity(CHURN_ROUNDS);
    let mut version = 1usize;

    for round in 0..CHURN_ROUNDS {
        let remove_start = (round * 7) % doc_records.len();
        let remove_count = 10usize;
        let mut removed = Vec::with_capacity(remove_count);
        for offset in 0..remove_count {
            let idx = (remove_start + offset) % doc_records.len();
            let (id, vec) = &doc_records[idx];
            corpus.remove(id);
            removed.push((id.clone(), vec.clone()));
        }
        let present: HashSet<String> = corpus.keys().cloned().collect();
        let wants_rebuild = graph.remove_missing(&present);

        for (id, vec) in &removed[..5] {
            version += 1;
            let revised: Vec<f32> = vec
                .iter()
                .enumerate()
                .map(|(i, x)| if i % 17 == 0 { x * 0.95 } else { *x })
                .collect();
            graph.upsert(id.clone(), format!("rev-{version}"), &revised);
            corpus.insert(id.clone(), revised);
        }
        for (id, vec) in &removed[5..] {
            version += 1;
            graph.upsert(id.clone(), format!("readd-{version}"), vec);
            corpus.insert(id.clone(), vec.clone());
        }
        if wants_rebuild {
            graph = NativeHnswGraph::new(dim);
            for (id, vec) in &corpus {
                graph.upsert(id.clone(), format!("rebuild-{round}"), vec);
            }
        }
        assert_eq!(graph.active_len(), corpus.len());
        let r = measure_recall_at_k(&graph, &corpus, &queries);
        round_recalls.push(r);
        assert!(r >= RECALL_FLOOR, "round {round} recall@{K} = {r:.4}");
    }

    println!(
        "rag_eval_v1 HNSW recall@{K} (40 docs, 320 queries): baseline={baseline:.4}, churn_rounds={round_recalls:?}"
    );
    assert!(
        baseline >= RECALL_FLOOR,
        "baseline recall@{K} = {baseline:.4}"
    );
}
