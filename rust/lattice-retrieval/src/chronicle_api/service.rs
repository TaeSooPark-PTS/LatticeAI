//! `ChronicleService` — three answers, all re-arrangements of stored rows.
//!
//! Ported field for field from `latticeai/services/chronicle.py`, including the
//! parts that look like details and are not:
//!
//! * **Key order.** `totals` is `sources, entities, connections, conversations`
//!   (the `_LANES` tuple), `counts` is `sources, entities, conversations,
//!   changes` (the literal in `day()`), and a series bucket puts `date` first.
//!   Alphabetical would be a different answer; see [`super::json`].
//! * **`superseded_by` is read for truth, not for presence.** `"fact_superseded"
//!   if row["superseded_by"] else "fact_retired"` — an empty string is
//!   `fact_retired`, which `is not None` would have called `fact_superseded`.
//!   The same trap sits in `float(importance_score or 0.0)`, where a stored `0`
//!   and a stored `NULL` must answer the same `0.0`.
//! * **Sparse series.** Only days that carry something appear; a two-year gap
//!   is a gap, not 700 rows of zeros.
//! * **Truncation is by character.** `_preview` slices `[:139]` on a Python
//!   `str`, so a Korean preview keeps 139 syllables rather than 139 bytes.

use std::collections::{BTreeMap, BTreeSet};

use lattice_core::CoreError;
use rusqlite::Connection;
use serde_json::Value;

use super::json::{object, Ordered};
use super::pytime::{self, Naive};
use super::store::{self, py_str, py_str_or_empty, py_truthy};

/// `_PREVIEW_LIMIT` — conversation previews are one short, plain-text line.
pub const PREVIEW_LIMIT: usize = 140;
/// `_GROUP_LIMIT` — one busy day cannot return an unbounded payload.
pub const GROUP_LIMIT: usize = 200;
/// `_TOP_ENTITY_LIMIT` — "what was important then" is a short list.
pub const TOP_ENTITY_LIMIT: usize = 12;
/// `_AS_OF_LIMIT` — the ceiling `store.as_of()` is willing to return.
pub const AS_OF_LIMIT: i64 = 2_000;

/// `_LANES`, in the order `_empty_lanes()` writes them.
pub const LANES: [&str; 4] = ["sources", "entities", "connections", "conversations"];

// ── shaping ─────────────────────────────────────────────────────────────────

fn tag_pattern() -> &'static fancy_regex::Regex {
    static TAG: std::sync::OnceLock<fancy_regex::Regex> = std::sync::OnceLock::new();
    TAG.get_or_init(|| crate::build_pattern(r"<[^>]*>"))
}

/// `str.splitlines()` — every boundary CPython recognises, not just `\n`.
fn splitlines(text: &str) -> Vec<String> {
    let mut lines: Vec<String> = Vec::new();
    let mut current = String::new();
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        let boundary = matches!(
            c,
            '\n' | '\r'
                | '\u{0b}'
                | '\u{0c}'
                | '\u{1c}'
                | '\u{1d}'
                | '\u{1e}'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        );
        if !boundary {
            current.push(c);
            continue;
        }
        if c == '\r' && chars.peek() == Some(&'\n') {
            chars.next();
        }
        lines.push(std::mem::take(&mut current));
    }
    if !current.is_empty() {
        lines.push(current);
    }
    lines
}

/// `_preview` — first non-empty line, tags stripped, whitespace collapsed.
pub fn preview(text: &Value) -> String {
    let raw = py_str_or_empty(text);
    let flat = tag_pattern().replace_all(&raw, " ");
    let first = splitlines(&flat)
        .into_iter()
        .map(|line| lattice_core::pytext::strip(&line))
        .find(|line| !line.is_empty())
        .unwrap_or_default();
    // `" ".join(first.split())` is exactly `_clean_text`.
    let collapsed = lattice_core::clean_text(&first);
    if collapsed.chars().count() > PREVIEW_LIMIT {
        let head: String = collapsed.chars().take(PREVIEW_LIMIT - 1).collect();
        return format!("{}…", lattice_core::pytext::rstrip(&head));
    }
    collapsed
}

