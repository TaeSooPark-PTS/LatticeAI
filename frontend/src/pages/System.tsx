import * as React from "react";
// Route-scoped copy: importing the namespace registers it into the shared
// table and keeps it inside this lazy chunk instead of the entry bundle.
import "@/i18n/workspace";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Network, ShieldCheck, UserCircle, Users } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EmptyState, EntityList, KeyValueList, ModeGate, OperationResult, StatGrid, StructuredView, Tabs } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { asArray, isRecord, shortId } from "@/lib/utils";
import { clearScopedClientState } from "@/queryClient";
import { navigateHash } from "@/features/brain/navigation";
import { NetworkBoundaryPanel } from "@/components/NetworkBoundaryPanel";
import { PermissionModePanel } from "@/components/PermissionModePanel";

type SystemTab = "account" | "workspaces" | "snapshots" | "activity" | "network" | "settings" | "admin";

type SystemGroup = "identity" | "data" | "system";

// Seven equal-looking tabs made someone read all seven to find one. They are
// the same seven destinations, sorted into the three questions people actually
// arrive with: who am I, where is my data, how does this machine behave.
const groups: Array<{ id: SystemGroup; labelKey: string }> = [
  { id: "identity", labelKey: "system.group.identity" },
  { id: "data", labelKey: "system.group.data" },
  { id: "system", labelKey: "system.group.system" },
];

const tabs: Array<{ id: SystemTab; labelKey: string; group: SystemGroup }> = [
  { id: "account", labelKey: "system.tab.account", group: "identity" },
  { id: "workspaces", labelKey: "system.tab.workspaces", group: "identity" },
  { id: "snapshots", labelKey: "system.tab.snapshots", group: "data" },
  { id: "activity", labelKey: "system.tab.activity", group: "data" },
  { id: "settings", labelKey: "system.tab.settings", group: "system" },
  { id: "network", labelKey: "system.tab.network", group: "system" },
  { id: "admin", labelKey: "system.tab.admin", group: "system" },
];

export function SystemPage({ initialTab }: { initialTab?: string }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const [tab, setTab] = React.useState<SystemTab>((initialTab as SystemTab) || "account");
  React.useEffect(() => {
    if (tabs.some((item) => item.id === initialTab)) setTab(initialTab as SystemTab);
  }, [initialTab]);
  const visibleTabs = mode === "basic"
    ? tabs.filter((item) => item.id === "account" || item.id === "workspaces" || item.id === "snapshots" || item.id === "settings")
    : tabs;
  const selectTab = (next: SystemTab) => {
    setTab(next);
    navigateHash("/" + ({
      account: "account",
      workspaces: "workspace-admin",
      snapshots: "snapshots",
      activity: "activity",
      network: "network",
      settings: "settings",
      admin: "system-admin",
    } as const)[next]);
  };
  return (
    <div className="product-page settings-page space-y-5 pb-8">
      <header className="page-hero">
        <div className="page-kicker"><ShieldCheck className="h-4 w-4" /> {t(language, "system.kicker")}</div>
        <h1 className="page-title">{t(language, "system.title")}</h1>
        <p className="page-copy">{t(language, "system.body")}</p>
      </header>
      <div className="flex flex-wrap items-end gap-x-6 gap-y-3" data-testid="system-tab-groups">
        {groups.map((group) => {
          const items = visibleTabs.filter((item) => item.group === group.id);
          if (!items.length) return null;
          const labelId = `system-group-${group.id}`;
          return (
            <div key={group.id} className="flex flex-col gap-1.5">
              <span id={labelId} className="px-1 text-xs font-medium text-muted-foreground">
                {t(language, group.labelKey)}
              </span>
              <Tabs
                ariaLabelledBy={labelId}
                tabs={items.map((item) => ({ id: item.id, label: t(language, item.labelKey) }))}
                value={tab}
                onChange={(id) => selectTab(id as SystemTab)}
              />
            </div>
          );
        })}
      </div>
      {tab === "account" ? <AccountPanel /> : null}
      {tab === "workspaces" ? <WorkspacePanel /> : null}
      {tab === "settings" ? <SettingsPanel /> : null}
      {tab === "snapshots" ? <SnapshotsPanel /> : null}
      {tab === "activity" ? <ActivityPanel /> : null}
      {tab === "network" ? <NetworkPanel /> : null}
      {tab === "admin" ? <AdminPanel /> : null}
    </div>
  );
}

