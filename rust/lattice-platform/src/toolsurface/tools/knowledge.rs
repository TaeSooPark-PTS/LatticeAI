//! Knowledge garden and Obsidian vault tools.

use std::path::{Path, PathBuf};

use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use lattice_agent::sandbox::MAX_FILE_BYTES;
use lattice_auth::{Identity, OrderedMap};
use serde_json::{json, Value};

use crate::toolsurface::mcp::{
    json_status, parse_json_object, requested_scope, require_user, sha256_hex,
};

use super::gov::KNOWLEDGE_FOLDERS;
use super::{tool_err, tool_ok, ToolExecError, ToolsState};

fn knowledge_root(brain: &Path, workspace_id: &str, email: &str) -> Result<PathBuf, Response> {
    let workspace = workspace_id.trim();
    let user = email.trim().to_lowercase();
    if workspace.is_empty() && user.is_empty() {
        return Ok(brain.to_path_buf());
    }
    if workspace.is_empty() || user.is_empty() {
        return Err(tool_err(
            "Knowledge tools require both workspace_id and user_email.",
        ));
    }
    Ok(brain
        .join(".lattice-scopes")
        .join(scope_digest("workspace", workspace))
        .join(scope_digest("user", &user)))
}

fn scope_digest(kind: &str, value: &str) -> String {
    let hex = sha256_hex(format!("{kind}\0{value}").as_bytes());
    format!("{kind}-{}", &hex[..24])
}

fn scope_of(
    state: &ToolsState,
    headers: &HeaderMap,
    identity: &Identity,
) -> Result<(String, String), Response> {
    if !state.require_auth {
        return Ok((String::new(), String::new()));
    }
    Ok((requested_scope(headers, None), identity.email.clone()))
}

pub(crate) async fn knowledge_save(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let content = parsed.get("content").and_then(Value::as_str).unwrap_or("");
    if content.is_empty() {
        return tool_err("Knowledge content is required.");
    }
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    match save_note(
        &state,
        &ws,
        &email,
        content,
        parsed.get("title").and_then(Value::as_str),
        false,
    ) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(r) => r,
    }
}

fn save_note(
    state: &ToolsState,
    workspace_id: &str,
    email: &str,
    content: &str,
    title: Option<&str>,
    obsidian: bool,
) -> Result<Value, Response> {
    if content.len() as u64 > MAX_FILE_BYTES {
        return Err(tool_err("Knowledge content is too large."));
    }
    let root = knowledge_root(&state.brain_dir, workspace_id, email)?;
    let folder = "00_Raw";
    let target_dir = root.join(folder);
    let _ = std::fs::create_dir_all(&target_dir);
    let mut safe = title.map(str::to_string).unwrap_or_else(|| {
        content
            .trim()
            .lines()
            .next()
            .unwrap_or("note")
            .chars()
            .take(60)
            .collect()
    });
    if safe.is_empty() {
        safe = "note".into();
    }
    safe = safe
        .chars()
        .filter(|ch| ch.is_alphanumeric() || *ch == ' ' || *ch == '-' || *ch == '_')
        .collect();
    safe = safe.split_whitespace().collect::<Vec<_>>().join("_");
    if safe.is_empty() {
        safe = "note".into();
    }
    let mut target = target_dir.join(format!("{safe}.md"));
    let mut counter = 2;
    while target.exists() {
        target = target_dir.join(format!("{safe}_{counter}.md"));
        counter += 1;
    }
    let _ = std::fs::write(&target, content);
    let mut result = json!({
        "folder": folder,
        "filename": target.file_name().unwrap_or_default().to_string_lossy(),
        "path": target.to_string_lossy(),
    });
    if obsidian {
        result["vault_root"] = json!(root.to_string_lossy());
        result["obsidian_uri_hint"] =
            json!(format!("obsidian://open?path={}", target.to_string_lossy()));
    }
    Ok(result)
}

