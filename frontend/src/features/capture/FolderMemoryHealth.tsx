import * as React from "react";
import { useQuery } from "@tanstack/react-query";

import { latticeApi } from "@/api/client";
import { t, type Language } from "@/i18n";
import { asArray, isRecord } from "@/lib/utils";

// Per-folder memory state (v9.9.7). "Connected" is not the same as
// "remembered": a folder can be linked and still half-indexed, or silently
// failing on a parser. This card answers the three questions a user actually
// has — how much of this folder is in my Brain, what failed, and why.
//
// Vector freshness is shown once, labelled global, because the vector index is
// global. A per-folder freshness number would be invented.

export type FolderHealth = {
  id: string;
  label: string;
  status: string;
  watchActive: boolean;
  total: number;
  indexed: number;
  failed: number;
  /** null when nothing is known yet — never rendered as 0%. */
  coverage: number | null;
  errors: Array<{ path: string; detail: string }>;
};

function text(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function count(record: Record<string, unknown>, key: string): number {
  const value = Number(record[key]);
  return Number.isFinite(value) && value > 0 ? Math.round(value) : 0;
}

export function parseFolderHealth(data: unknown): {
  folders: FolderHealth[];
  vectorStatus: string;
  vectorPending: number;
} {
  const root = isRecord(data) ? data : {};
  const vector = isRecord(root.vector_freshness_global) ? root.vector_freshness_global : {};
  return {
    vectorStatus: text(vector, ["status"]) || "unavailable",
    vectorPending: count(vector, "pending_items"),
    folders: asArray<unknown>(root.folders).flatMap((raw): FolderHealth[] => {
      const folder = isRecord(raw) ? raw : {};
      const id = text(folder, ["id"]);
      if (!id) return [];
      const files = isRecord(folder.files) ? folder.files : {};
      const rawCoverage = folder.coverage;
      return [{
        id,
        label: text(folder, ["label", "root_path"]) || id,
        status: text(folder, ["status"]),
        watchActive: folder.watch_active === true,
        total: count(files, "total"),
        indexed: count(files, "indexed"),
        failed: count(files, "failed"),
        coverage: typeof rawCoverage === "number" && Number.isFinite(rawCoverage) ? rawCoverage : null,
        errors: asArray<unknown>(folder.recent_errors).flatMap((entry) => {
          const error = isRecord(entry) ? entry : {};
          const detail = text(error, ["detail"]);
          return detail ? [{ path: text(error, ["path"]), detail }] : [];
        }),
      }];
    }),
  };
}

export function FolderMemoryHealthCard({ language }: { language: Language }) {
  const healthQ = useQuery({
    queryKey: ["folderMemoryHealth"],
    queryFn: () => latticeApi.localFolderHealth(),
    staleTime: 30_000,
  });
  const parsed = React.useMemo(
    () => (healthQ.data?.ok ? parseFolderHealth(healthQ.data.data) : null),
    [healthQ.data],
  );
  if (!parsed || !parsed.folders.length) return null;

  return (
    <section className="folder-memory-health" data-testid="folder-memory-health">
      <h3>{t(language, "capture.folderHealth.title")}</h3>
      <ul>
        {parsed.folders.map((folder) => (
          <li key={folder.id} data-testid={`folder-health-${folder.id}`}>
            <div className="folder-memory-head">
              <strong>{folder.label}</strong>
              <span className="folder-memory-coverage">
                {folder.coverage === null
                  ? t(language, "capture.folderHealth.unknown")
                  : t(language, "capture.folderHealth.coverage", {
                      percent: Math.round(folder.coverage * 100),
                      indexed: folder.indexed,
                      total: folder.total,
                    })}
              </span>
            </div>
            {folder.coverage === null ? null : (
              <div
                className="folder-memory-bar"
                role="progressbar"
                aria-valuenow={Math.round(folder.coverage * 100)}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <span style={{ width: `${Math.round(folder.coverage * 100)}%` }} />
              </div>
            )}
            {folder.watchActive ? (
              <small>{t(language, "capture.folderHealth.watching")}</small>
            ) : null}
            {folder.failed > 0 ? (
              <details className="folder-memory-errors">
                <summary>
                  {t(language, "capture.folderHealth.failed", { count: folder.failed })}
                </summary>
                <ul>
                  {folder.errors.map((error) => (
                    <li key={`${folder.id}-${error.path}-${error.detail}`}>
                      <code>{error.path}</code>
                      <span>{error.detail}</span>
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </li>
        ))}
      </ul>
      {parsed.vectorPending > 0 ? (
        <small className="folder-memory-vector">
          {t(language, "capture.folderHealth.vectorGlobal", { count: parsed.vectorPending })}
        </small>
      ) : null}
    </section>
  );
}