function AccountPanel() {
  const qc = useQueryClient();
  const language = useAppStore((state) => state.language);
  const setWorkspaceId = useAppStore((state) => state.setWorkspaceId);
  const profile = useQuery({ queryKey: ["profile"], queryFn: latticeApi.profile });
  const sso = useQuery({ queryKey: ["sso"], queryFn: latticeApi.ssoConfig });
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [name, setName] = React.useState("");
  const [nickname, setNickname] = React.useState("");
  const [newPassword, setNewPassword] = React.useState("");
  const resetIdentityScope = (result: { ok: boolean }) => {
    if (!result.ok) return;
    clearScopedClientState();
    setWorkspaceId(null);
  };
  const login = useMutation({ mutationFn: () => latticeApi.login(email, password), onSuccess: resetIdentityScope });
  const register = useMutation({ mutationFn: () => latticeApi.register({ email, password, name, nickname }), onSuccess: resetIdentityScope });
  const saveProfile = useMutation({ mutationFn: () => latticeApi.updateProfile({ name, nickname }), onSuccess: () => qc.invalidateQueries({ queryKey: ["profile"] }) });
  const changePassword = useMutation({ mutationFn: () => latticeApi.changePassword(password, newPassword) });
  return (
    <div className="flex flex-col gap-4">
      <DataPanel title={t(language, "system.account.profile")} result={profile.data}>
        {(data) => <KeyValueList data={data as Record<string, unknown>} />}
      </DataPanel>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><UserCircle className="h-4 w-4" /> {t(language, "system.account.title")}</CardTitle>
          <CardDescription>{t(language, "system.account.detail")}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder={t(language, "system.account.email")} />
          <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder={t(language, "system.account.password")} />
          <div className="flex flex-col sm:flex-row gap-2">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t(language, "system.account.name")} />
            <Input value={nickname} onChange={(e) => setNickname(e.target.value)} placeholder={t(language, "system.account.nickname")} />
          </div>
          <Input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder={t(language, "system.account.newPassword")} />
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => login.mutate()} disabled={!email || !password || login.isPending}>{t(language, "system.account.login")}</Button>
            <Button variant="outline" onClick={() => register.mutate()} disabled={!email || !password || register.isPending}>{t(language, "system.account.register")}</Button>
            <Button variant="outline" onClick={() => saveProfile.mutate()} disabled={saveProfile.isPending}>{t(language, "system.account.save")}</Button>
            <Button variant="outline" onClick={() => changePassword.mutate()} disabled={!password || !newPassword || changePassword.isPending}>{t(language, "system.account.changePassword")}</Button>
            <ActionButton label={t(language, "system.account.logout")} action={() => latticeApi.logout()} onSuccess={resetIdentityScope} />
          </div>
          {[login.data, register.data, saveProfile.data, changePassword.data].filter(Boolean).map((item, i) => (
            <OperationResult key={i} result={item} successLabel={t(language, "system.account.requestDone")} />
          ))}
        </CardContent>
      </Card>
      <DataPanel title={t(language, "system.account.signInOptions")} result={sso.data}>
        {(data) => <SignInOptionsView data={data as Record<string, unknown>} />}
      </DataPanel>
    </div>
  );
}

/**
 * How you get in, as a sentence. The payload underneath is a provider list and
 * an OIDC discovery URL — real, but not an answer to the question this panel is
 * on the screen to answer. Advanced mode still gets the payload.
 */
