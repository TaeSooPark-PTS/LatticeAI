/**
 * Brain Chronicle fixtures — a populated eight-week history.
 *
 * The release screenshot of 연대기 is only worth publishing if the screen has
 * something on it, so this module hands back a Brain that has plainly been used:
 * a growth curve that climbs, a heat-map with light and heavy days, one rich
 * selected day, and an `as-of` slice for the rewind panel.
 *
 * Everything is derived from constants, never from `Date.now()`: two captures
 * taken a day apart have to produce identical pixels, and a "last 8 weeks"
 * fixture computed from the clock would move the whole grid every midnight.
 *
 * Returns true when this module answered the request; false lets the entry try
 * the next module, in the same order the original if-chain ran.
 */
const { json } = require("./http.cjs");

const DAY_MS = 86400000;
// Anchored to the same June 2026 week the rest of the visual fixtures use, so
// every captured screen looks like one computer on one day.
const LAST_DAY = "2026-06-06";
const WEEKS = 8;

function dayAt(offset) {
  return new Date(Date.parse(`${LAST_DAY}T00:00:00Z`) + offset * DAY_MS)
    .toISOString()
    .slice(0, 10);
}

/**
 * Sparse series: only days that carried something, ascending — the shape the
 * real service returns. Quiet days are simply absent, which is exactly the case
 * the UI has to fill in for itself.
 *
 * The pattern is deterministic rather than random: weekdays are busy, weekends
 * are light or missing, and one day near the end is the heaviest so the
 * heat-map's darkest level is always represented.
 */
function buildSeries() {
  const series = [];
  for (let index = 0; index < WEEKS * 7; index += 1) {
    const offset = index - (WEEKS * 7 - 1);
    const date = dayAt(offset);
    const weekday = new Date(Date.parse(`${date}T00:00:00Z`)).getUTCDay();
    if (weekday === 0) continue; // Sundays stay off the record entirely.
    if (weekday === 6 && index % 3 !== 0) continue;
    const wave = index % 7;
    series.push({
      date,
      sources: wave % 3,
      entities: (wave % 4) + (weekday === 3 ? 3 : 0),
      connections: wave % 5,
      conversations: weekday % 2,
    });
  }
  // One unmistakable peak, so the darkest heat level is never merely theoretical.
  const peak = series[series.length - 4];
  peak.sources = 6;
  peak.entities = 11;
  peak.connections = 9;
  peak.conversations = 3;
  return series;
}

const series = buildSeries();

const totals = series.reduce(
  (sum, point) => ({
    sources: sum.sources + point.sources,
    entities: sum.entities + point.entities,
    connections: sum.connections + point.connections,
    conversations: sum.conversations + point.conversations,
  }),
  { sources: 0, entities: 0, connections: 0, conversations: 0 },
);

const overview = {
  first_activity_at: `${series[0].date}T09:12:04`,
  last_activity_at: `${series[series.length - 1].date}T18:40:11`,
  totals,
  series,
};

/**
 * One day with something in every group.
 *
 * `counts` deliberately exceed the group lengths for 자료, so the "이 밖에 N개가
 * 더 있어요" line is exercised by the capture rather than only by a unit test.
 */
const richDay = {
  sources: [
    { id: "prov-1", title: "retrieval-design.pdf", source_type: "upload", captured_at: "T09:20:00", node_id: "file:retrieval" },
    { id: "prov-2", title: "릴리스 절차 정리", source_type: "note", captured_at: "T10:05:00", node_id: "note:release" },
    { id: "prov-3", title: "Lattice 소개 페이지", source_type: "web_url", captured_at: "T11:41:00", node_id: "web:intro" },
    { id: "prov-4", title: "meeting-notes.md", source_type: "local_file", captured_at: "T14:02:00", node_id: "file:meeting" },
  ],
  entities: [
    { id: "entity:memory", label: "Workspace 개인 기억", type: "Concept", created_at: "T09:33:00" },
    { id: "entity:release", label: "릴리스 절차", type: "Task", created_at: "T10:12:00" },
    { id: "entity:review", label: "검토함", type: "Decision", created_at: "T14:20:00" },
    { id: "entity:person", label: "박태수", type: "Person", created_at: "T16:44:00" },
  ],
  conversations: [
    { conversation_id: "conv-1", preview: "이번 릴리스에서 뭐가 달라졌는지 정리해 줘", messages: 12, started_at: "T09:15:00" },
    { conversation_id: "conv-2", preview: "지난주에 넣은 자료 중에 검색이 잘 안 되는 게 있어", messages: 6, started_at: "T13:08:00" },
    { conversation_id: "", preview: "예전에 옮겨 온 기록", messages: 3, started_at: "T17:55:00" },
  ],
  changes: [
    { kind: "fact_superseded", label: "릴리스 절차 — 새 문서로 교체됨", at: "T11:02:00", node_id: "entity:release" },
    { kind: "fact_retired", label: "임시 백업 위치", at: "T12:30:00", node_id: "entity:backup" },
    { kind: "connection_superseded", label: "릴리스 절차 → 검토함", at: "T15:10:00", node_id: "edge:release-review" },
    { kind: "connection_ended", label: "임시 백업 위치 → Workspace 개인 기억", at: "T16:00:00", node_id: "edge:backup-memory" },
  ],
};

function dayPayload(date) {
  const stamp = (suffix) => `${date}${suffix}`;
  return {
    date,
    counts: {
      // True totals, larger than the lists: the real service caps groups at 200
      // and the screen has to say how many it is not showing.
      sources: richDay.sources.length + 3,
      entities: richDay.entities.length,
      conversations: richDay.conversations.length,
      changes: richDay.changes.length,
    },
    groups: {
      sources: richDay.sources.map((item) => ({ ...item, captured_at: stamp(item.captured_at) })),
      entities: richDay.entities.map((item) => ({ ...item, created_at: stamp(item.created_at) })),
      conversations: richDay.conversations.map((item) => ({ ...item, started_at: stamp(item.started_at) })),
      changes: richDay.changes.map((item) => ({ ...item, at: stamp(item.at) })),
    },
  };
}

const topEntities = [
  { id: "entity:lattice", label: "Lattice Workspace", type: "Topic", importance_score: 14 },
  { id: "entity:memory", label: "Workspace 개인 기억", type: "Concept", importance_score: 11 },
  { id: "entity:release", label: "릴리스 절차", type: "Task", importance_score: 9 },
  { id: "entity:workspace", label: "Workspace Health", type: "Concept", importance_score: 7 },
  { id: "entity:review", label: "검토함", type: "Decision", importance_score: 5 },
  { id: "entity:skills", label: "Skill Marketplace", type: "Task", importance_score: 3 },
];

module.exports = function handleChronicle({ res, url, pathname }) {
  if (pathname === "/api/chronicle/overview") return json(res, overview);
  if (pathname.startsWith("/api/chronicle/day/")) {
    return json(res, dayPayload(pathname.slice("/api/chronicle/day/".length)));
  }
  if (pathname === "/api/chronicle/as-of") {
    return json(res, {
      ts: url.searchParams.get("ts") || `${LAST_DAY}T23:59:59`,
      // Deliberately larger than the overview's concept-only lane: the as-of
      // slice counts every node, documents included, and the rewind panel says
      // so instead of presenting the two as the same number.
      stats: { entities: 148, connections: 96 },
      top_entities: topEntities,
    });
  }
  return false;
};