/// `_on_day(rows, day)` for one row's `at` cell.
fn on_day(at: &Value, day: &str) -> bool {
    pytime::day_of(&py_str_or_empty(at)).as_deref() == Some(day)
}

/// `_conversation_cards` — one card per conversation that spoke on the day.
fn conversation_cards(rows: &[store::MessageRow]) -> Vec<Ordered> {
    // `dict.setdefault` keeps first-seen order, and the rows arrive `ORDER BY
    // id ASC`, so the cards come out in the order the day's conversations
    // started.
    let mut order: Vec<String> = Vec::new();
    let mut grouped: BTreeMap<String, Vec<&store::MessageRow>> = BTreeMap::new();
    for row in rows {
        let key = py_str(&row.conversation_id);
        if !grouped.contains_key(&key) {
            order.push(key.clone());
        }
        grouped.entry(key).or_default().push(row);
    }
    order
        .into_iter()
        .filter_map(|key| {
            let items = grouped.get(&key)?;
            let lead = items
                .iter()
                .find(|item| item.role == Value::String("user".to_string()))
                .unwrap_or(items.first()?);
            Some(object([
                ("conversation_id", key.clone().into()),
                ("preview", preview(&lead.content).into()),
                ("messages", items.len().into()),
                ("started_at", items.first()?.at.clone().into()),
            ]))
        })
        .collect()
}

/// `_change_cards` — what stopped being true, facts first then relationships.
fn change_cards(nodes: &[store::ChangedNodeRow], edges: &[store::ChangedEdgeRow]) -> Vec<Ordered> {
    let mut cards: Vec<(String, String, Ordered)> = Vec::new();
    for row in nodes {
        let kind = if py_truthy(&row.superseded_by) {
            "fact_superseded"
        } else {
            "fact_retired"
        };
        cards.push((
            py_str(&row.at),
            py_str(&row.id),
            object([
                ("kind", kind.into()),
                ("label", row.label.clone().into()),
                ("at", row.at.clone().into()),
                ("node_id", row.id.clone().into()),
            ]),
        ));
    }
    for row in edges {
        let kind = if py_truthy(&row.superseded_by) {
            "connection_superseded"
        } else {
            "connection_ended"
        };
        // The f-string calls `str()` on both labels, so a NULL label reads
        // "None" rather than emptying the arrow.
        let label = format!(
            "{} → {}",
            py_str(&row.source_label),
            py_str(&row.target_label)
        );
        cards.push((
            py_str(&row.at),
            py_str(&row.node_id),
            object([
                ("kind", kind.into()),
                ("label", label.into()),
                ("at", row.at.clone().into()),
                ("node_id", row.node_id.clone().into()),
            ]),
        ));
    }
    // `sort` is stable in both languages, and the key is the same pair.
    cards.sort_by(|left, right| (&left.0, &left.1).cmp(&(&right.0, &right.1)));
    cards.into_iter().map(|(_, _, card)| card).collect()
}

fn observe(earliest: &mut Option<Naive>, latest: &mut Option<Naive>, moment: Naive) {
    if earliest.map(|value| moment < value).unwrap_or(true) {
        *earliest = Some(moment);
    }
    if latest.map(|value| moment > value).unwrap_or(true) {
        *latest = Some(moment);
    }
}

// ── the three answers ───────────────────────────────────────────────────────

