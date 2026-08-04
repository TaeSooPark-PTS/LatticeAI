import * as React from "react";
// Route-scoped copy: importing the namespace registers it into the shared
// table and keeps it inside this lazy chunk instead of the entry bundle.
import "@/i18n/workspace";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ListFilter, RotateCcw, Search, ShieldCheck } from "lucide-react";
import { latticeApi, type AdminAuditFilters, type ApiResult } from "@/api/client";
import { Button } from "@/components/ui/button";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { useAppStore } from "@/store/appStore";
import { asArray, isRecord } from "@/lib/utils";
import { t } from "@/i18n";

type ApiRecord = Record<string, unknown>;
type AdminFilterState = Required<Pick<AdminAuditFilters, "q" | "actor" | "action" | "severity">> & { limit: number };

export function AdminConsole({ onBack }: { onBack: () => void }) {
  const qc = useQueryClient();
  const language = useAppStore((state) => state.language);
  const [filters, setFilters] = React.useState<AdminFilterState>({ q: "", actor: "", action: "", severity: "", limit: 50 });
  const { summaryQ, statsQ, usersQ, auditQ, securityQ, securityEventsQ, policiesQ, rolesQ, retentionQ, indexQ, agentRuntimeQ, toolRegistryQ, healthSummaryQ } = useAdminConsoleData(filters);
  const rebuildIndex = useMutation({
    mutationFn: latticeApi.rebuildIndex,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["indexStatus"] }),
  });

  const users = asArray(usersQ.data?.data);
  const auditEvents = asArray((auditQ.data?.data as ApiRecord | undefined)?.recent_events);
  const securityEvents = asArray((securityEventsQ.data?.data as ApiRecord | undefined)?.events);
  const policies = asArray((policiesQ.data?.data as ApiRecord | undefined)?.policies);
  const roles = asArray((rolesQ.data?.data as ApiRecord | undefined)?.roles);
  const retention = (retentionQ.data?.data || {}) as ApiRecord;
  const healthData = (healthSummaryQ.data?.data || healthSummaryQ.data) as ApiRecord | undefined;
  const hasIssue = healthData?.status === "attention" || (typeof healthData?.issue_count === "number" && healthData.issue_count > 0);

  return (
    <main className="admin-console" aria-label={t(language, "admin.aria.console")}>
      <header className="admin-console-header">
        <button className="admin-back-button" type="button" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" />
          {t(language, "admin.back")}
        </button>
        <div>
          <span>{t(language, "admin.kicker")}</span>
          <h1>{t(language, "admin.title")}</h1>
          <p>{t(language, "admin.body")}</p>
        </div>
        <LanguageSwitcher compact />
      </header>

      <section className="admin-metrics-statement px-4 py-3 border border-border/40 rounded-lg bg-muted/25 text-sm text-muted-foreground flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2" aria-label={t(language, "admin.overview")}>
        <div className="flex items-center gap-2 font-medium text-foreground">
          <ShieldCheck className={`h-4 w-4 ${hasIssue ? "text-amber-500" : "text-emerald-500"}`} />
          <span>
            {hasIssue
              ? t(language, "admin.health.attention", { count: String(healthData?.issue_count || 1) })
              : t(language, "admin.health.ok")}
          </span>
        </div>
        <div className="text-xs text-muted-foreground">
          {t(language, "admin.summaryStatement", {
            users: users.length,
            logs: auditEvents.length + securityEvents.length,
            // `.ok` is only "the request succeeded". A server answering 200
            // with `status: "degraded"` was being summarised as 준비됨, which
            // shows an admin a green light over a degraded service. Prefer the
            // status the server actually reports and fall back to the
            // request-level outcome only when it reports none.
            security:
              adminStatusLabel(securityQ.data?.data, "status") ||
              (securityQ.data?.ok ? t(language, "admin.status.ready") : t(language, "admin.status.unavailable")),
            index:
              adminStatusLabel(indexQ.data?.data, "status") ||
              (indexQ.data?.ok ? t(language, "admin.status.indexed") : t(language, "admin.status.unknown")),
          })}
        </div>
      </section>

      <section className="admin-accordion-list flex flex-col gap-4 mt-4">
        <details className="admin-accordion-details border border-border rounded-lg bg-card p-4" open>
          <summary className="font-semibold text-base cursor-pointer mb-3"><h2 className="inline font-semibold text-base">{t(language, "admin.panel.users")} · {t(language, "admin.panel.people")}</h2></summary>
          <AdminPanel title={t(language, "admin.panel.users")} eyebrow={t(language, "admin.panel.people")}>
            <AdminList
              items={users.slice(0, 8)}
              empty={t(language, "admin.empty.users")}
              render={(item) => {
                const user = item as ApiRecord;
                return (
                  <>
                    <strong>{stringValue(user.name || user.email || user.id, t(language, "admin.fallback.localUser"))}</strong>
                    <span>{stringValue(user.role || user.status || user.workspace_id, t(language, "admin.fallback.member"))}</span>
                  </>
                );
              }}
            />
          </AdminPanel>
        </details>

        <details className="admin-accordion-details border border-border rounded-lg bg-card p-4">
          <summary className="font-semibold text-base cursor-pointer mb-3"><h2 className="inline font-semibold text-base">{t(language, "admin.panel.roles")} · {t(language, "admin.panel.access")}</h2></summary>
          <AdminPanel title={t(language, "admin.panel.roles")} eyebrow={t(language, "admin.panel.access")}>
            <AdminList
              items={roles.slice(0, 6)}
              empty={t(language, "admin.empty.roles")}
              render={(item) => {
                const role = item as ApiRecord;
                return (
                  <>
                    <strong>{stringValue(role.role, t(language, "admin.fallback.role"))} · {stringValue(role.members, "0")} {t(language, "admin.metric.users")}</strong>
                    <span>{asArray(role.caps).slice(0, 4).map((cap) => stringValue(cap, "")).filter(Boolean).join(", ") || t(language, "admin.fallback.noCaps")}</span>
                  </>
                );
              }}
            />
          </AdminPanel>
        </details>

        <details className="admin-accordion-details border border-border rounded-lg bg-card p-4">
          <summary className="font-semibold text-base cursor-pointer mb-3"><h2 className="inline font-semibold text-base">{t(language, "admin.panel.logs")} · {t(language, "admin.panel.audit")}</h2></summary>
          <AdminPanel title={t(language, "admin.panel.logs")} eyebrow={t(language, "admin.panel.audit")}>
            <AdminLogFilters language={language} filters={filters} onChange={setFilters} matched={(auditQ.data?.data as ApiRecord | undefined)?.filters as ApiRecord | undefined} />
            <AdminList
              items={auditEvents.slice(0, 8)}
              empty={t(language, "admin.empty.audit")}
              render={(item) => renderLogRow(item as ApiRecord, language)}
            />
          </AdminPanel>
        </details>

        <details className="admin-accordion-details border border-border rounded-lg bg-card p-4">
          <summary className="font-semibold text-base cursor-pointer mb-3"><h2 className="inline font-semibold text-base">{t(language, "admin.panel.securityEvents")} · {t(language, "admin.panel.protection")}</h2></summary>
          <AdminPanel title={t(language, "admin.panel.securityEvents")} eyebrow={t(language, "admin.panel.protection")}>
            <AdminList
              items={securityEvents.slice(0, 8)}
              empty={t(language, "admin.empty.security")}
              render={(item) => renderLogRow(item as ApiRecord, language)}
            />
          </AdminPanel>
        </details>

        <details className="admin-accordion-details border border-border rounded-lg bg-card p-4">
          <summary className="font-semibold text-base cursor-pointer mb-3"><h2 className="inline font-semibold text-base">{t(language, "admin.panel.brainOps")} · {t(language, "admin.panel.maintenance")}</h2></summary>
          <AdminPanel title={t(language, "admin.panel.brainOps")} eyebrow={t(language, "admin.panel.maintenance")}>
            <div className="admin-operation">
              <div>
                <strong>{indexDetail(indexQ.data?.data, language)}</strong>
                <span>{summaryText(summaryQ.data?.data) || summaryText(statsQ.data?.data) || t(language, "admin.brain.summaryFallback")}</span>
              </div>
              <Button variant="outline" size="sm" disabled={rebuildIndex.isPending} onClick={() => rebuildIndex.mutate()}>
                <RotateCcw className="h-3.5 w-3.5" />
                {rebuildIndex.isPending ? t(language, "admin.brain.rebuilding") : t(language, "admin.brain.rebuild")}
              </Button>
            </div>
            <div className="admin-policy-strip">
              {policies.slice(0, 5).map((item, index) => {
                const policy = item as ApiRecord;
                return <span key={`${stringValue(policy.id || policy.name, "policy")}-${index}`}>{stringValue(policy.label || policy.name || policy.id, t(language, "admin.policy.fallback"))}</span>;
              })}
              {!policies.length ? <span>{t(language, "admin.policy.quiet")}</span> : null}
            </div>
            <div className="admin-retention">
              <strong>{t(language, "admin.retention.days", { days: stringValue(retention.retention_days, "90") })}</strong>
              <span>{t(language, "admin.retention.detail", { events: stringValue(retention.retained_events, "0"), candidates: stringValue(retention.prune_candidates, "0") })}</span>
            </div>
          </AdminPanel>
        </details>

        <details className="admin-accordion-details border border-border rounded-lg bg-card p-4">
          <summary className="font-semibold text-base cursor-pointer mb-3"><h2 className="inline font-semibold text-base">{t(language, "admin.panel.runtimeTrust")} · {t(language, "admin.panel.contracts")}</h2></summary>
          <AdminPanel title={t(language, "admin.panel.runtimeTrust")} eyebrow={t(language, "admin.panel.contracts")}>
            <RuntimeTrustPanel runtime={agentRuntimeQ.data?.data as ApiRecord | undefined} registry={toolRegistryQ.data?.data as ApiRecord | undefined} language={language} />
          </AdminPanel>
        </details>
      </section>
    </main>
  );
}

