use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use lattice_retrieval::rag_eval_chat::{
    embedding_inputs, evaluate_chat_path, evaluate_chat_path_with, process_flags_overridden,
    ChatEvalOptions, RerankRequestRecord, RerankScores,
};
use lattice_retrieval::rag_eval_dataset::{
    load_versioned_dataset, EvalEmbeddings, UpdatedAtMode, VersionedRagDataset,
};
use serde::de::DeserializeOwned;
use serde_json::json;

const DIR_ENV: &str = "LATTICE_NEURAL_EVAL_DIR";
const REPORT_ENV: &str = "LATTICE_CHAT_EVAL_REPORT";
const MODES: [UpdatedAtMode; 2] = [UpdatedAtMode::Constant, UpdatedAtMode::ValidFrom];

fn read_json<T: DeserializeOwned>(path: &Path) -> T {
    let raw = std::fs::read_to_string(path).expect("read eval input");
    serde_json::from_str(&raw).expect("parse eval input")
}

fn write_json(path: &Path, value: &impl serde::Serialize) {
    std::fs::write(
        path,
        serde_json::to_string_pretty(value).expect("encode eval output"),
    )
    .expect("write eval output");
}

fn pinned_dataset() -> VersionedRagDataset {
    assert!(
        process_flags_overridden().is_empty(),
        "unset {:?} to run the pinned chat-path evaluation",
        process_flags_overridden()
    );
    let dataset_path =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../datasets/rag_eval_v1.json");
    load_versioned_dataset(&dataset_path).expect("load rag_eval_v1.json")
}

fn eval_dir() -> PathBuf {
    PathBuf::from(std::env::var(DIR_ENV).expect(DIR_ENV))
}

#[test]
#[ignore = "writes the texts scripts/run_neural_retrieval_eval.py must embed"]
fn the_chat_retriever_embedding_inputs_are_written() {
    let dataset = pinned_dataset();
    write_json(
        &eval_dir().join("embedding_inputs.json"),
        &embedding_inputs(&dataset),
    );
}

#[test]
#[ignore = "needs real model vectors from scripts/run_neural_retrieval_eval.py"]
fn the_chat_retriever_is_measured_with_real_model_vectors() {
    let dir = eval_dir();
    let dataset = pinned_dataset();
    let embeddings: EvalEmbeddings = read_json(&dir.join("embeddings.json"));
    let scores_path = dir.join("rerank_scores.json");
    let scores: Option<BTreeMap<String, RerankScores>> =
        scores_path.is_file().then(|| read_json(&scores_path));
    let mut report = serde_json::Map::new();
    let mut candidates: BTreeMap<String, Vec<RerankRequestRecord>> = BTreeMap::new();
    for mode in MODES {
        let label = format!("{mode:?}");
        let run = |options| evaluate_chat_path_with(&dataset, mode, options).expect("eval");
        let (hash, _) = run(ChatEvalOptions::default());
        let (neural, records) = run(ChatEvalOptions {
            embeddings: Some(&embeddings),
            rerank_scores: None,
        });
        let reranked = scores.as_ref().map(|all| {
            let for_mode = all
                .get(&label)
                .unwrap_or_else(|| panic!("rerank_scores.json has no {label} section"));
            run(ChatEvalOptions {
                embeddings: Some(&embeddings),
                rerank_scores: Some(for_mode),
            })
            .0
        });
        candidates.insert(label.clone(), records);
        report.insert(
            label,
            json!({"hash": hash, "neural": neural, "reranked": reranked}),
        );
    }
    write_json(&dir.join("candidates.json"), &candidates);
    write_json(&dir.join("report.json"), &report);
}

#[test]
#[ignore = "writes the product retriever report for the nightly workflow"]
fn the_chat_retriever_report_is_written_for_the_nightly_workflow() {
    let path = PathBuf::from(std::env::var(REPORT_ENV).expect(REPORT_ENV));
    let dataset = pinned_dataset();
    let report =
        evaluate_chat_path(&dataset, UpdatedAtMode::ValidFrom).expect("chat path evaluation");
    write_json(&path, &report);
}
