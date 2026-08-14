//! `lattice_brain/graph/schema.py`'s `NodeType` / `EdgeType` normalization.
//!
//! Only `from_legacy(label).value` is ever reached from the write path, so the
//! enums are ported as string functions rather than as Rust enums: the value
//! *is* the contract (it lands in `nodes_v2.type` / `edges_v2.type`), and a
//! Rust enum would add a second name for the same thing plus a conversion that
//! could drift from it.
//!
//! The two-step rule is Python's, in Python's order:
//!
//! 1. `cls(m.upper())` — a canonical value round-trips exactly, which is what
//!    keeps `CODE_FILE` / `AI_RESPONSE` from degrading to `CONCEPT`. Note that
//!    it also catches ordinary labels whose uppercase *is* canonical
//!    (`"Chat"` → `CHAT`), before the alias table is consulted at all;
//! 2. the alias table, keyed on `m.lower()`;
//! 3. `CONCEPT` (nodes) / `MENTIONS` (edges) as the fallback. Nothing is lost:
//!    the caller stores the raw label in `legacy_type` beside it.

/// Every `NodeType` member value, in declaration order.
pub const NODE_TYPES: [&str; 43] = [
    "CONVERSATION",
    "MESSAGE",
    "FILE",
    "DOCUMENT",
    "CHUNK",
    "CODE_SYMBOL",
    "CONCEPT",
    "PERSON",
    "MODEL",
    "TOOL",
    "PROJECT",
    "COMPUTER",
    "DRIVE",
    "FOLDER",
    "CODE_FILE",
    "SPREADSHEET",
    "SLIDE_DECK",
    "IMAGE",
    "IMAGE_TEXT",
    "SLIDE",
    "PAGE",
    "SHEET",
    "SECTION",
    "CHAT",
    "AI_RESPONSE",
    "TOPIC",
    "FEATURE",
    "TASK",
    "DECISION",
    "ERROR",
    "EVENT",
    "SOURCE",
    "REPOSITORY",
    "MEETING",
    "ORGANIZATION",
    "WORKFLOW",
    "AGENT",
    "SELF",
    "PREFERENCE",
    "HABIT",
    "RELATIONSHIP",
    "AUDIO",
    "VIDEO",
];

/// Every `EdgeType` member value, in declaration order.
pub const EDGE_TYPES: [&str; 37] = [
    "CONTAINS",
    "MENTIONS",
    "REFERENCES",
    "REPLIES_TO",
    "AUTHORED_BY",
    "USES",
    "DERIVED_FROM",
    "SIMILAR_TO",
    "DEPENDS_ON",
    "TAGGED_AS",
    "VERSION_OF",
    "GRANTS_ACCESS",
    "USED_IN",
    "INSPIRED_BY",
    "CONTRADICTS",
    "EVOLVES_FROM",
    "UPLOADED_BY",
    "WROTE",
    "HAS_EVENT",
    "TRIGGERED",
    "HAS_SLIDE",
    "HAS_PAGE",
    "HAS_SHEET",
    "HAS_CHUNK",
    "CONTAINS_IMAGE",
    "CONTAINS_SIGNAL",
    "DISCUSSES",
    "IMPLIES",
    "RELATED_TO",
    "INDEXED_FROM",
    "MODIFIED_BY",
    "BELONGS_TO_PROJECT",
    "PART_OF",
    "DISCUSSED_IN",
    "DECIDED_BY",
    "GENERATED_BY",
    "USED_BY_AGENT",
];

