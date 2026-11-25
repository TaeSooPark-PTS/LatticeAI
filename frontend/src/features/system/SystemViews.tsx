import { EmptyState, EntityList, KeyValueList, StatGrid, StructuredView } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { t, type Language } from "@/i18n";
import { asArray, isRecord, shortId } from "@/lib/utils";
import { useAppStore } from "@/store/appStore";

export function localizedTextValue(language: Language, value: unknown, fallback: string) {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "boolean") return t(language, value ? "system.value.enabled" : "system.value.disabled");
  return String(value);
}

export function StatusCard({ title, status, detail }: { title: string; status: string; detail: string }) {
  const variant = /unavailable|failed|denied|disabled|not enabled|사용 불가|실패|거부|비활성|활성화되지 않음/i.test(status) ? "warning" : "success";
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="font-medium">{title}</div>
        <Badge variant={variant}>{status}</Badge>
      </div>
      <p className="mt-2 text-sm text-muted-foreground">{detail}</p>
    </div>
  );
}

export function SignInOptionsView({ data }: { data: Record<string, unknown> }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const enabled = Boolean(data.enabled) || asArray(data.providers).length > 0;
  return (
    <div className="space-y-3">
      <StatusCard
        title={t(language, enabled ? "system.account.signIn.sso" : "system.account.signIn.localOnly")}
        status={t(language, enabled ? "system.value.enabled" : "system.storage.local.badge")}
        detail={t(language, enabled ? "system.account.signIn.ssoDetail" : "system.account.signIn.localOnlyDetail")}
      />
      {mode === "basic" ? null : <StructuredView value={data} />}
    </div>
  );
}

export function PresenceView({ data }: { data: Record<string, unknown> }) {
  const language = useAppStore((state) => state.language);
  const rows = asArray<Record<string, unknown>>(data.presence || data.clients || data);
  if (!rows.length) return <EmptyState title={t(language, "system.presence.empty")} detail={t(language, "system.presence.emptyDetail")} />;
  return <EntityList items={rows} titleKey="user" metaKey="workspace_id" />;
}