pub(crate) fn run_knowledge_search(
    state: &ToolsState,
    identity: &Identity,
    headers: &HeaderMap,
    args: &Value,
) -> Result<Value, ToolExecError> {
    if !args
        .as_object()
        .map(|o| o.contains_key("query"))
        .unwrap_or(false)
    {
        return Err(ToolExecError::Missing("query"));
    }
    let query = args.get("query").and_then(Value::as_str).unwrap_or("");
    if query.is_empty() {
        return Err(ToolExecError::Message("Query is required.".into()));
    }
    let (ws, email) = scope_of(state, headers, identity)
        .map_err(|_| ToolExecError::Message("Knowledge search failed.".into()))?;
    let max_results = args.get("max_results").and_then(Value::as_u64).unwrap_or(5) as usize;
    let mode = args.get("mode").and_then(Value::as_str);
    let hops = args
        .get("hops")
        .or_else(|| args.get("expand_depth"))
        .and_then(Value::as_u64)
        .map(|v| v as usize);
    let include_graph = args
        .get("include_graph")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    search_notes_with_options(
        state,
        &ws,
        &email,
        query,
        max_results,
        false,
        mode,
        hops,
        include_graph,
    )
    .map_err(|_| ToolExecError::Message("Knowledge search failed.".into()))
}

pub(crate) async fn knowledge_search(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    match run_knowledge_search(&state, &identity, &headers, &parsed) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(error) => error.into_response(&parsed),
    }
}

fn is_hangul_ch(c: char) -> bool {
    ('\u{AC00}'..='\u{D7A3}').contains(&c)
}

fn is_cjk_ch(c: char) -> bool {
    ('\u{4E00}'..='\u{9FFF}').contains(&c) || ('\u{3040}'..='\u{30FF}').contains(&c)
}

fn strip_ko_suffix(tok: &str) -> &str {
    const SUFFIXES: [&str; 22] = [
        "에서", "으로", "에게", "부터", "까지", "처럼", "대한", "관한", "하는", "하고", "하며",
        "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과",
    ];
    let clen = tok.chars().count();
    for s in SUFFIXES {
        if let Some(stripped) = tok.strip_suffix(s) {
            if clen >= s.chars().count() + 2 {
                return stripped;
            }
        }
    }
    tok
}

/// Tokenize note/query text into normalized stems + CJK character bigrams for BM25F vault search.
fn tokenize_vault_bm25(text: &str) -> Vec<(String, f64)> {
    let lower = text.to_lowercase();
    let chars: Vec<char> = lower.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c.is_ascii_alphanumeric() {
            let start = i;
            i += 1;
            while i < chars.len()
                && (chars[i].is_ascii_alphanumeric() || matches!(chars[i], '_' | '-' | '.'))
            {
                i += 1;
            }
            let raw: String = chars[start..i].iter().collect();
            for part in raw.split(['_', '-', '.']) {
                if part.len() >= 2 {
                    out.push((part.to_string(), 1.0));
                }
            }
        } else if is_hangul_ch(c) {
            let start = i;
            i += 1;
            while i < chars.len() && is_hangul_ch(chars[i]) {
                i += 1;
            }
            let raw: String = chars[start..i].iter().collect();
            if i - start >= 2 {
                let stem = strip_ko_suffix(&raw);
                out.push((stem.to_string(), 1.1));
                let sc: Vec<char> = stem.chars().collect();
                if sc.len() >= 2 {
                    for j in 0..sc.len() - 1 {
                        let bg: String = sc[j..j + 2].iter().collect();
                        out.push((format!("ko_bi:{bg}"), 0.65));
                    }
                }
            }
        } else if is_cjk_ch(c) {
            let start = i;
            i += 1;
            while i < chars.len() && is_cjk_ch(chars[i]) {
                i += 1;
            }
            let raw: String = chars[start..i].iter().collect();
            let cc: Vec<char> = raw.chars().collect();
            out.push((raw, 1.05));
            if cc.len() >= 2 {
                for j in 0..cc.len() - 1 {
                    let bg: String = cc[j..j + 2].iter().collect();
                    out.push((format!("cjk_bi:{bg}"), 0.70));
                }
            }
        } else {
            i += 1;
        }
    }
    out
}

