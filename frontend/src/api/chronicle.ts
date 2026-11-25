import { get } from "./base";

export type ChronicleLaneCounts = {
  sources: number;
  entities: number;
  connections: number;
  conversations: number;
};

export type ChronicleSeriesPoint = ChronicleLaneCounts & { date: string };

export type ChronicleOverview = {
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

export type ChronicleAsOf = {
  ts: string;
  stats: { entities: number; connections: number };
  top_entities: ChronicleTopEntity[];
};

export const chronicleApi = {
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
