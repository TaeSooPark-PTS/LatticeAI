import { get } from "./base";

/**
 * Brain Chronicle (`/api/chronicle/*`, v11.3.0).
 *
 * The router returns plain dicts, so the generated `operations` entry declares
 * its 200 body as `unknown` and there is nothing to infer from. These types are
 * therefore hand-written against `latticeai/services/chronicle.py` — the same
 * arrangement `PermissionModeState` and `NetworkBoundaryState` already use.
 */
export type ChronicleLaneCounts = {
  sources: number;
  entities: number;
  connections: number;
  conversations: number;
};

/** One day that carried something. The series is sparse — quiet days are absent. */
export type ChronicleSeriesPoint = ChronicleLaneCounts & { date: string };

export type ChronicleOverview = {
  /** Naive local ISO seconds, or null for a Brain that has seen nothing yet. */
  first_activity_at: string | null;
  last_activity_at: string | null;
  totals: ChronicleLaneCounts;
  series: ChronicleSeriesPoint[];
};

export type ChronicleSourceCard = {
  id: string;
  title: string;
  source_type: string;
  captured_at: string;
  node_id: string;
};

export type ChronicleEntityCard = { id: string; label: string; type: string; created_at: string };

/** Messages with no conversation id collapse into a single card with `""`. */
export type ChronicleConversationCard = {
  conversation_id: string;
  preview: string;
  messages: number;
  started_at: string;
};

export type ChronicleChangeKind =
  | "fact_superseded"
  | "fact_retired"
  | "connection_superseded"
  | "connection_ended";

export type ChronicleChangeCard = {
  kind: ChronicleChangeKind | string;
  label: string;
  at: string;
  node_id: string;
};

/** `counts` are true totals; each `groups` list is capped at 200 server-side. */
export type ChronicleDay = {
  date: string;
  counts: { sources: number; entities: number; conversations: number; changes: number };
  groups: {
    sources: ChronicleSourceCard[];
    entities: ChronicleEntityCard[];
    conversations: ChronicleConversationCard[];
    changes: ChronicleChangeCard[];
  };
};

export type ChronicleTopEntity = {
  id: string;
  label: string;
  type: string;
  importance_score: number;
};

/**
 * The Brain as it stood at an instant.
 *
 * `stats.entities` counts every node in the slice, documents included — it is
 * deliberately a different measure from the overview's concept-only `entities`
 * lane, and the screen says so rather than presenting the two as one number.
 */
export type ChronicleAsOf = {
  ts: string;
  stats: { entities: number; connections: number };
  top_entities: ChronicleTopEntity[];
};

/**
 * The three chronicle reads, kept beside the types they answer with.
 *
 * Spread into `latticeApi` so every caller — and the endpoint table that proves
 * each wrapper sends what it documents — still reaches them the usual way.
 */
export const chronicleApi = {
  // All three are read-only. The fallbacks are an honest empty timeline rather
  // than `{}`: the page maps over `series` and over every `groups` list
  // unconditionally, so a failed read has to render "nothing recorded" instead
  // of throwing on `undefined.map`.
  chronicleOverview: () => get<ChronicleOverview>("/api/chronicle/overview", {
    first_activity_at: null,
    last_activity_at: null,
    totals: { sources: 0, entities: 0, connections: 0, conversations: 0 },
    series: [],
  }),
  chronicleDay: (date: string) => get<ChronicleDay>(
    `/api/chronicle/day/${encodeURIComponent(date)}`,
    {
      date,
      counts: { sources: 0, entities: 0, conversations: 0, changes: 0 },
      groups: { sources: [], entities: [], conversations: [], changes: [] },
    },
  ),
  chronicleAsOf: (ts: string) => get<ChronicleAsOf>(
    "/api/chronicle/as-of",
    { ts, stats: { entities: 0, connections: 0 }, top_entities: [] },
    { ts },
  ),
};