/// `ChronicleService.overview` — totals, endpoints, and one bucket per day.
pub fn overview(
    conn: &Connection,
    graph: bool,
    user_email: &str,
    workspace: Option<&str>,
) -> Ordered {
    let mut days: BTreeMap<String, [i64; 4]> = BTreeMap::new();
    let mut totals = [0_i64; 4];
    let (mut earliest, mut latest) = (None, None);

    let lanes: [Vec<Value>; 3] = [
        store::sources(conn, graph, workspace)
            .into_iter()
            .map(|row| row.at)
            .collect(),
        store::entities(conn, graph, workspace)
            .into_iter()
            .map(|row| row.at)
            .collect(),
        store::connections(conn, graph, workspace)
            .into_iter()
            .map(|row| row.at)
            .collect(),
    ];
    for (lane, stamps) in lanes.into_iter().enumerate() {
        for stamp in stamps {
            let Some(moment) = pytime::moment(&py_str_or_empty(&stamp)) else {
                continue;
            };
            observe(&mut earliest, &mut latest, moment);
            totals[lane] += 1;
            days.entry(moment.date_iso()).or_insert([0; 4])[lane] += 1;
        }
    }

    let mut per_day: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    for row in store::messages(conn, user_email, workspace) {
        let Some(moment) = pytime::moment(&py_str_or_empty(&row.at)) else {
            continue;
        };
        observe(&mut earliest, &mut latest, moment);
        let key = moment.date_iso();
        days.entry(key.clone()).or_insert([0; 4]);
        let conversation = py_str(&row.conversation_id);
        per_day.entry(key).or_default().insert(conversation.clone());
        seen.insert(conversation);
    }
    for (key, conversations) in &per_day {
        if let Some(bucket) = days.get_mut(key) {
            bucket[3] = conversations.len() as i64;
        }
    }
    totals[3] = seen.len() as i64;

    let series: Vec<Ordered> = days
        .iter()
        .map(|(date, counts)| {
            Ordered::Object(
                std::iter::once(("date".to_string(), Ordered::from(date.clone())))
                    .chain(
                        LANES
                            .iter()
                            .zip(counts)
                            .map(|(lane, count)| (lane.to_string(), Ordered::from(*count))),
                    )
                    .collect(),
            )
        })
        .collect();

    object([
        (
            "first_activity_at",
            earliest.map(|moment| moment.iso_seconds()).into(),
        ),
        (
            "last_activity_at",
            latest.map(|moment| moment.iso_seconds()).into(),
        ),
        (
            "totals",
            Ordered::Object(
                LANES
                    .iter()
                    .zip(totals)
                    .map(|(lane, count)| (lane.to_string(), Ordered::from(count)))
                    .collect(),
            ),
        ),
        ("series", series.into()),
    ])
}

/// `ChronicleService.day` — the day is already through `parse_day`.
pub fn day(
    conn: &Connection,
    graph: bool,
    day: &str,
    user_email: &str,
    workspace: Option<&str>,
) -> Ordered {
    let sources: Vec<store::SourceRow> = store::sources(conn, graph, workspace)
        .into_iter()
        .filter(|row| on_day(&row.at, day))
        .collect();
    let entities: Vec<store::EntityRow> = store::entities(conn, graph, workspace)
        .into_iter()
        .filter(|row| on_day(&row.at, day))
        .collect();
    let messages: Vec<store::MessageRow> = store::messages(conn, user_email, workspace)
        .into_iter()
        .filter(|row| on_day(&row.at, day))
        .collect();
    let conversations = conversation_cards(&messages);
    let changed_nodes: Vec<store::ChangedNodeRow> = store::changed_nodes(conn, graph, workspace)
        .into_iter()
        .filter(|row| on_day(&row.at, day))
        .collect();
    let changed_edges: Vec<store::ChangedEdgeRow> = store::changed_edges(conn, graph, workspace)
        .into_iter()
        .filter(|row| on_day(&row.at, day))
        .collect();
    let changes = change_cards(&changed_nodes, &changed_edges);

    let source_cards: Vec<Ordered> = sources
        .iter()
        .take(GROUP_LIMIT)
        .map(|row| {
            object([
                ("id", row.id.clone().into()),
                ("title", row.title.clone().into()),
                ("source_type", row.source_type.clone().into()),
                ("captured_at", row.at.clone().into()),
                ("node_id", row.node_id.clone().into()),
            ])
        })
        .collect();
    let entity_cards: Vec<Ordered> = entities
        .iter()
        .take(GROUP_LIMIT)
        .map(|row| {
            object([
                ("id", row.id.clone().into()),
                ("label", row.label.clone().into()),
                ("type", row.kind.clone().into()),
                ("created_at", row.at.clone().into()),
            ])
        })
        .collect();

    object([
        ("date", day.into()),
        (
            "counts",
            object([
                ("sources", sources.len().into()),
                ("entities", entities.len().into()),
                ("conversations", conversations.len().into()),
                ("changes", changes.len().into()),
            ]),
        ),
        (
            "groups",
            object([
                ("sources", source_cards.into()),
                ("entities", entity_cards.into()),
                (
                    "conversations",
                    conversations
                        .into_iter()
                        .take(GROUP_LIMIT)
                        .collect::<Vec<_>>()
                        .into(),
                ),
                (
                    "changes",
                    changes
                        .into_iter()
                        .take(GROUP_LIMIT)
                        .collect::<Vec<_>>()
                        .into(),
                ),
            ]),
        ),
    ])
}