function SignInOptionsView({ data }: { data: Record<string, unknown> }) {
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

function WorkspacePanel() {
  const qc = useQueryClient();
  const language = useAppStore((state) => state.language);
  const { setWorkspaceId } = useAppStore();
  const registry = useQuery({ queryKey: ["workspaceRegistry"], queryFn: latticeApi.workspaceRegistry });
  const invites = useQuery({ queryKey: ["invitations"], queryFn: latticeApi.invitations });
  const [orgName, setOrgName] = React.useState("");
  const [inviteEmail, setInviteEmail] = React.useState("");
  const [inviteToken, setInviteToken] = React.useState("");
  const createOrg = useMutation({ mutationFn: () => latticeApi.createOrg(orgName), onSuccess: () => qc.invalidateQueries({ queryKey: ["workspaceRegistry"] }) });
  const createInvite = useMutation({ mutationFn: () => latticeApi.createInvitation({ email: inviteEmail || null, role: "member", expires_hours: 168 }), onSuccess: () => qc.invalidateQueries({ queryKey: ["invitations"] }) });
  const accept = useMutation({ mutationFn: () => latticeApi.acceptInvitation(inviteToken), onSuccess: () => qc.invalidateQueries({ queryKey: ["workspaceRegistry"] }) });
  const workspaces = asArray<Record<string, unknown>>((registry.data?.data as Record<string, unknown>)?.workspaces);
  return (
    <div className="flex flex-col gap-4">
      <DataPanel title={t(language, "system.workspace.yours")} result={registry.data}>
        {() => (
          <div className="flex flex-col gap-2">
            {workspaces.map((workspace) => {
              const id = String(workspace.workspace_id || workspace.id);
              return (
                <div key={id} className="rounded-md border border-border p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <div className="font-medium">{String(workspace.name || id)}</div>
                      <div className="text-sm text-muted-foreground">{String(workspace.type || "")} · {String(workspace.your_role || workspace.role || "")}</div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button variant="outline" onClick={() => setWorkspaceId(id)}>{t(language, "system.workspace.use")}</Button>
                      <ActionButton label={t(language, "system.workspace.activate")} action={() => latticeApi.activateWorkspace(id)} invalidate={["workspaceRegistry"]} />
                      <ActionButton label={t(language, "system.workspace.archive")} action={() => latticeApi.archiveWorkspace(id)} invalidate={["workspaceRegistry"]} variant="destructive" />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </DataPanel>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Users className="h-4 w-4" /> {t(language, "system.workspace.organizations")}</CardTitle>
          <CardDescription>{t(language, "system.workspace.organizationsHint")}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <Input value={orgName} onChange={(e) => setOrgName(e.target.value)} placeholder={t(language, "system.workspace.orgPlaceholder")} />
          <Button disabled={!orgName.trim() || createOrg.isPending} onClick={() => createOrg.mutate()}>{t(language, "system.workspace.createOrg")}</Button>
          <Input value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} placeholder={t(language, "system.workspace.inviteeEmail")} />
          <Button variant="outline" disabled={createInvite.isPending} onClick={() => createInvite.mutate()}>{t(language, "system.workspace.createInvite")}</Button>
          <Input value={inviteToken} onChange={(e) => setInviteToken(e.target.value)} placeholder={t(language, "system.workspace.inviteToken")} />
          <Button variant="outline" disabled={!inviteToken.trim() || accept.isPending} onClick={() => accept.mutate()}>{t(language, "system.workspace.acceptInvite")}</Button>
          <DataPanel title={t(language, "system.workspace.invitations")} result={invites.data}>
            {(data) => <EntityList items={(data as Record<string, unknown>).invitations} titleKey="token" metaKey="role" />}
          </DataPanel>
        </CardContent>
      </Card>
    </div>
  );
}

function SnapshotsPanel() {
  const language = useAppStore((state) => state.language);
  const snaps = useQuery({ queryKey: ["snapshots"], queryFn: latticeApi.snapshots });
  const timeline = useQuery({ queryKey: ["timeMachine"], queryFn: latticeApi.timeMachine });
  const [name, setName] = React.useState("");
  const [before, setBefore] = React.useState("");
  const [after, setAfter] = React.useState("");
  const create = useMutation({ mutationFn: () => latticeApi.createSnapshot(name || t(language, "system.snapshots.defaultName")) });
  const compare = useMutation({ mutationFn: () => latticeApi.compareSnapshots(before, after) });
  const rows = asArray<Record<string, unknown>>((snaps.data?.data as Record<string, unknown>)?.snapshots);
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

function ActivityPanel() {
  const language = useAppStore((state) => state.language);
  const feed = useQuery({ queryKey: ["realtimeFeed"], queryFn: latticeApi.realtimeFeed });
  const presence = useQuery({ queryKey: ["presence"], queryFn: latticeApi.presence });
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

function NetworkPanel() {
  const qc = useQueryClient();
  const language = useAppStore((state) => state.language);
  const identity = useQuery({ queryKey: ["networkIdentity"], queryFn: latticeApi.networkIdentity });
  const peers = useQuery({ queryKey: ["networkPeers"], queryFn: latticeApi.networkPeers });
  const [name, setName] = React.useState("");
  const [baseUrl, setBaseUrl] = React.useState("");
  const [publicKey, setPublicKey] = React.useState("");
  const pair = useMutation({
    mutationFn: () => latticeApi.pairPeer({ name, base_url: baseUrl, public_key: publicKey }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["networkPeers"] }),
  });
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

function SettingsPanel() {
  const qc = useQueryClient();
  const { theme, setTheme, mode, setMode } = useAppStore();
  const language = useAppStore((state) => state.language);
  const health = useQuery({ queryKey: ["health"], queryFn: latticeApi.health });
  const sys = useQuery({ queryKey: ["sysinfo"], queryFn: latticeApi.sysinfo });
  const comp = useQuery({ queryKey: ["computerMemory"], queryFn: latticeApi.computerMemory });
  const storage = useQuery({ queryKey: ["brainStorage"], queryFn: latticeApi.brainStorage });
  const backupHealth = useQuery({ queryKey: ["backupHealth"], queryFn: latticeApi.backupHealth });
  const [dsn, setDsn] = React.useState("");
  const [schema, setSchema] = React.useState("lattice_brain");
  const [dockerConsent, setDockerConsent] = React.useState(false);
  const [archivePath, setArchivePath] = React.useState("");
  const [restorePath, setRestorePath] = React.useState("");
  const [archivePassphrase, setArchivePassphrase] = React.useState("");
  const [restoreConfirm, setRestoreConfirm] = React.useState(false);
  const [importConfirm, setImportConfirm] = React.useState(false);
  const docker = useMutation({ mutationFn: (consent: boolean) => latticeApi.dockerPostgres({ consent, dry_run: !consent, port: 5432 }) });
  const migration = useMutation({
    mutationFn: () => latticeApi.migratePostgres({ dsn, schema_name: schema || "lattice_brain", dry_run: true }),
  });
  const archiveCreate = useMutation({
    mutationFn: () => latticeApi.brainArchive({ path: archivePath.trim() || null, passphrase: archivePassphrase }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["backupHealth"] }),
  });
  const archiveInspect = useMutation({
    mutationFn: () => latticeApi.brainArchiveInspect({ path: restorePath, passphrase: archivePassphrase || null }),
  });
  const archiveVerify = useMutation({
    mutationFn: () => latticeApi.brainArchiveVerify({ path: restorePath, passphrase: archivePassphrase }),
  });
  const archiveDryRun = useMutation({
    mutationFn: () => latticeApi.brainArchiveRestore({ path: restorePath, passphrase: archivePassphrase, dry_run: true, confirm: false }),
  });
  const archiveRestore = useMutation({
    mutationFn: () => latticeApi.brainArchiveRestore({ path: restorePath, passphrase: archivePassphrase, dry_run: false, confirm: restoreConfirm }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["brainStorage"] });
      qc.invalidateQueries({ queryKey: ["backupHealth"] });
    },
  });
  const archiveImportDryRun = useMutation({
    mutationFn: () => latticeApi.brainArchiveImport({ path: restorePath, passphrase: archivePassphrase, dry_run: true, confirm: false }),
  });
  const archiveImport = useMutation({
    mutationFn: () => latticeApi.brainArchiveImport({ path: restorePath, passphrase: archivePassphrase, dry_run: false, confirm: importConfirm }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["brainStorage"] });
      qc.invalidateQueries({ queryKey: ["backupHealth"] });
    },
  });
  return (
    <div className="flex flex-col gap-4 max-w-[54rem]">
      <Card>
        <CardHeader>
          <CardTitle>{t(language, "system.panel.appearance")}</CardTitle>
          <CardDescription>{t(language, "system.panel.appearance.hint")}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Button variant={theme === "dark" ? "default" : "outline"} onClick={() => setTheme("dark")}>{t(language, "system.theme.dark")}</Button>
          <Button variant={theme === "light" ? "default" : "outline"} onClick={() => setTheme("light")}>{t(language, "system.theme.light")}</Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t(language, "system.panel.detailLevel")}</CardTitle>
          <CardDescription>{t(language, "system.panel.detailLevel.hint")}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {(["basic", "advanced", "admin"] as const).map((item) => (
            <Button key={item} variant={mode === item ? "default" : "outline"} onClick={() => setMode(item)}>{t(language, `shell.mode.${item}`)}</Button>
          ))}
        </CardContent>
      </Card>
      <PermissionModePanel />
      <NetworkBoundaryPanel />
      <DataPanel title={mode === "basic" ? t(language, "system.panel.brainStatus") : t(language, "system.panel.serverHealth")} result={health.data}>
        {(data) => <HealthView data={data as Record<string, unknown>} />}
      </DataPanel>
      <DataPanel title={mode === "basic" ? t(language, "system.panel.readiness") : t(language, "system.panel.hostTelemetry")} result={sys.data}>
        {(data) => mode === "basic" ? (
          <StatGrid stats={[
            { label: t(language, "system.stat.cpu"), value: `${String((data as Record<string, unknown>).cpu_pct || "0")}%` },
            { label: t(language, "system.stat.memory"), value: `${String((data as Record<string, unknown>).ram_pct || "0")}%` },
            { label: t(language, "system.stat.gpu"), value: `${String((data as Record<string, unknown>).gpu_mem_pct || "0")}%` },
            { label: t(language, "system.stat.localStatus"), value: t(language, "system.stat.ready") },
          ]} />
        ) : <StructuredView value={data} />}
      </DataPanel>
      <DataPanel title={mode === "basic" ? t(language, "system.storage.title") : t(language, "system.panel.brainStorage")} result={storage.data}>
        {(data) => <StorageView data={data as Record<string, unknown>} mode={mode} language={language} />}
      </DataPanel>
      {mode === "basic" ? null : (
        <DataPanel title={t(language, "system.backup.health")} result={backupHealth.data}>
          {(data) => <BackupHealthView data={data as Record<string, unknown>} />}
        </DataPanel>
      )}
      {mode === "basic" ? null : (
      <Card>
        <CardHeader>
          <CardTitle>{t(language, "system.archive.title")}</CardTitle>
          <CardDescription>{t(language, "system.archive.detail")}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-col sm:flex-row gap-2">
            <Input value={archivePath} onChange={(e) => setArchivePath(e.target.value)} placeholder={t(language, "system.archive.exportPath")} />
            <Input value={restorePath} onChange={(e) => setRestorePath(e.target.value)} placeholder={t(language, "system.archive.restorePath")} />
          </div>
          <Input type="password" value={archivePassphrase} onChange={(e) => setArchivePassphrase(e.target.value)} placeholder={t(language, "system.archive.passphrase")} />
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => archiveCreate.mutate()} disabled={!archivePassphrase || archiveCreate.isPending}>{t(language, "system.archive.export")}</Button>
            <Button variant="outline" onClick={() => archiveInspect.mutate()} disabled={!restorePath || archiveInspect.isPending}>{t(language, "system.archive.inspect")}</Button>
            <Button variant="outline" onClick={() => archiveVerify.mutate()} disabled={!restorePath || !archivePassphrase || archiveVerify.isPending}>{t(language, "system.archive.verify")}</Button>
            <Button variant="outline" onClick={() => archiveDryRun.mutate()} disabled={!restorePath || !archivePassphrase || archiveDryRun.isPending}>{t(language, "system.archive.restoreDryRun")}</Button>
            <Button variant="outline" onClick={() => archiveImportDryRun.mutate()} disabled={!restorePath || !archivePassphrase || archiveImportDryRun.isPending}>{t(language, "system.archive.importDryRun")}</Button>
            <label className="flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm">
              <input type="checkbox" checked={restoreConfirm} onChange={(e) => setRestoreConfirm(e.target.checked)} />
              {t(language, "system.archive.confirmRestore")}
            </label>
            <Button variant="destructive" onClick={() => archiveRestore.mutate()} disabled={!restorePath || !archivePassphrase || !restoreConfirm || archiveRestore.isPending}>{t(language, "system.archive.restore")}</Button>
            <label className="flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm">
              <input type="checkbox" checked={importConfirm} onChange={(e) => setImportConfirm(e.target.checked)} />
              {t(language, "system.archive.confirmImport")}
            </label>
            <Button variant="outline" onClick={() => archiveImport.mutate()} disabled={!restorePath || !archivePassphrase || !importConfirm || archiveImport.isPending}>{t(language, "system.archive.import")}</Button>
          </div>
          {[archiveCreate.data, archiveInspect.data, archiveVerify.data, archiveDryRun.data, archiveRestore.data, archiveImportDryRun.data, archiveImport.data].filter(Boolean).map((item, i) => (
            <OperationResult key={i} result={item} successLabel={t(language, "system.archive.requestDone")} />
          ))}
        </CardContent>
      </Card>
      )}
      {mode !== "basic" ? <Card>
        <CardHeader>
          <CardTitle>{t(language, "system.scale.title")}</CardTitle>
          <CardDescription>{t(language, "system.scale.detail")}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-col sm:flex-row gap-2">
            <Input value={dsn} onChange={(e) => setDsn(e.target.value)} placeholder={t(language, "system.scale.dsn")} />
            <Input value={schema} onChange={(e) => setSchema(e.target.value)} placeholder={t(language, "system.scale.schema")} />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => docker.mutate(false)} disabled={docker.isPending}>{t(language, "system.scale.dockerPlan")}</Button>
            <label className="flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm">
              <input type="checkbox" checked={dockerConsent} onChange={(e) => setDockerConsent(e.target.checked)} />
              {t(language, "system.scale.dockerConsent")}
            </label>
            <Button onClick={() => docker.mutate(true)} disabled={!dockerConsent || docker.isPending}>{t(language, "system.scale.dockerStart")}</Button>
            <Button variant="outline" onClick={() => migration.mutate()} disabled={!dsn || migration.isPending}>{t(language, "system.scale.migrationPlan")}</Button>
          </div>
          {docker.data ? <OperationResult result={docker.data} successLabel={t(language, "system.scale.dockerDone")} /> : null}
          {migration.data ? <OperationResult result={migration.data} successLabel={t(language, "system.scale.migrationDone")} /> : null}
        </CardContent>
      </Card> : null}
      {mode === "basic" ? null : (
        <DataPanel title={t(language, "system.computerMemory.title")} result={comp.data}>
          {(data) => (
            <div className="space-y-3">
              <StructuredView value={data} />
              <div className="flex gap-2">
                <ActionButton label={t(language, "system.computerMemory.enable")} action={() => latticeApi.setComputerMemory(true)} invalidate={["computerMemory"]} />
                <ActionButton label={t(language, "system.computerMemory.disable")} action={() => latticeApi.setComputerMemory(false)} invalidate={["computerMemory"]} variant="destructive" />
              </div>
            </div>
          )}
        </DataPanel>
      )}
    </div>
  );
}