function useAdminConsoleData(filters: AdminFilterState) {
  const auditFilters = React.useMemo<AdminAuditFilters>(() => ({
    q: filters.q || undefined,
    actor: filters.actor || undefined,
    action: filters.action || undefined,
    severity: filters.severity || undefined,
    limit: filters.limit,
  }), [filters]);

  return {
    summaryQ: useQuery({ queryKey: ["adminSummary"], queryFn: latticeApi.adminSummary }),
    statsQ: useQuery({ queryKey: ["adminStats"], queryFn: latticeApi.adminStats }),
    usersQ: useQuery({ queryKey: ["adminUsers"], queryFn: latticeApi.adminUsers }),
    auditQ: useQuery({ queryKey: ["adminAudit", auditFilters], queryFn: () => latticeApi.adminAudit(auditFilters) }),
    securityQ: useQuery({ queryKey: ["adminSecurity"], queryFn: latticeApi.adminSecurity }),
    securityEventsQ: useQuery({ queryKey: ["adminSecurityEvents"], queryFn: () => latticeApi.adminSecurityEvents(50) }),
    policiesQ: useQuery({ queryKey: ["adminPolicies"], queryFn: latticeApi.adminPolicies }),
    rolesQ: useQuery({ queryKey: ["adminRoles"], queryFn: latticeApi.adminRoles }),
    retentionQ: useQuery({ queryKey: ["adminLogRetention"], queryFn: latticeApi.adminLogRetention }),
    indexQ: useQuery({ queryKey: ["indexStatus"], queryFn: latticeApi.indexStatus }),
    agentRuntimeQ: useQuery({ queryKey: ["agentRuntime"], queryFn: latticeApi.agentRuntime }),
    toolRegistryQ: useQuery({ queryKey: ["toolRegistryDiagnostics"], queryFn: latticeApi.toolRegistryDiagnostics }),
    healthSummaryQ: useQuery({ queryKey: ["adminHealthSummary"], queryFn: latticeApi.adminHealthSummary }),
  };
}