fn extract_note_links_and_tags(content: &str) -> (Vec<String>, Vec<String>, String) {
    let mut wikilinks = Vec::new();
    let mut tags = Vec::new();
    let mut headings = Vec::new();

    let mut rest = content;
    while let Some(open) = rest.find("[[") {
        let after = &rest[open + 2..];
        if let Some(close) = after.find("]]") {
            let inner = &after[..close];
            let target = inner
                .split('|')
                .next()
                .unwrap_or(inner)
                .trim()
                .to_lowercase();
            if !target.is_empty() && !wikilinks.contains(&target) {
                wikilinks.push(target);
            }
            rest = &after[close + 2..];
        } else {
            break;
        }
    }

    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('#') && !trimmed.starts_with("##") || trimmed.starts_with("##") {
            let h = trimmed.trim_start_matches('#').trim();
            if !h.is_empty() {
                headings.push(h.to_string());
            }
        }
        for tok in trimmed.split_whitespace() {
            if let Some(tg) = tok.strip_prefix('#') {
                let clean: String = tg
                    .chars()
                    .take_while(|c| {
                        c.is_alphanumeric()
                            || is_hangul_ch(*c)
                            || is_cjk_ch(*c)
                            || matches!(*c, '_' | '-' | '/')
                    })
                    .collect();
                if clean.chars().count() >= 2 {
                    let norm = clean.to_lowercase();
                    if !tags.contains(&norm) {
                        tags.push(norm);
                    }
                }
            }
        }
    }

    (wikilinks, tags, headings.join(" "))
}

#[derive(Clone)]
struct VaultNote {
    path: PathBuf,
    relative_path: String,
    stem_lower: String,
    content: String,
    wikilinks: Vec<String>,
    tags: Vec<String>,
    headings: String,
}

