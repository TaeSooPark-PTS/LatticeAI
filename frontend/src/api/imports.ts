import { get, post } from "./base";

export type ImportSource = "obsidian" | "notion" | "git" | "mail";

export type InteropStatus = {
  sources: Partial<Record<Exclude<ImportSource, "obsidian">, { available: boolean }>>;
};

export type ImportError = { file?: string; title?: string; error?: string };

export type ImportResponse = {
  permission_required?: boolean;
  approval_token?: string;
  expires_in?: number;
  path?: string;
  dry_run?: boolean;
  files_scanned?: number;
  notes_found?: number;
  items_found?: number;
  ingested?: number;
  duplicates?: number;
  failed?: number;
  wikilinks_detected?: number;
  edges_created?: number;
  sample_files?: string[];
  sample_titles?: string[];
  errors?: ImportError[];
};

export type ImportRequest = {
  source: ImportSource;
  path: string;
  approved: boolean;
  approval_token?: string;
  dry_run?: boolean;
};

export const IMPORT_TIMEOUT_MS = 10 * 60_000;

export const importsApi = {
  interopStatus: () => get<InteropStatus>("/api/ingestion/interop", { sources: {} }),
  importFromTool: ({ source, ...body }: ImportRequest) =>
    source === "obsidian"
      ? post<ImportResponse>("/api/ingestion/obsidian", body, {}, IMPORT_TIMEOUT_MS)
      : post<ImportResponse>("/api/ingestion/interop", { source, ...body }, {}, IMPORT_TIMEOUT_MS),
};