function localizedTextValue(language: Language, value: unknown, fallback = t(language, "system.value.notReported")) {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "boolean") return t(language, value ? "system.value.enabled" : "system.value.disabled");
  return String(value);
}

function PresenceView({ data }: { data: Record<string, unknown> }) {
  const language = useAppStore((state) => state.language);
  const rows = asArray<Record<string, unknown>>(data.presence || data.clients || data);
  if (!rows.length) return <EmptyState title={t(language, "system.presence.empty")} detail={t(language, "system.presence.emptyDetail")} />;
  return <EntityList items={rows} titleKey="user" metaKey="workspace_id" />;
}

function DeviceIdentityView({ data }: { data: Record<string, unknown> }) {
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

function HealthView({ data }: { data: Record<string, unknown> }) {
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

function StorageView({ data, mode = "advanced", language = "en" }: { data: Record<string, unknown>; mode?: string; language?: Language }) {
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

function BackupHealthView({ data }: { data: Record<string, unknown> }) {
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

function StatusCard({ title, status, detail }: { title: string; status: string; detail: string }) {
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

function HardeningView({ data }: { data: Record<string, unknown> }) {
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

function SecurityView({ data }: { data: Record<string, unknown> }) {
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

function AdminPanel() {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const summary = useQuery({ queryKey: ["adminSummary"], queryFn: latticeApi.adminSummary });
  const users = useQuery({ queryKey: ["adminUsers"], queryFn: latticeApi.adminUsers });
  const audit = useQuery({ queryKey: ["adminAudit"], queryFn: () => latticeApi.adminAudit() });
  const roles = useQuery({ queryKey: ["adminRoles"], queryFn: latticeApi.adminRoles });
  const policies = useQuery({ queryKey: ["adminPolicies"], queryFn: latticeApi.adminPolicies });
  const hardening = useQuery({ queryKey: ["adminProductHardening"], queryFn: latticeApi.adminProductHardening });
  const security = useQuery({ queryKey: ["adminSecurity"], queryFn: latticeApi.adminSecurity });
  const vpc = useQuery({ queryKey: ["vpcStatus"], queryFn: latticeApi.vpcStatus });
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