/// `_LEGACY_NODE_MAP` — lowercase alias → canonical value.
const LEGACY_NODE_MAP: &[(&str, &str)] = &[
    ("conversation", "CONVERSATION"),
    ("chat", "CHAT"),
    ("message", "MESSAGE"),
    ("airesponse", "AI_RESPONSE"),
    ("file", "FILE"),
    ("codefile", "CODE_FILE"),
    ("spreadsheet", "SPREADSHEET"),
    ("slidedeck", "SLIDE_DECK"),
    ("image", "IMAGE"),
    ("imagetext", "IMAGE_TEXT"),
    ("computer", "COMPUTER"),
    ("drive", "DRIVE"),
    ("folder", "FOLDER"),
    ("page", "PAGE"),
    ("sheet", "SHEET"),
    ("slide", "SLIDE"),
    ("section", "SECTION"),
    ("chunk", "CHUNK"),
    ("code", "CODE_SYMBOL"),
    ("concept", "CONCEPT"),
    ("topic", "TOPIC"),
    ("feature", "FEATURE"),
    ("task", "TASK"),
    ("decision", "DECISION"),
    ("error", "ERROR"),
    ("event", "EVENT"),
    ("tag", "CONCEPT"),
    ("person", "PERSON"),
    ("user", "PERSON"),
    ("model", "MODEL"),
    ("tool", "TOOL"),
    ("mcp", "TOOL"),
    ("project", "PROJECT"),
    ("workspace", "PROJECT"),
    ("document", "DOCUMENT"),
    ("report", "DOCUMENT"),
    ("plan", "DOCUMENT"),
    ("proposal", "DOCUMENT"),
    ("보고서", "DOCUMENT"),
    ("계획서", "DOCUMENT"),
    ("기획서", "DOCUMENT"),
    ("source", "SOURCE"),
    ("ingestionsource", "SOURCE"),
    ("repository", "REPOSITORY"),
    ("repo", "REPOSITORY"),
    ("gitrepo", "REPOSITORY"),
    ("meeting", "MEETING"),
    ("organization", "ORGANIZATION"),
    ("org", "ORGANIZATION"),
    ("company", "ORGANIZATION"),
    ("team", "ORGANIZATION"),
    ("workflow", "WORKFLOW"),
    ("agent", "AGENT"),
    ("self", "SELF"),
    ("selfmodel", "SELF"),
    ("me", "SELF"),
    ("나", "SELF"),
    ("preference", "PREFERENCE"),
    ("선호", "PREFERENCE"),
    ("habit", "HABIT"),
    ("습관", "HABIT"),
    ("relationship", "RELATIONSHIP"),
    ("관계", "RELATIONSHIP"),
    ("결정", "DECISION"),
    ("audio", "AUDIO"),
    ("오디오", "AUDIO"),
    ("video", "VIDEO"),
    ("영상", "VIDEO"),
    ("동영상", "VIDEO"),
];

/// `_LEGACY_EDGE_MAP` — lowercase alias → canonical value.
const LEGACY_EDGE_MAP: &[(&str, &str)] = &[
    ("언급함", "MENTIONS"),
    ("포함함", "CONTAINS"),
    ("해결함", "REFERENCES"),
    ("의존함", "DEPENDS_ON"),
    ("설명함", "MENTIONS"),
    ("비교함", "SIMILAR_TO"),
    ("사용함", "USES"),
    ("연결함", "REFERENCES"),
    ("확장함", "DERIVED_FROM"),
    ("생성함", "AUTHORED_BY"),
    ("작성함", "WROTE"),
    ("업로드함", "UPLOADED_BY"),
    ("대체함", "VERSION_OF"),
    ("지원함", "USES"),
    ("발생함", "REFERENCES"),
    ("관련됨", "MENTIONS"),
    ("mentions", "MENTIONS"),
    ("contains", "CONTAINS"),
    ("references", "REFERENCES"),
    ("replies_to", "REPLIES_TO"),
    ("authored_by", "AUTHORED_BY"),
    ("uses", "USES"),
    ("derived_from", "DERIVED_FROM"),
    ("similar_to", "SIMILAR_TO"),
    ("depends_on", "DEPENDS_ON"),
    ("tagged_as", "TAGGED_AS"),
    ("version_of", "VERSION_OF"),
    ("grants_access", "GRANTS_ACCESS"),
    ("used_in", "USED_IN"),
    ("inspired_by", "INSPIRED_BY"),
    ("contradicts", "CONTRADICTS"),
    ("evolves_from", "EVOLVES_FROM"),
    ("uploaded_by", "UPLOADED_BY"),
    ("wrote", "WROTE"),
    ("has_event", "HAS_EVENT"),
    ("triggered", "TRIGGERED"),
    ("has_slide", "HAS_SLIDE"),
    ("has_page", "HAS_PAGE"),
    ("has_sheet", "HAS_SHEET"),
    ("has_chunk", "HAS_CHUNK"),
    ("contains_image", "CONTAINS_IMAGE"),
    ("contains_signal", "CONTAINS_SIGNAL"),
    ("discusses", "DISCUSSES"),
    ("implies", "IMPLIES"),
    ("related_to", "RELATED_TO"),
    ("활용됨", "USED_IN"),
    ("영감받음", "INSPIRED_BY"),
    ("상충함", "CONTRADICTS"),
    ("발전함", "EVOLVES_FROM"),
    ("indexed_from", "INDEXED_FROM"),
    ("modified_by", "MODIFIED_BY"),
    ("belongs_to_project", "BELONGS_TO_PROJECT"),
    ("belongs_to", "BELONGS_TO_PROJECT"),
    ("part_of", "PART_OF"),
    ("discussed_in", "DISCUSSED_IN"),
    ("decided_by", "DECIDED_BY"),
    ("generated_by", "GENERATED_BY"),
    ("used_by_agent", "USED_BY_AGENT"),
    ("색인됨", "INDEXED_FROM"),
    ("수정함", "MODIFIED_BY"),
    ("결정함", "DECIDED_BY"),
    ("구성요소", "PART_OF"),
];