#[allow(clippy::too_many_arguments)]
fn search_notes_with_options(
    state: &ToolsState,
    workspace_id: &str,
    email: &str,
    query: &str,
    max_results: usize,
    obsidian: bool,
    mode: Option<&str>,
    hops: Option<usize>,
    include_graph: bool,
) -> Result<Value, Response> {
    let root = knowledge_root(&state.brain_dir, workspace_id, email)?;
    let max_results = max_results.clamp(1, 20);
    let ql = query.to_lowercase();

    let rich_mode = include_graph
        || hops.is_some()
        || mode
            .map(|m| {
                matches!(
                    m.trim().to_ascii_lowercase().as_str(),
                    "hybrid" | "bm25" | "bm25f" | "graph" | "graphrag" | "subgraph"
                )
            })
            .unwrap_or(false);

    // Default fast path when no graph/hybrid mode was requested: preserve exact walk order
    // if substring matches exist, and fall back to BM25F + CJK bigram + graph expansion when
    // naive substring matching finds nothing (e.g., multi-term or Korean particle queries).
    if !rich_mode {
        let mut results = Vec::new();
        fn walk(dir: &Path, root: &Path, ql: &str, max: usize, out: &mut Vec<Value>) {
            if out.len() >= max {
                return;
            }
            let Ok(rd) = std::fs::read_dir(dir) else {
                return;
            };
            for e in rd.filter_map(|e| e.ok()) {
                if out.len() >= max {
                    return;
                }
                let path = e.path();
                if path.is_dir() {
                    walk(&path, root, ql, max, out);
                    continue;
                }
                if path.extension().and_then(|x| x.to_str()) != Some("md") {
                    continue;
                }
                let Ok(content) = std::fs::read_to_string(&path) else {
                    continue;
                };
                if content.to_lowercase().contains(ql)
                    || path
                        .file_name()
                        .unwrap_or_default()
                        .to_string_lossy()
                        .to_lowercase()
                        .contains(ql)
                {
                    out.push(json!({
                        "path": path.to_string_lossy(),
                        "relative_path": path.strip_prefix(root).unwrap_or(&path).to_string_lossy(),
                        "preview": content.chars().take(500).collect::<String>(),
                    }));
                }
            }
        }
        walk(&root, &root, &ql, max_results, &mut results);
        if !results.is_empty() {
            let mut result = json!({"query": query, "results": results});
            if obsidian {
                result["vault_root"] = json!(root.to_string_lossy());
            }
            return Ok(result);
        }
    }

    // Collect all markdown files in vault for BM25F + Subword Vector + Multi-Hop Graph ranking
    let mut md_files = walkdir_md(&root);
    md_files.sort();
    let mut notes: Vec<VaultNote> = Vec::new();
    for path in md_files {
        let Ok(content) = std::fs::read_to_string(&path) else {
            continue;
        };
        let relative_path = path
            .strip_prefix(&root)
            .unwrap_or(&path)
            .to_string_lossy()
            .into_owned();
        let stem_lower = path
            .file_stem()
            .unwrap_or_default()
            .to_string_lossy()
            .to_lowercase();
        let (wikilinks, tags, headings) = extract_note_links_and_tags(&content);
        notes.push(VaultNote {
            path,
            relative_path,
            stem_lower,
            content,
            wikilinks,
            tags,
            headings,
        });
    }

    let q_terms = tokenize_vault_bm25(query);
    let embedder = lattice_core::FallbackTextEmbedder::default();
    let q_vec = embedder.embed(query);

    let n_docs = (notes.len() as f64).max(1.0);
    let mut title_tfs: Vec<std::collections::HashMap<String, usize>> =
        Vec::with_capacity(notes.len());
    let mut tag_tfs: Vec<std::collections::HashMap<String, usize>> =
        Vec::with_capacity(notes.len());
    let mut body_tfs: Vec<std::collections::HashMap<String, usize>> =
        Vec::with_capacity(notes.len());
    let mut body_lens: Vec<f64> = Vec::with_capacity(notes.len());

    for note in &notes {
        let mut t_map = std::collections::HashMap::new();
        for (tok, _) in tokenize_vault_bm25(&note.stem_lower) {
            *t_map.entry(tok).or_insert(0) += 1;
        }
        let tag_str = format!(
            "{} {} {}",
            note.headings,
            note.wikilinks.join(" "),
            note.tags.join(" ")
        );
        let mut tg_map = std::collections::HashMap::new();
        for (tok, _) in tokenize_vault_bm25(&tag_str) {
            *tg_map.entry(tok).or_insert(0) += 1;
        }
        let mut b_map = std::collections::HashMap::new();
        let b_tokens = tokenize_vault_bm25(&note.content);
        body_lens.push((b_tokens.len() as f64).max(1.0));
        for (tok, _) in b_tokens {
            *b_map.entry(tok).or_insert(0) += 1;
        }
        title_tfs.push(t_map);
        tag_tfs.push(tg_map);
        body_tfs.push(b_map);
    }

    let avgdl = if body_lens.is_empty() {
        1.0
    } else {
        (body_lens.iter().sum::<f64>() / n_docs).max(1.0)
    };

    let k1 = 1.2f64;
    let b = 0.75f64;
    let mut bm25_scores = vec![0.0f64; notes.len()];
    for (qtok, qboost) in &q_terms {
        let mut df = 0usize;
        for idx in 0..notes.len() {
            if title_tfs[idx].contains_key(qtok)
                || tag_tfs[idx].contains_key(qtok)
                || body_tfs[idx].contains_key(qtok)
            {
                df += 1;
            }
        }
        if df == 0 {
            continue;
        }
        let df_f = df as f64;
        let idf = (1.0 + (n_docs - df_f + 0.5) / (df_f + 0.5)).ln().max(0.10);
        for idx in 0..notes.len() {
            let tf_t = *title_tfs[idx].get(qtok).unwrap_or(&0) as f64;
            let tf_tg = *tag_tfs[idx].get(qtok).unwrap_or(&0) as f64;
            let tf_b = *body_tfs[idx].get(qtok).unwrap_or(&0) as f64;
            let norm_b = (1.0 - b) + b * (body_lens[idx] / avgdl);
            let pseudo_tf = 3.0 * tf_t + 2.5 * tf_tg + (1.0 * tf_b / norm_b);
            if pseudo_tf > 0.0 {
                bm25_scores[idx] += qboost * idf * (pseudo_tf * (k1 + 1.0)) / (pseudo_tf + k1);
            }
        }
    }

    let mut vec_scores = vec![0.0f64; notes.len()];
    if q_vec.iter().any(|v| *v != 0.0) {
        for (idx, note) in notes.iter().enumerate() {
            let d_vec = embedder.embed(&format!("{} {}", note.stem_lower, note.content));
            let sim = embedder.similarity(&q_vec, &d_vec).unwrap_or(0.0);
            if sim > 0.10 {
                vec_scores[idx] = sim;
            }
        }
    }

    // Build Vault Graph edges (wikilinks + shared tags) and propagate 1-hop / 2-hop scores
    let max_hops = hops.unwrap_or(2).clamp(1, 2);
    let mut adj: Vec<Vec<(usize, f64, String)>> = vec![Vec::new(); notes.len()];
    let mut subgraph_edges: Vec<Value> = Vec::new();
    for i in 0..notes.len() {
        for j in (i + 1)..notes.len() {
            let mut linked = false;
            let stem_i = notes[i].stem_lower.replace('_', " ");
            let stem_j = notes[j].stem_lower.replace('_', " ");
            if notes[i]
                .wikilinks
                .iter()
                .any(|w| *w == notes[j].stem_lower || *w == stem_j)
                || notes[j]
                    .wikilinks
                    .iter()
                    .any(|w| *w == notes[i].stem_lower || *w == stem_i)
            {
                adj[i].push((j, 0.85, "WIKILINK".into()));
                adj[j].push((i, 0.85, "WIKILINK".into()));
                subgraph_edges.push(json!({
                    "source": notes[i].relative_path,
                    "target": notes[j].relative_path,
                    "relation": "WIKILINK",
                }));
                linked = true;
            }
            if !linked {
                for tg in &notes[i].tags {
                    if notes[j].tags.contains(tg) {
                        adj[i].push((j, 0.55, format!("TAG:#{tg}")));
                        adj[j].push((i, 0.55, format!("TAG:#{tg}")));
                        subgraph_edges.push(json!({
                            "source": notes[i].relative_path,
                            "target": notes[j].relative_path,
                            "relation": format!("TAG:#{tg}"),
                        }));
                        break;
                    }
                }
            }
        }
    }

    let max_bm25 = bm25_scores.iter().copied().fold(0.0f64, f64::max).max(1e-6);
    let mut graph_scores = vec![0.0f64; notes.len()];
    let mut note_hops: Vec<Option<usize>> = vec![None; notes.len()];
    let mut note_relations: Vec<Vec<String>> = vec![Vec::new(); notes.len()];

    for i in 0..notes.len() {
        let seed_strength = (bm25_scores[i] / max_bm25).max(vec_scores[i] * 0.7);
        if seed_strength <= 0.05 {
            continue;
        }
        note_hops[i] = Some(0);
        for &(nbr1, w1, ref rel1) in &adj[i] {
            let hop1_contrib = seed_strength * w1 * 0.68;
            graph_scores[nbr1] += hop1_contrib;
            note_hops[nbr1] = Some(note_hops[nbr1].map_or(1, |h| h.min(1)));
            if !note_relations[nbr1].contains(rel1) {
                note_relations[nbr1].push(rel1.clone());
            }
            if max_hops >= 2 {
                for &(nbr2, w2, ref rel2) in &adj[nbr1] {
                    if nbr2 == i {
                        continue;
                    }
                    let hop2_contrib = hop1_contrib * w2 * 0.38;
                    graph_scores[nbr2] += hop2_contrib;
                    note_hops[nbr2] = Some(note_hops[nbr2].map_or(2, |h| h.min(2)));
                    let chain = format!("{rel1}->{rel2}");
                    if !note_relations[nbr2].contains(&chain) {
                        note_relations[nbr2].push(chain);
                    }
                }
            }
        }
    }

    // Compute 3-way RRF + normalized blend across BM25F, Vector, and Graph channels
    let rrf_ranks = |scores: &[f64]| -> Vec<Option<usize>> {
        let mut order: Vec<(usize, f64)> = scores
            .iter()
            .copied()
            .enumerate()
            .filter(|(_, s)| *s > 0.0)
            .collect();
        order.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
        let mut ranks = vec![None; scores.len()];
        for (pos, (idx, _)) in order.into_iter().enumerate() {
            ranks[idx] = Some(pos + 1);
        }
        ranks
    };

    let bm25_ranks = rrf_ranks(&bm25_scores);
    let vec_ranks = rrf_ranks(&vec_scores);
    let graph_ranks = rrf_ranks(&graph_scores);
    let max_graph = graph_scores
        .iter()
        .copied()
        .fold(0.0f64, f64::max)
        .max(1e-6);

    let mut ranked: Vec<(f64, usize)> = Vec::new();
    for idx in 0..notes.len() {
        let r_b = bm25_ranks[idx]
            .map(|r| 0.45 * 61.0 / (60.0 + r as f64))
            .unwrap_or(0.0);
        let r_v = vec_ranks[idx]
            .map(|r| 0.30 * 61.0 / (60.0 + r as f64))
            .unwrap_or(0.0);
        let r_g = graph_ranks[idx]
            .map(|r| 0.25 * 61.0 / (60.0 + r as f64))
            .unwrap_or(0.0);
        let rrf = r_b + r_v + r_g;
        if rrf <= 0.0 {
            continue;
        }
        let norm_blend = 0.45 * (bm25_scores[idx] / max_bm25)
            + 0.30 * vec_scores[idx]
            + 0.25 * (graph_scores[idx] / max_graph);
        ranked.push((0.55 * rrf + 0.45 * norm_blend, idx));
    }
    ranked.sort_by(|a, b| b.0.total_cmp(&a.0).then_with(|| a.1.cmp(&b.1)));

    let mut results = Vec::new();
    let mut subgraph_nodes = Vec::new();
    for (score, idx) in ranked.into_iter().take(max_results) {
        let note = &notes[idx];
        let mut item = json!({
            "path": note.path.to_string_lossy(),
            "relative_path": note.relative_path,
            "preview": note.content.chars().take(500).collect::<String>(),
        });
        if rich_mode {
            item["score"] = json!(lattice_core::round6(score));
            item["bm25_score"] = json!(lattice_core::round6(bm25_scores[idx]));
            item["vector_score"] = json!(lattice_core::round6(vec_scores[idx]));
            item["graph_score"] = json!(lattice_core::round6(graph_scores[idx]));
            item["wikilinks"] = json!(note.wikilinks);
            item["tags"] = json!(note.tags);
            item["graph_hop"] = json!(note_hops[idx]);
            item["graph_relations"] = json!(note_relations[idx]);
            subgraph_nodes.push(json!({
                "id": note.relative_path,
                "title": note.stem_lower,
                "hop": note_hops[idx],
                "tags": note.tags,
            }));
        }
        results.push(item);
    }

    let mut result = json!({"query": query, "results": results});
    if obsidian {
        result["vault_root"] = json!(root.to_string_lossy());
    }
    if rich_mode {
        result["mode"] = json!(mode.unwrap_or("hybrid"));
        result["subgraph"] = json!({
            "hops": max_hops,
            "nodes": subgraph_nodes,
            "edges": subgraph_edges,
        });
    }
    Ok(result)
}

