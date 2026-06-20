import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, ArrowLeft, ListFilter, RotateCcw, Search, ServerCog, ShieldCheck, Users } from "lucide-react";
import { latticeApi, type AdminAuditFilters, type ApiResult } from "@/api/client";
import { Button } from "@/components/ui/button";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { useAppStore } from "@/store/appStore";
import { asArray } from "@/lib/utils";
import { t } from "@/i18n";

type ApiRecord = Record<string, unknown>;
type AdminFilterState = Required<Pick<AdminAuditFilters, "q" | "actor" | "action" | "severity">> & { limit: number };

export function AdminConsole({ onBack }: { onBack: () => void }) {
  const qc = useQueryClient();
  const language = useAppStore((state) => state.language);
  const [filters, setFilters] = React.useState<AdminFilterState>({ q: "", actor: "", action: "", severity: "", limit: 50 });
  const { summaryQ, statsQ, usersQ, auditQ, securityQ, securityEventsQ, policiesQ, rolesQ, retentionQ, indexQ, agentRuntimeQ, toolRegistryQ } = useAdminConsoleData(filters);
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

      <section className="admin-metrics" aria-label={t(language, "admin.overview")}>
        <AdminMetric icon={<Users className="h-4 w-4" />} label={t(language, "admin.metric.users")} value={String(users.length)} detail={sourceLabel(usersQ.data, language)} />
        <AdminMetric
          icon={<Activity className="h-4 w-4" />}
          label={t(language, "admin.metric.logs")}
          value={String(auditEvents.length + securityEvents.length)}
          detail={sourceLabel(auditQ.data, language)}
        />
        <AdminMetric
          icon={<ShieldCheck className="h-4 w-4" />}
          label={t(language, "admin.metric.security")}
          value={adminStatusLabel(securityQ.data?.data, "status") || (securityQ.data?.ok ? t(language, "admin.status.ready") : t(language, "admin.status.unavailable"))}
          detail={sourceLabel(securityQ.data, language)}
        />
        <AdminMetric
          icon={<ServerCog className="h-4 w-4" />}
          label={t(language, "admin.metric.index")}
          value={adminStatusLabel(indexQ.data?.data, "status") || (indexQ.data?.ok ? t(language, "admin.status.indexed") : t(language, "admin.status.unknown"))}
          detail={indexDetail(indexQ.data?.data, language)}
        />
      </section>

      <section className="admin-grid">
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

        <AdminPanel title={t(language, "admin.panel.logs")} eyebrow={t(language, "admin.panel.audit")}>
          <AdminLogFilters language={language} filters={filters} onChange={setFilters} matched={(auditQ.data?.data as ApiRecord | undefined)?.filters as ApiRecord | undefined} />
          <AdminList
            items={auditEvents.slice(0, 8)}
            empty={t(language, "admin.empty.audit")}
            render={(item) => renderLogRow(item as ApiRecord, language)}
          />
        </AdminPanel>

        <AdminPanel title={t(language, "admin.panel.securityEvents")} eyebrow={t(language, "admin.panel.protection")}>
          <AdminList
            items={securityEvents.slice(0, 8)}
            empty={t(language, "admin.empty.security")}
            render={(item) => renderLogRow(item as ApiRecord, language)}
          />
        </AdminPanel>

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

        <AdminPanel title={t(language, "admin.panel.runtimeTrust")} eyebrow={t(language, "admin.panel.contracts")}>
          <RuntimeTrustPanel runtime={agentRuntimeQ.data?.data as ApiRecord | undefined} registry={toolRegistryQ.data?.data as ApiRecord | undefined} language={language} />
        </AdminPanel>

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

function AdminMetric({ icon, label, value, detail }: { icon: React.ReactNode; label: string; value: string; detail: string }) {
  return (
    <div className="admin-metric">
      <div>{icon}</div>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
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

function sourceLabel(result: ApiResult<unknown> | undefined, language: "ko" | "en") {
  if (!result) return t(language, "admin.source.loading");
  return result.ok ? t(language, "admin.source.live") : result.error || t(language, "admin.status.unavailable");
}

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

function isRecord(value: unknown): value is ApiRecord {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function textValue(record: ApiRecord, keys: string[], fallback = "") {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return fallback;
}
