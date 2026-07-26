import * as React from "react";
import { useQuery } from "@tanstack/react-query";

import { latticeApi } from "@/api/client";
import { t, type Language } from "@/i18n";
import { asArray, isRecord } from "@/lib/utils";

// The knowledge garden in four beds (v9.9.7). Living Brain answers "how
// healthy is my knowledge?" in aggregate; a gardener asks four concrete
// questions instead: what arrived, what disagrees, what went stale, what the
// rest leans on. Read-only — every bed links into existing surfaces rather
// than inventing its own actions.

export type GardenBedId = "recent" | "contradictions" | "stale" | "frequent";

export type GardenItem = {
  id: string;
  title: string;
  type: string;
  detail: string;
};

export type GardenBed = {
  id: GardenBedId;
  count: number;
  items: GardenItem[];
};

const BED_ORDER: GardenBedId[] = ["recent", "contradictions", "stale", "frequent"];

function text(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return "";
}

/**
 * `GET /api/brain/garden` → the four beds the panel renders.
 *
 * Rows without an id or a title are dropped: an unlabelled plant is noise. A
 * missing bed becomes an empty bed, never a fabricated one.
 */
export function parseGarden(data: unknown): { available: boolean; beds: GardenBed[] } {
  const root = isRecord(data) ? data : {};
  const beds = isRecord(root.beds) ? root.beds : {};
  return {
    available: root.available === true,
    beds: BED_ORDER.map((id) => {
      const bed = isRecord(beds[id]) ? (beds[id] as Record<string, unknown>) : {};
      const rawCount = Number(bed.count);
      const items = asArray<unknown>(bed.items).flatMap((raw): GardenItem[] => {
        const item = isRecord(raw) ? raw : {};
        const itemId = text(item, ["id"]);
        const title = text(item, ["title", "summary", "label"]);
        if (!itemId || !title) return [];
        return [{
          id: itemId,
          title,
          type: text(item, ["type", "kind"]),
          detail:
            id === "frequent"
              ? text(item, ["degree"])
              : text(item, ["updated_at", "detail"]),
        }];
      });
      return {
        id,
        count: Number.isFinite(rawCount) && rawCount > 0 ? Math.round(rawCount) : items.length,
        items,
      };
    }),
  };
}

export function KnowledgeGardenPanel({ language }: { language: Language }) {
  const [expanded, setExpanded] = React.useState(false);
  const gardenQ = useQuery({
    queryKey: ["knowledgeGarden"],
    queryFn: () => latticeApi.brainGarden(),
    enabled: expanded,
    staleTime: 60_000,
  });
  const parsed = React.useMemo(
    () => (gardenQ.data?.ok ? parseGarden(gardenQ.data.data) : null),
    [gardenQ.data],
  );

  return (
    <details
      className="brain-garden"
      data-testid="knowledge-garden"
      open={expanded}
      onToggle={(event) => setExpanded((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary>{t(language, "brain.garden.title")}</summary>
      {!expanded ? null : gardenQ.isLoading ? (
        <p role="status">{t(language, "brain.garden.loading")}</p>
      ) : !parsed || !parsed.available ? (
        <p role="status" className="is-muted">{t(language, "brain.garden.unavailable")}</p>
      ) : (
        <div className="brain-garden-beds">
          {parsed.beds.map((bed) => (
            <section key={bed.id} data-testid={`garden-bed-${bed.id}`}>
              <h4>
                {t(language, `brain.garden.bed.${bed.id}`)}
                <span className="brain-garden-count">{bed.count}</span>
              </h4>
              {bed.items.length ? (
                <ul>
                  {bed.items.map((item) => (
                    <li key={item.id}>
                      <strong>{item.title}</strong>
                      {item.type ? <em>{item.type}</em> : null}
                      {item.detail ? <small>{item.detail}</small> : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="is-muted">{t(language, `brain.garden.empty.${bed.id}`)}</p>
              )}
            </section>
          ))}
        </div>
      )}
    </details>
  );
}