function RuntimeTrustPanel({ runtime, registry, language }: { runtime?: ApiRecord; registry?: ApiRecord; language: "ko" | "en" }) {
  const runtimeInfo = (runtime?.runtime || {}) as ApiRecord;
  const health = (runtime?.health || {}) as ApiRecord;
  const diagnostics = (registry?.diagnostics || {}) as ApiRecord;
  const ready = Boolean(runtimeInfo.ready);
  const registryReady = Boolean(diagnostics.ready);
  const blocking = stringValue(runtimeInfo.unavailable_reason, ready ? t(language, "admin.runtime.readyDetail") : t(language, "admin.runtime.blockedFallback"));
  return (
    <div className="admin-runtime-trust">
      <div className="admin-runtime-row">
        <strong>{t(language, "admin.runtime.agent")}</strong>
        <span className={ready ? "is-ok" : "is-warn"}>{ready ? t(language, "admin.status.ready") : t(language, "admin.status.unavailable")}</span>
        <small>{blocking}</small>
      </div>
      <div className="admin-runtime-row">
        <strong>{t(language, "admin.runtime.tools")}</strong>
        <span className={registryReady ? "is-ok" : "is-warn"}>{registryReady ? t(language, "admin.runtime.aligned") : t(language, "admin.runtime.drift")}</span>
        <small>
          {t(language, "admin.runtime.toolCounts", {
            registered: stringValue(diagnostics.registered_tools, "0"),
            governed: stringValue(diagnostics.governed_tools, "0"),
            described: stringValue(diagnostics.described_tools, "0"),
          })}
        </small>
      </div>
      <div className="admin-policy-strip">
        <span>{t(language, "admin.runtime.mode", { mode: stringValue(runtimeInfo.mode, "unknown") })}</span>
        <span>{t(language, "admin.runtime.execution", { mode: stringValue(runtimeInfo.execution_mode, "unknown") })}</span>
        <span>{t(language, "admin.runtime.health", { status: stringValue(health.status, "unknown") })}</span>
      </div>
    </div>
  );
}