/// `ChronicleService.as_of` — the stamp is already through `parse_timestamp`.
pub fn as_of(
    conn: &Connection,
    graph: bool,
    stamp: &str,
    workspace: Option<&str>,
) -> Result<Ordered, CoreError> {
    if !graph {
        return Ok(empty_as_of(stamp));
    }
    let window = store::as_of(conn, stamp, AS_OF_LIMIT, workspace)?;
    let ids: Vec<String> = window.nodes.iter().map(|node| py_str(&node.id)).collect();
    let scores = store::importance(conn, &ids)?;
    let mut ranked: Vec<&store::AsOfNode> = window.nodes.iter().collect();
    ranked.sort_by(|left, right| {
        let (left_id, right_id) = (py_str(&left.id), py_str(&right.id));
        let left_score = -scores.get(&left_id).copied().unwrap_or(0.0);
        let right_score = -scores.get(&right_id).copied().unwrap_or(0.0);
        left_score
            .partial_cmp(&right_score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| left_id.cmp(&right_id))
    });
    let top: Vec<Ordered> = ranked
        .into_iter()
        .take(TOP_ENTITY_LIMIT)
        .map(|node| {
            let score = scores.get(&py_str(&node.id)).copied().unwrap_or(0.0);
            object([
                ("id", node.id.clone().into()),
                ("label", node.title.clone().into()),
                ("type", node.kind.clone().into()),
                ("importance_score", score.into()),
            ])
        })
        .collect();
    Ok(object([
        ("ts", stamp.into()),
        (
            "stats",
            object([
                ("entities", window.node_count.into()),
                ("connections", window.edge_count.into()),
            ]),
        ),
        ("top_entities", top.into()),
    ]))
}