/// `NodeType.from_legacy(label).value`.
pub fn node_type_from_legacy(label: &str) -> String {
    let trimmed = label.trim();
    let upper = trimmed.to_uppercase();
    if NODE_TYPES.contains(&upper.as_str()) {
        return upper;
    }
    let lower = trimmed.to_lowercase();
    LEGACY_NODE_MAP
        .iter()
        .find(|(alias, _)| *alias == lower)
        .map(|(_, canonical)| (*canonical).to_string())
        .unwrap_or_else(|| "CONCEPT".to_string())
}

/// `EdgeType.from_legacy(label).value`.
pub fn edge_type_from_legacy(label: &str) -> String {
    let trimmed = label.trim();
    let upper = trimmed.to_uppercase();
    if EDGE_TYPES.contains(&upper.as_str()) {
        return upper;
    }
    let lower = trimmed.to_lowercase();
    LEGACY_EDGE_MAP
        .iter()
        .find(|(alias, _)| *alias == lower)
        .map(|(_, canonical)| (*canonical).to_string())
        .unwrap_or_else(|| "MENTIONS".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_values_round_trip_rather_than_degrading() {
        assert_eq!(node_type_from_legacy("CODE_FILE"), "CODE_FILE");
        assert_eq!(node_type_from_legacy("AI_RESPONSE"), "AI_RESPONSE");
        assert_eq!(edge_type_from_legacy("INDEXED_FROM"), "INDEXED_FROM");
    }

    #[test]
    fn uppercase_of_an_ordinary_label_wins_before_the_alias_table() {
        // `cls(m.upper())` is tried first, so "Chat" resolves through the enum
        // rather than through `_LEGACY_NODE_MAP["chat"]` — same answer here,
        // and the order is what keeps that true for every other member.
        assert_eq!(node_type_from_legacy("Chat"), "CHAT");
        assert_eq!(node_type_from_legacy("Document"), "DOCUMENT");
    }

    #[test]
    fn compound_legacy_labels_go_through_the_alias_table() {
        assert_eq!(node_type_from_legacy("AIResponse"), "AI_RESPONSE");
        assert_eq!(node_type_from_legacy("CodeFile"), "CODE_FILE");
        assert_eq!(node_type_from_legacy("SlideDeck"), "SLIDE_DECK");
        assert_eq!(node_type_from_legacy("ImageText"), "IMAGE_TEXT");
    }

    #[test]
    fn korean_verbs_normalize_to_the_schema_enum() {
        assert_eq!(edge_type_from_legacy("작성함"), "WROTE");
        assert_eq!(edge_type_from_legacy("포함함"), "CONTAINS");
        assert_eq!(edge_type_from_legacy("언급함"), "MENTIONS");
        assert_eq!(edge_type_from_legacy("관련됨"), "MENTIONS");
    }

    #[test]
    fn unknown_labels_fall_back_without_losing_the_original() {
        assert_eq!(node_type_from_legacy("Whatsit"), "CONCEPT");
        assert_eq!(edge_type_from_legacy("whatsits"), "MENTIONS");
        assert_eq!(node_type_from_legacy(""), "CONCEPT");
    }
}
