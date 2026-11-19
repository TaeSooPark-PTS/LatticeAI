//! Concept-cluster and unlinked-pair candidate discovery for synthesis proposals.

use std::collections::BTreeSet;

use serde_json::Value;

use crate::brain_api::pyutil;
use crate::brain_api::quality::content_signature;
use crate::brain_api::sampling;

use super::{COMMON_TOKEN_RATIO, EDGE_SIMILARITY, MIN_CLUSTER_MEMBERS, MIN_SHARED_TOKENS};

pub(super) fn concept_clusters(nodes: &[Value]) -> Vec<Value> {
    let existing: BTreeSet<String> = nodes
        .iter()
        .map(|node| pyutil::field_text(node, "title").trim().to_lowercase())
        .collect();
    let mut index: std::collections::BTreeMap<String, Vec<&Value>> =
        std::collections::BTreeMap::new();
    for node in nodes {
        let text = sampling::node_text(node);
        if text.chars().count() < 3 {
            continue;
        }
        for token in content_signature(&text) {
            index.entry(token).or_default().push(node);
        }
    }
    let ceiling = MIN_CLUSTER_MEMBERS.max((nodes.len() as f64 * COMMON_TOKEN_RATIO) as usize);
    let mut clusters = Vec::new();
    for (token, members) in index {
        if members.len() < MIN_CLUSTER_MEMBERS || members.len() > ceiling {
            continue;
        }
        if existing.contains(&token) {
            continue;
        }
        let listed: Vec<Value> = members
            .iter()
            .take(8)
            .map(|node| {
                serde_json::json!({
                    "id": pyutil::text_of(node.get("id")),
                    "title": title_of(node),
                })
            })
            .collect();
        clusters.push(serde_json::json!({
            "token": token,
            "size": members.len() as i64,
            "members": listed,
        }));
    }
    clusters.sort_by(|left, right| {
        let ls = left.get("size").and_then(Value::as_i64).unwrap_or(0);
        let rs = right.get("size").and_then(Value::as_i64).unwrap_or(0);
        rs.cmp(&ls).then_with(|| {
            pyutil::text_of(left.get("token")).cmp(&pyutil::text_of(right.get("token")))
        })
    });
    clusters
}

pub(super) fn unlinked_pairs(nodes: &[Value], edges: &[Value]) -> Vec<Value> {
    let linked: BTreeSet<String> = edges
        .iter()
        .map(|edge| {
            pair_key(
                "e",
                &pyutil::text_of(edge.get("source")),
                &pyutil::text_of(edge.get("target")),
            )
        })
        .collect();
    let signatures: Vec<(&Value, BTreeSet<String>)> = nodes
        .iter()
        .filter(|node| sampling::node_text(node).chars().count() >= 3)
        .map(|node| (node, content_signature(&sampling::node_text(node))))
        .collect();
    let mut pairs = Vec::new();
    for (index, (left, left_sig)) in signatures.iter().enumerate() {
        for (right, right_sig) in signatures.iter().skip(index + 1) {
            let shared: BTreeSet<_> = left_sig.intersection(right_sig).cloned().collect();
            if shared.len() < MIN_SHARED_TOKENS {
                continue;
            }
            let left_id = pyutil::text_of(left.get("id"));
            let right_id = pyutil::text_of(right.get("id"));
            if linked.contains(&pair_key("e", &left_id, &right_id)) {
                continue;
            }
            let union = left_sig.union(right_sig).count();
            let similarity = shared.len() as f64 / union as f64;
            if similarity < EDGE_SIMILARITY {
                continue;
            }
            let mut shared_tokens: Vec<String> = shared.into_iter().collect();
            shared_tokens.sort();
            shared_tokens.truncate(8);
            pairs.push(serde_json::json!({
                "left": {"id": left_id, "title": title_of(left)},
                "right": {"id": right_id, "title": title_of(right)},
                "source": {"id": left_id, "title": title_of(left)},
                "target": {"id": right_id, "title": title_of(right)},
                "similarity": pyutil::round_to(similarity, 4),
                "shared_tokens": shared_tokens,
            }));
        }
    }
    pairs.sort_by(|left, right| {
        let ls = left
            .get("similarity")
            .and_then(Value::as_f64)
            .unwrap_or(0.0);
        let rs = right
            .get("similarity")
            .and_then(Value::as_f64)
            .unwrap_or(0.0);
        rs.partial_cmp(&ls)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                pyutil::text_of(left.get("left").and_then(|v| v.get("id"))).cmp(&pyutil::text_of(
                    right.get("left").and_then(|v| v.get("id")),
                ))
            })
    });
    pairs
}

pub(super) fn title_of(node: &Value) -> String {
    let title = lattice_core::pytext::strip(&pyutil::field_text(node, "title"));
    if title.is_empty() {
        pyutil::text_of(node.get("id"))
    } else {
        title
    }
}

pub(super) fn pair_key(prefix: &str, left: &str, right: &str) -> String {
    let (a, b) = if left <= right {
        (left, right)
    } else {
        (right, left)
    };
    format!("{prefix}:{a}|{b}")
}