/// The answer a graph-disabled Brain gives — zeros, not a refusal.
fn empty_as_of(stamp: &str) -> Ordered {
    object([
        ("ts", stamp.into()),
        (
            "stats",
            object([("entities", 0_i64.into()), ("connections", 0_i64.into())]),
        ),
        ("top_entities", Vec::new().into()),
    ])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(value: &str) -> Value {
        Value::String(value.to_string())
    }

    fn render(value: &Ordered) -> String {
        serde_json::to_string(value).expect("render")
    }

    #[test]
    fn a_preview_is_one_plain_line_cut_by_characters() {
        assert_eq!(preview(&text("<b>hi</b> there")), "hi there");
        assert_eq!(preview(&text("\n\n  second\nthird")), "second");
        // `splitlines` breaks on more than `\n`; a vertical tab is a new line.
        assert_eq!(preview(&text("\u{0b}alpha\u{0b}beta")), "alpha");
        assert_eq!(preview(&text("  a   b\t c ")), "a b c");
        assert_eq!(preview(&Value::Null), "");
        assert_eq!(preview(&Value::from(0)), "");
        let long = "가".repeat(200);
        let cut = preview(&text(&long));
        assert_eq!(cut.chars().count(), PREVIEW_LIMIT);
        assert!(cut.ends_with('…'));
        assert_eq!(preview(&text(&"가".repeat(140))).chars().count(), 140);
    }

    #[test]
    fn conversation_cards_lead_with_the_question_and_keep_first_seen_order() {
        let rows = vec![
            store::MessageRow {
                conversation_id: text("c2"),
                role: text("assistant"),
                content: text("answered first"),
                at: text("2026-08-11T09:00:00"),
            },
            store::MessageRow {
                conversation_id: text("c1"),
                role: text("assistant"),
                content: text("an answer"),
                at: text("2026-08-11T09:01:00"),
            },
            store::MessageRow {
                conversation_id: text("c1"),
                role: text("user"),
                content: text("a question"),
                at: text("2026-08-11T09:02:00"),
            },
        ];
        assert_eq!(
            render(&Ordered::Array(conversation_cards(&rows))),
            "[{\"conversation_id\":\"c2\",\"preview\":\"answered first\",\"messages\":1,\
             \"started_at\":\"2026-08-11T09:00:00\"},\
             {\"conversation_id\":\"c1\",\"preview\":\"a question\",\"messages\":2,\
             \"started_at\":\"2026-08-11T09:01:00\"}]"
        );
    }

    #[test]
    fn a_change_card_reads_superseded_by_for_truth_not_for_presence() {
        let nodes = vec![
            store::ChangedNodeRow {
                id: text("n2"),
                label: text("retired"),
                // The trap: `""` is falsy, so this is *retired*, not superseded.
                superseded_by: text(""),
                at: text("2026-08-11T10:00:00"),
            },
            store::ChangedNodeRow {
                id: text("n1"),
                label: text("replaced"),
                superseded_by: text("n9"),
                at: text("2026-08-11T10:00:00"),
            },
        ];
        let edges = vec![store::ChangedEdgeRow {
            node_id: text("e-src"),
            source_label: text("A"),
            target_label: Value::Null,
            superseded_by: Value::Null,
            at: text("2026-08-11T09:00:00"),
        }];
        let cards = change_cards(&nodes, &edges);
        assert_eq!(
            render(&Ordered::Array(cards)),
            "[{\"kind\":\"connection_ended\",\"label\":\"A → None\",\
             \"at\":\"2026-08-11T09:00:00\",\"node_id\":\"e-src\"},\
             {\"kind\":\"fact_superseded\",\"label\":\"replaced\",\
             \"at\":\"2026-08-11T10:00:00\",\"node_id\":\"n1\"},\
             {\"kind\":\"fact_retired\",\"label\":\"retired\",\
             \"at\":\"2026-08-11T10:00:00\",\"node_id\":\"n2\"}]"
        );
    }

    #[test]
    fn a_graph_disabled_brain_rewinds_to_zeros_rather_than_refusing() {
        assert_eq!(
            render(&empty_as_of("2026-08-11T09:00:00")),
            "{\"ts\":\"2026-08-11T09:00:00\",\"stats\":{\"entities\":0,\"connections\":0},\
             \"top_entities\":[]}"
        );
    }

    #[test]
    fn a_row_the_clock_cannot_read_is_filed_under_no_day_at_all() {
        assert!(!on_day(&Value::Null, "2026-08-11"));
        assert!(!on_day(&text(""), "2026-08-11"));
        assert!(on_day(&text("2026-08-11T23:59:59"), "2026-08-11"));
        assert!(!on_day(&text("2026-08-12T00:00:00"), "2026-08-11"));
    }
}
