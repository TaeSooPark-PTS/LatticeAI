import "@/i18n/brain";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { t, type Language } from "@/i18n";
import { EMPTY_SEARCH_FILTERS, isReversedRange, type SearchFilterState } from "./searchFilterModel";

export function SearchFilters({
  language,
  value,
  onChange,
}: {
  language: Language;
  value: SearchFilterState;
  onChange: (next: SearchFilterState) => void;
}) {
  const reversed = isReversedRange(value);
  const active = Boolean(value.since || value.until || value.includeSuperseded);
  return (
    <fieldset className="m-0 space-y-2 border-0 p-0">
      <legend className="sr-only">{t(language, "brain.filters.legend")}</legend>
      <div className="flex flex-wrap items-end gap-3 text-xs text-muted-foreground">
        <label className="grid gap-1">
          {t(language, "brain.filters.since")}
          <Input type="date" className="h-8 w-40" value={value.since} aria-invalid={reversed} onChange={(event) => onChange({ ...value, since: event.target.value })} />
        </label>
        <label className="grid gap-1">
          {t(language, "brain.filters.until")}
          <Input type="date" className="h-8 w-40" value={value.until} aria-invalid={reversed} onChange={(event) => onChange({ ...value, until: event.target.value })} />
        </label>
        <label className="flex h-8 items-center gap-2">
          <input type="checkbox" checked={value.includeSuperseded} onChange={(event) => onChange({ ...value, includeSuperseded: event.target.checked })} />
          {t(language, "brain.filters.superseded")}
        </label>
        {active ? (
          <Button type="button" size="sm" variant="ghost" onClick={() => onChange(EMPTY_SEARCH_FILTERS)}>
            {t(language, "brain.filters.clear")}
          </Button>
        ) : null}
      </div>
      {reversed ? <p role="alert" className="text-xs text-destructive">{t(language, "brain.filters.reversed")}</p> : null}
      {value.until && !value.includeSuperseded ? (
        <p className="text-xs text-muted-foreground">{t(language, "brain.filters.pastNote")}</p>
      ) : null}
    </fieldset>
  );
}
