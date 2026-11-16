import { Network } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EntityList, KeyValueList, ModeGate, OperationResult, StructuredView } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { t } from "@/i18n";
import { asArray } from "@/lib/utils";
import { useAppStore } from "@/store/appStore";
import {
  DeviceIdentityView,
  HardeningView,
  PresenceView,
  SecurityView,
} from "./SystemViews";
import {
  useActivityPanel,
  useAdminPanel,
  useNetworkPanel,
  useSnapshotsPanel,
} from "./hooks/useSystemPanels";

export function SnapshotsPanel() {
  const language = useAppStore((state) => state.language);
  const {
    snaps,
    timeline,
    name,
    setName,
    before,
    setBefore,
    after,
    setAfter,
    create,
    compare,
    rows,
  } = useSnapshotsPanel(language);

  return (
    <div className="flex flex-col gap-4">
      <DataPanel title={t(language, "system.snapshots.title")} result={snaps.data}>
        {() => (
          <div className="space-y-2">
            {rows.map((snap) => {
              const id = String(snap.id || snap.snapshot_id);
              return (
                <div key={id} className="rounded-md border border-border p-3">
                  <div className="font-medium">{String(snap.name || id)}</div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <ActionButton label={t(language, "system.snapshots.export")} action={() => latticeApi.exportSnapshot(id)} />
                    <ActionButton label={t(language, "system.snapshots.mergeRestore")} action={() => latticeApi.restoreSnapshot(id)} variant="outline" />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </DataPanel>
      <Card>
        <CardHeader>
          <CardTitle>{t(language, "system.snapshots.actions")}</CardTitle>
          <CardDescription>{t(language, "system.snapshots.actionsHint")}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t(language, "system.snapshots.namePlaceholder")} />
          <Button onClick={() => create.mutate()} disabled={create.isPending}>{t(language, "system.snapshots.create")}</Button>
          <div className="flex flex-col sm:flex-row gap-2">
            <Input value={before} onChange={(e) => setBefore(e.target.value)} placeholder={t(language, "system.snapshots.beforeId")} />
            <Input value={after} onChange={(e) => setAfter(e.target.value)} placeholder={t(language, "system.snapshots.afterId")} />
          </div>
          <Button variant="outline" onClick={() => compare.mutate()} disabled={!before || !after || compare.isPending}>{t(language, "system.snapshots.compare")}</Button>
          {compare.data ? <OperationResult result={compare.data} successLabel={t(language, "system.snapshots.compareDone")} /> : null}
        </CardContent>
      </Card>
      <DataPanel title={t(language, "system.snapshots.timeline")} result={timeline.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).events || data} titleKey="event" metaKey="type" limit={14} />}
      </DataPanel>
    </div>
  );
}

export function ActivityPanel() {
  const language = useAppStore((state) => state.language);
  const { feed, presence } = useActivityPanel();
  return (
    <div className="flex flex-col gap-4">
      <DataPanel title={t(language, "system.activity.feed")} result={feed.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).events} titleKey="event_type" metaKey="area" limit={14} />}
      </DataPanel>
      <DataPanel title={t(language, "system.activity.presence")} result={presence.data}>
        {(data) => <PresenceView data={data as Record<string, unknown>} />}
      </DataPanel>
    </div>
  );
}

export function NetworkPanel() {
  const language = useAppStore((state) => state.language);
  const {
    identity,
    peers,
    name,
    setName,
    baseUrl,
    setBaseUrl,
    publicKey,
    setPublicKey,
    pair,
  } = useNetworkPanel();

  return (
    <div className="flex flex-col gap-4">
      <DataPanel title={t(language, "system.network.identity")} result={identity.data}>
        {(data) => <DeviceIdentityView data={data as Record<string, unknown>} />}
      </DataPanel>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Network className="h-4 w-4" /> {t(language, "system.network.pair")}</CardTitle>
          <CardDescription>{t(language, "system.network.pairHint")}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t(language, "system.network.deviceName")} />
          <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder={t(language, "system.network.address")} />
          <Input value={publicKey} onChange={(e) => setPublicKey(e.target.value)} placeholder={t(language, "system.network.publicKey")} />
          <Button disabled={!name || !baseUrl || !publicKey || pair.isPending} onClick={() => pair.mutate()}>{t(language, "system.network.pair")}</Button>
          {pair.data ? <OperationResult result={pair.data} successLabel={t(language, "system.network.pairDone")} /> : null}
        </CardContent>
      </Card>
      <DataPanel title={t(language, "system.network.peers")} result={peers.data} className="xl:col-span-2">
        {(data) => (
          <div className="grid gap-2">
            {asArray<Record<string, unknown>>((data as Record<string, unknown>).peers).map((peer) => {
              const id = String(peer.peer_id || peer.id);
              return (
                <div key={id} className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3">
                  <div>
                    <div className="font-medium">{String(peer.name || id)}</div>
                    <div className="text-sm text-muted-foreground">{String(peer.base_url || "")}</div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <ActionButton label={t(language, "system.network.pushWorkspace")} action={() => latticeApi.pushPeer(id, useAppStore.getState().workspaceId)} />
                    <ActionButton label={t(language, "system.network.unpair")} action={() => latticeApi.unpairPeer(id)} invalidate={["networkPeers"]} variant="destructive" />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </DataPanel>
    </div>
  );
}

export function AdminPanel() {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const { summary, users, audit, roles, policies, hardening, security, vpc } = useAdminPanel();

  if (mode !== "admin") {
    return <ModeGate title={t(language, "system.admin.controls")} detail={t(language, "system.admin.controlsDetail")} target="admin" />;
  }
  return (
    <div className="flex flex-col gap-4">
      <DataPanel title={t(language, "system.admin.summary")} result={summary.data}>{(data) => <KeyValueList data={data as Record<string, unknown>} />}</DataPanel>
      <DataPanel title={t(language, "system.admin.users")} result={users.data}>{(data) => <EntityList items={data} titleKey="email" metaKey="role" />}</DataPanel>
      <DataPanel title={t(language, "system.admin.audit")} result={audit.data}>{(data) => <EntityList items={(data as Record<string, unknown>).recent_events || data} titleKey="act" metaKey="sev" />}</DataPanel>
      <DataPanel title={t(language, "system.admin.roles")} result={roles.data}>{(data) => <EntityList items={(data as Record<string, unknown>).roles || data} titleKey="role" metaKey="members" />}</DataPanel>
      <DataPanel title={t(language, "system.admin.policies")} result={policies.data}>{(data) => <EntityList items={(data as Record<string, unknown>).policies || data} titleKey="label" metaKey="enforced" />}</DataPanel>
      <DataPanel title={t(language, "system.admin.hardening")} result={hardening.data}>{(data) => <HardeningView data={data as Record<string, unknown>} />}</DataPanel>
      <DataPanel title={t(language, "system.admin.security")} result={security.data}>{(data) => <SecurityView data={data as Record<string, unknown>} />}</DataPanel>
      <DataPanel title={t(language, "system.admin.vpc")} result={vpc.data}>
        {(data) => (
          <div className="space-y-2">
            <Badge variant="muted">{t(language, "system.admin.communityUnavailable")}</Badge>
            <StructuredView value={data} />
          </div>
        )}
      </DataPanel>
    </div>
  );
}