function AdminLogFilters({
  filters,
  matched,
  onChange,
  language,
}: {
  filters: AdminFilterState;
  matched?: ApiRecord;
  onChange: React.Dispatch<React.SetStateAction<AdminFilterState>>;
  language: "ko" | "en";
}) {
  return (
    <div className="admin-log-filters" aria-label={t(language, "admin.filters.label")}>
      <label>
        <Search className="h-3.5 w-3.5" />
        <input
          value={filters.q}
          onChange={(event) => onChange((current) => ({ ...current, q: event.target.value }))}
          placeholder={t(language, "admin.filters.search")}
          aria-label={t(language, "admin.filters.searchAria")}
        />
      </label>
      <label>
        <ListFilter className="h-3.5 w-3.5" />
        <select
          value={filters.severity}
          onChange={(event) => onChange((current) => ({ ...current, severity: event.target.value }))}
          aria-label={t(language, "admin.filters.severityAria")}
        >
          <option value="">{t(language, "admin.filters.all")}</option>
          <option value="informational">{t(language, "admin.filters.informational")}</option>
          <option value="notice">{t(language, "admin.filters.notice")}</option>
          <option value="warning">{t(language, "admin.filters.warning")}</option>
          <option value="high">{t(language, "admin.filters.high")}</option>
        </select>
      </label>
      <span>{t(language, "admin.filters.matched", { count: stringValue(matched?.matched_events, "0") })}</span>
    </div>
  );
}

function AdminPanel({ eyebrow, title, children }: { eyebrow: string; title: string; children: React.ReactNode }) {
  return (
    <section className="admin-panel">
      <div className="admin-panel-head">
        <span>{eyebrow}</span>
        <h2>{title}</h2>
      </div>
      {children}
    </section>
  );
}

function AdminList({ items, empty, render }: { items: unknown[]; empty: string; render: (item: unknown) => React.ReactNode }) {
  if (!items.length) return <div className="admin-empty">{empty}</div>;
  return <div className="admin-list">{items.map((item, index) => <div key={index} className="admin-list-row">{render(item)}</div>)}</div>;
}

function renderLogRow(event: ApiRecord, language: "ko" | "en") {
  const action = stringValue(event.action || event.event || event.type || event.name, t(language, "admin.log.event"));
  const actor = stringValue(event.actor || event.user || event.user_id || event.workspace_id, t(language, "admin.log.system"));
  const when = stringValue(event.timestamp || event.time || event.created_at || event.ts, t(language, "admin.log.recently"));
  return (
    <>
      <strong>{action}</strong>
      <span>{actor} · {when}</span>
    </>
  );
}

// `sourceLabel` lived here unused: the rebuilt console reports each service by
// the status the service itself returns, not by where the answer came from, so
// there is no longer a "live / loading / unavailable" line to label. Deleted
// rather than left dormant — dead code reads as a wiring mistake.

function adminStatusLabel(data: unknown, key: string) {
  const record = isRecord(data) ? data : {};
  return textValue(record, [key, "health", "state", "overall_status"]);
}

function indexDetail(data: unknown, language: "ko" | "en") {
  const record = isRecord(data) ? data : {};
  const docs = record.documents ?? record.document_count ?? record.docs;
  const chunks = record.chunks ?? record.chunk_count ?? record.vectors;
  if (docs !== undefined || chunks !== undefined) {
    return `${stringValue(docs, "0")} docs · ${stringValue(chunks, "0")} chunks`;
  }
  return textValue(record, ["message", "detail", "status"], t(language, "admin.index.ready"));
}

function summaryText(data: unknown) {
  const record = isRecord(data) ? data : {};
  return textValue(record, ["summary", "message", "status", "detail"]);
}

function stringValue(value: unknown, fallback = "") {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  return fallback;
}

function textValue(record: ApiRecord, keys: string[], fallback = "") {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return fallback;
}