pub(crate) fn run_knowledge_tree(
    state: &ToolsState,
    identity: &Identity,
    headers: &HeaderMap,
) -> Result<Value, ToolExecError> {
    let (ws, email) = scope_of(state, headers, identity)
        .map_err(|_| ToolExecError::Message("Knowledge tree failed.".into()))?;
    tree_notes(state, &ws, &email)
        .map_err(|_| ToolExecError::Message("Knowledge tree failed.".into()))
}

pub(crate) async fn knowledge_tree(
    State(state): State<ToolsState>,
    headers: HeaderMap,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    match run_knowledge_tree(&state, &identity, &headers) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(error) => error.into_response(&json!({})),
    }
}

fn tree_notes(state: &ToolsState, workspace_id: &str, email: &str) -> Result<Value, Response> {
    let root = knowledge_root(&state.brain_dir, workspace_id, email)?;
    let mut entries = Vec::new();
    for folder in KNOWLEDGE_FOLDERS {
        let dir = root.join(folder);
        let _ = std::fs::create_dir_all(&dir);
        let mut files: Vec<PathBuf> = walkdir_md(&dir);
        files.sort();
        for file in files {
            entries.push(json!({
                "folder": folder,
                "relative_path": file.strip_prefix(&root).unwrap_or(&file).to_string_lossy(),
                "size": file.metadata().map(|m| m.len()).unwrap_or(0),
            }));
        }
    }
    Ok(json!({"root": root.to_string_lossy(), "entries": entries}))
}