export function DeviceIdentityView({ data }: { data: Record<string, unknown> }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const publicKey = localizedTextValue(language, data.public_key, "");
  if (mode === "basic") {
    return (
      <div className="space-y-3">
        <StatusCard title={t(language, "system.device.thisMac")} status={t(language, "system.value.trusted")} detail={t(language, "system.device.thisMacDetail")} />
        <Badge variant="muted">{localizedTextValue(language, data.algorithm, t(language, "system.value.localIdentity"))}</Badge>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="success">{t(language, "system.value.localDevice")}</Badge>
        <Badge variant="muted">{localizedTextValue(language, data.algorithm, t(language, "system.value.identityKey"))}</Badge>
      </div>
      <KeyValueList data={{
        device_id: data.device_id || data.id || t(language, "system.value.notReported"),
        fingerprint: data.fingerprint || t(language, "system.value.notReported"),
        public_key: publicKey ? shortId(publicKey.replace(/\s+/g, " "), 72) : t(language, "system.value.notReported"),
      }} />
    </div>
  );
}

export function HealthView({ data }: { data: Record<string, unknown> }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  if (mode === "basic") {
    return (
      <StatusCard
        title={t(language, "system.health.title")}
        status={t(language, "system.health.ok")}
        detail={t(language, "system.health.detail")}
      />
    );
  }
  return (
    <div className="space-y-3">
      <StatGrid stats={[
        { label: t(language, "system.health.status"), value: data.status || data.ok || t(language, "system.value.reported") },
        { label: t(language, "system.health.version"), value: data.version || t(language, "system.value.notReported") },
        { label: t(language, "system.health.mode"), value: data.mode || data.environment || t(language, "system.value.local") },
        { label: t(language, "system.health.port"), value: data.port || data.backend_port || t(language, "system.value.configured") },
      ]} />
      <StructuredView value={data} />
    </div>
  );
}

export function StorageView({ data, mode, language }: { data: Record<string, unknown>; mode: string; language: Language }) {
  const active = isRecord(data.active) ? data.active : data;
  const postgres = isRecord(data.postgres) ? data.postgres : {};
  const backup = isRecord(data.backup_health) ? data.backup_health : {};
  const vector = active.vector_search || active.vector || data.vector_search || data.sqlite_vec;
  const postgresAvailable = Boolean(postgres.available || postgres.connected || postgres.enabled);
  const searchOn = Boolean(vector) && String(vector).toLowerCase() !== "not reported";
  if (mode === "basic") {
    return (
      <div className="space-y-3">
        <StatusCard
          title={t(language, "system.storage.local.title")}
          status={t(language, "system.storage.local.badge")}
          detail={t(language, "system.storage.local.detail")}
        />
        <StatusCard
          title={t(language, "system.storage.search.title")}
          status={searchOn ? t(language, "system.storage.search.on") : t(language, "system.storage.search.pending")}
          detail={searchOn ? t(language, "system.storage.search.detailOn") : t(language, "system.storage.search.detailPending")}
        />
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <StatGrid stats={[
        { label: t(language, "system.storage.activeEngine"), value: active.engine || data.engine || t(language, "system.storage.sqlite") },
        { label: t(language, "system.storage.sqliteDefault"), value: active.engine === "postgres" ? t(language, "system.value.scaleMode") : t(language, "system.value.enabled") },
        { label: t(language, "system.storage.vector"), value: vector || t(language, "system.value.notReported") },
        { label: t(language, "system.storage.postgres"), value: postgresAvailable ? t(language, "system.value.available") : t(language, "system.value.optional") },
      ]} />
      <div className="flex flex-col sm:flex-row gap-3">
        <StatusCard title={t(language, "system.storage.sqlite")} status={active.available === false ? t(language, "system.value.unavailable") : t(language, "system.value.default")} detail={localizedTextValue(language, active.reason || active.path || data.path, t(language, "system.storage.sqliteDetail"))} />
        <StatusCard title={t(language, "system.storage.vector")} status={localizedTextValue(language, vector, t(language, "system.value.reported"))} detail={localizedTextValue(language, active.vector_reason || active.sqlite_vec_reason || data.vector_reason, t(language, "system.storage.vectorDetail"))} />
        <StatusCard title={t(language, "system.storage.postgres")} status={postgresAvailable ? t(language, "system.value.available") : t(language, "system.value.notEnabled")} detail={localizedTextValue(language, postgres.reason || postgres.dsn || postgres.status, t(language, "system.storage.postgresDetail"))} />
      </div>
      {Object.keys(backup).length ? <StructuredView value={{ backup_health: backup }} /> : null}
    </div>
  );
}

export function BackupHealthView({ data }: { data: Record<string, unknown> }) {
  const language = useAppStore((state) => state.language);
  return (
    <div className="space-y-3">
      <StatGrid stats={[
        { label: t(language, "system.backup.available"), value: data.available === false ? t(language, "system.value.no") : t(language, "system.value.yes") },
        { label: t(language, "system.backup.backups"), value: data.count || data.backups || 0 },
        { label: t(language, "system.backup.encrypted"), value: data.encrypted_archives || 0 },
        { label: t(language, "system.backup.zip"), value: data.zip_backups || 0 },
      ]} />
      <KeyValueList data={{
        directory: data.directory || t(language, "system.value.notReported"),
        latest: data.latest || t(language, "system.value.noneReported"),
        last_verified: data.last_verified || data.verified_at || t(language, "system.value.notReported"),
        failure: data.error || data.reason || t(language, "system.value.noneReported"),
      }} />
    </div>
  );
}

export function HardeningView({ data }: { data: Record<string, unknown> }) {
  const language = useAppStore((state) => state.language);
  const startup = isRecord(data.startup) ? data.startup : {};
  const privacy = isRecord(data.privacy) ? data.privacy : {};
  const storage = isRecord(data.storage) ? data.storage : {};
  const backup = isRecord(data.backup) ? data.backup : {};
  const identity = isRecord(data.device_identity) ? data.device_identity : {};
  const permissions = isRecord(data.permissions) ? data.permissions : {};
  return (
    <div className="space-y-3">
      <StatGrid stats={[
        { label: t(language, "system.hardening.version"), value: data.version || t(language, "system.value.reported") },
        { label: t(language, "system.hardening.localOnly"), value: privacy.local_only_default ?? startup.local_only_default ?? t(language, "system.value.reported") },
        { label: t(language, "system.hardening.storage"), value: isRecord(storage.active) ? (storage.active as Record<string, unknown>).engine : t(language, "system.value.reported") },
        { label: t(language, "system.hardening.backups"), value: backup.count || backup.available || t(language, "system.value.reported") },
      ]} />
      <div className="flex flex-col sm:flex-row flex-wrap gap-3">
        <StatusCard title={t(language, "system.hardening.startup")} status={t(language, startup.network_exposed ? "system.value.networkExposed" : "system.value.localOnly")} detail={t(language, "system.hardening.startupDetail")} />
        <StatusCard title={t(language, "system.hardening.integrations")} status={t(language, privacy.local_only_default === false ? "system.value.reviewRequired" : "system.value.optIn")} detail={t(language, "system.hardening.integrationsDetail")} />
        <StatusCard title={t(language, "system.hardening.identity")} status={localizedTextValue(language, identity.algorithm || identity.fingerprint, t(language, "system.value.reported"))} detail={localizedTextValue(language, identity.storage, t(language, "system.hardening.identityStorage"))} />
        <StatusCard title={t(language, "system.hardening.permissions")} status={t(language, permissions.destructive_restore_requires_confirmation === false ? "system.value.reviewRequired" : "system.value.guarded")} detail={t(language, "system.hardening.permissionsDetail")} />
      </div>
    </div>
  );
}

export function SecurityView({ data }: { data: Record<string, unknown> }) {
  const language = useAppStore((state) => state.language);
  const cards = isRecord(data.cards) ? data.cards : {};
  const severities = isRecord(data.severity_counts) ? data.severity_counts : {};
  return (
    <div className="space-y-3">
      <StatGrid stats={[
        { label: t(language, "system.security.eventsToday"), value: cards.events_today || 0 },
        { label: t(language, "system.security.highRisk"), value: cards.high_risk_events || severities.high || 0 },
        { label: t(language, "system.security.review"), value: cards.review_required || 0 },
        { label: t(language, "system.security.riskRate"), value: data.risk_rate || 0 },
      ]} />
      <StructuredView value={{ severity_counts: severities, sensitive_fields: data.field_counts || {} }} />
    </div>
  );
}