fn walkdir_md(dir: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    fn rec(dir: &Path, out: &mut Vec<PathBuf>) {
        let Ok(rd) = std::fs::read_dir(dir) else {
            return;
        };
        for e in rd.filter_map(|e| e.ok()) {
            let p = e.path();
            if p.is_dir() {
                rec(&p, out);
            } else if p.extension().and_then(|x| x.to_str()) == Some("md") {
                out.push(p);
            }
        }
    }
    rec(dir, &mut out);
    out
}

pub(crate) async fn obsidian_save(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let content = parsed.get("content").and_then(Value::as_str).unwrap_or("");
    if content.is_empty() {
        return tool_err("Knowledge content is required.");
    }
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    match save_note(
        &state,
        &ws,
        &email,
        content,
        parsed.get("title").and_then(Value::as_str),
        true,
    ) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(r) => r,
    }
}

pub(crate) async fn obsidian_search(
    State(state): State<ToolsState>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let parsed = match parse_json_object(&body) {
        Ok(v) => v,
        Err(r) => return r,
    };
    let query = parsed.get("query").and_then(Value::as_str).unwrap_or("");
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    let max_results = parsed
        .get("max_results")
        .and_then(Value::as_u64)
        .unwrap_or(5) as usize;
    let mode = parsed.get("mode").and_then(Value::as_str);
    let hops = parsed
        .get("hops")
        .or_else(|| parsed.get("expand_depth"))
        .and_then(Value::as_u64)
        .map(|v| v as usize);
    let include_graph = parsed
        .get("include_graph")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    match search_notes_with_options(
        &state,
        &ws,
        &email,
        query,
        max_results,
        true,
        mode,
        hops,
        include_graph,
    ) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(r) => r,
    }
}

pub(crate) async fn obsidian_tree(State(state): State<ToolsState>, headers: HeaderMap) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    match tree_notes(&state, &ws, &email) {
        Ok(v) => tool_ok(&state.workspace, v),
        Err(r) => r,
    }
}

pub(crate) async fn obsidian_status(
    State(state): State<ToolsState>,
    headers: HeaderMap,
) -> Response {
    let identity = match require_user(&state.auth, &headers) {
        Ok(id) => id,
        Err(r) => return r,
    };
    let (ws, email) = match scope_of(&state, &headers, &identity) {
        Ok(s) => s,
        Err(r) => return r,
    };
    let root = match knowledge_root(&state.brain_dir, &ws, &email) {
        Ok(p) => p,
        Err(r) => return r,
    };
    let folders: Vec<String> = if root.exists() {
        std::fs::read_dir(&root)
            .map(|rd| {
                rd.filter_map(|e| e.ok())
                    .filter(|e| e.path().is_dir())
                    .filter_map(|e| e.file_name().into_string().ok())
                    .collect()
            })
            .unwrap_or_default()
    } else {
        Vec::new()
    };
    let ocr = which("tesseract");
    let mut body = OrderedMap::new();
    body.insert("status", json!("ok"));
    body.insert("vault_root", json!(root.to_string_lossy()));
    body.insert("folders", json!(folders));
    body.insert("ocr_engine", json!(ocr));
    json_status(StatusCode::OK, &body)
}

fn which(name: &str) -> Option<String> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let candidate = dir.join(name);
        if candidate.is_file() {
            return Some(candidate.to_string_lossy().into_owned());
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use lattice_agent::sandbox::Workspace;
    use lattice_auth::{AuthConfig, AuthState, Clock};
    use std::collections::HashMap;

    #[test]
    fn hybrid_note_search_surfaces_bm25f_and_wikilink_graph_subgraph() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let mut config = AuthConfig::from_map(&HashMap::new(), None);
        config.data_dir = tmp.path().to_path_buf();
        let auth = AuthState::with_clock(config, Clock::frozen(1_700_000_000.0));
        let workspace = Workspace::new(tmp.path()).expect("workspace");
        let state = ToolsState::new(auth, workspace, tmp.path());
        save_note(
            &state,
            "personal",
            "user@example.com",
            "# 하이브리드 검색 설계\n한국어 BM25F 및 그래프 확장을 지원합니다. [[GraphRAG 서브그래프 노트]] 참고 #graphrag #bm25f",
            Some("하이브리드 검색 설계"),
            false,
        )
        .expect("save seed note");
        save_note(
            &state,
            "personal",
            "user@example.com",
            "# GraphRAG 서브그래프 노트\n1홉 및 2홉 위키링크 이웃 문서를 RRF로 결합합니다. #graphrag",
            Some("GraphRAG 서브그래프 노트"),
            false,
        )
        .expect("save neighbor note");

        let report = search_notes_with_options(
            &state,
            "personal",
            "user@example.com",
            "하이브리드에서 BM25F",
            5,
            false,
            Some("hybrid"),
            Some(2),
            true,
        )
        .expect("hybrid search");

        assert_eq!(report["mode"], "hybrid");
        let results = report["results"].as_array().expect("results array");
        assert!(
            results.len() >= 2,
            "Both seed note and 1-hop wikilink/tag neighbor should be returned: {results:?}"
        );
        assert!(results[0]["bm25_score"].as_f64().unwrap_or(0.0) > 0.0);
        assert!(
            results.iter().any(|r| {
                r["relative_path"]
                    .as_str()
                    .unwrap_or("")
                    .contains("GraphRAG")
                    && r["graph_score"].as_f64().unwrap_or(0.0) > 0.0
            }),
            "1-hop neighbor should receive positive graph_score: {results:?}"
        );
        let subgraph = &report["subgraph"];
        assert_eq!(subgraph["hops"], 2);
        assert!(!subgraph["nodes"].as_array().unwrap().is_empty());
        assert!(!subgraph["edges"].as_array().unwrap().is_empty());
    }
}
