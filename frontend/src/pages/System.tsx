import * as React from "react";
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
import { asArray, isRecord, shortId, titleize } from "@/lib/utils";
import { clearScopedClientState } from "@/queryClient";
import { navigateHash } from "@/features/brain/navigation";

type SystemTab = "account" | "workspaces" | "snapshots" | "activity" | "network" | "settings" | "admin";

const tabs: Array<{ id: SystemTab; labelKey: string }> = [
  { id: "account", labelKey: "system.tab.account" },
  { id: "workspaces", labelKey: "system.tab.workspaces" },
  { id: "snapshots", labelKey: "system.tab.snapshots" },
  { id: "activity", labelKey: "system.tab.activity" },
  { id: "network", labelKey: "system.tab.network" },
  { id: "settings", labelKey: "system.tab.settings" },
  { id: "admin", labelKey: "system.tab.admin" },
];

export function SystemPage({ initialTab }: { initialTab?: string }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const [tab, setTab] = React.useState<SystemTab>((initialTab as SystemTab) || "account");
  React.useEffect(() => {
    if (tabs.some((item) => item.id === initialTab)) setTab(initialTab as SystemTab);
  }, [initialTab]);
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
    <div className="product-page settings-page space-y-5">
      <header className="page-hero">
        <div className="page-kicker"><ShieldCheck className="h-4 w-4" /> {t(language, "system.kicker")}</div>
        <h1 className="page-title">{t(language, "system.title")}</h1>
        <p className="page-copy">{t(language, "system.body")}</p>
      </header>
      <Tabs
        tabs={(mode === "basic"
          ? tabs.filter((item) => item.id === "account" || item.id === "workspaces" || item.id === "snapshots" || item.id === "settings")
          : tabs
        ).map((item) => ({ id: item.id, label: t(language, item.labelKey) }))}
        value={tab}
        onChange={(id) => selectTab(id as SystemTab)}
      />
      {tab === "account" ? <AccountPanel /> : null}
      {tab === "workspaces" ? <WorkspacePanel /> : null}
      {tab === "snapshots" ? <SnapshotsPanel /> : null}
      {tab === "activity" ? <ActivityPanel /> : null}
      {tab === "network" ? <NetworkPanel /> : null}
      {tab === "settings" ? <SettingsPanel /> : null}
      {tab === "admin" ? <AdminPanel /> : null}
    </div>
  );
}

function AccountPanel() {
  const qc = useQueryClient();
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
    <div className="grid gap-4 xl:grid-cols-2">
      <DataPanel title="Profile" result={profile.data}>
        {(data) => <KeyValueList data={data as Record<string, unknown>} />}
      </DataPanel>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><UserCircle className="h-4 w-4" /> Account</CardTitle>
          <CardDescription>Sign in, create a local account, and keep your profile current.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="email" />
          <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="current password" />
          <div className="grid gap-2 sm:grid-cols-2">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="name" />
            <Input value={nickname} onChange={(e) => setNickname(e.target.value)} placeholder="nickname" />
          </div>
          <Input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="new password" />
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => login.mutate()} disabled={!email || !password || login.isPending}>Login</Button>
            <Button variant="outline" onClick={() => register.mutate()} disabled={!email || !password || register.isPending}>Register</Button>
            <Button variant="outline" onClick={() => saveProfile.mutate()} disabled={saveProfile.isPending}>Save profile</Button>
            <Button variant="outline" onClick={() => changePassword.mutate()} disabled={!password || !newPassword || changePassword.isPending}>Change password</Button>
            <ActionButton label="Logout" action={() => latticeApi.logout()} onSuccess={resetIdentityScope} />
          </div>
          {[login.data, register.data, saveProfile.data, changePassword.data].filter(Boolean).map((item, i) => (
            <OperationResult key={i} result={item} successLabel="Account request completed" />
          ))}
        </CardContent>
      </Card>
      <DataPanel title="Sign-in options" result={sso.data} className="xl:col-span-2">
        {(data) => <StructuredView value={data} />}
      </DataPanel>
    </div>
  );
}

function WorkspacePanel() {
  const qc = useQueryClient();
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
    <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
      <DataPanel title="Your workspaces" result={registry.data}>
        {() => (
          <div className="grid gap-2">
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
                      <Button variant="outline" onClick={() => setWorkspaceId(id)}>Use</Button>
                      <ActionButton label="Activate" action={() => latticeApi.activateWorkspace(id)} invalidate={["workspaceRegistry"]} />
                      <ActionButton label="Archive" action={() => latticeApi.archiveWorkspace(id)} invalidate={["workspaceRegistry"]} variant="destructive" />
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
          <CardTitle className="flex items-center gap-2"><Users className="h-4 w-4" /> Organizations and invitations</CardTitle>
          <CardDescription>Create or join a workspace before adding knowledge.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          <Input value={orgName} onChange={(e) => setOrgName(e.target.value)} placeholder="New organization name" />
          <Button disabled={!orgName.trim() || createOrg.isPending} onClick={() => createOrg.mutate()}>Create organization</Button>
          <Input value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} placeholder="invitee email" />
          <Button variant="outline" disabled={createInvite.isPending} onClick={() => createInvite.mutate()}>Create invitation</Button>
          <Input value={inviteToken} onChange={(e) => setInviteToken(e.target.value)} placeholder="invitation token" />
          <Button variant="outline" disabled={!inviteToken.trim() || accept.isPending} onClick={() => accept.mutate()}>Accept invitation</Button>
          <DataPanel title="Invitations" result={invites.data}>
            {(data) => <EntityList items={(data as Record<string, unknown>).invitations} titleKey="token" metaKey="role" />}
          </DataPanel>
        </CardContent>
      </Card>
    </div>
  );
}

function SnapshotsPanel() {
  const snaps = useQuery({ queryKey: ["snapshots"], queryFn: latticeApi.snapshots });
  const timeline = useQuery({ queryKey: ["timeMachine"], queryFn: latticeApi.timeMachine });
  const [name, setName] = React.useState("");
  const [before, setBefore] = React.useState("");
  const [after, setAfter] = React.useState("");
  const create = useMutation({ mutationFn: () => latticeApi.createSnapshot(name || "desktop checkpoint") });
  const compare = useMutation({ mutationFn: () => latticeApi.compareSnapshots(before, after) });
  const rows = asArray<Record<string, unknown>>((snaps.data?.data as Record<string, unknown>)?.snapshots);
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <DataPanel title="Snapshots" result={snaps.data}>
        {() => (
          <div className="space-y-2">
            {rows.map((snap) => {
              const id = String(snap.id || snap.snapshot_id);
              return (
                <div key={id} className="rounded-md border border-border p-3">
                  <div className="font-medium">{String(snap.name || id)}</div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <ActionButton label="Export" action={() => latticeApi.exportSnapshot(id)} />
                    <ActionButton label="Merge restore" action={() => latticeApi.restoreSnapshot(id)} variant="outline" />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </DataPanel>
      <Card>
        <CardHeader>
          <CardTitle>Snapshot actions</CardTitle>
          <CardDescription>Create checkpoints and compare changes over time.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="snapshot name" />
          <Button onClick={() => create.mutate()} disabled={create.isPending}>Create snapshot</Button>
          <div className="grid gap-2 sm:grid-cols-2">
            <Input value={before} onChange={(e) => setBefore(e.target.value)} placeholder="before id" />
            <Input value={after} onChange={(e) => setAfter(e.target.value)} placeholder="after id" />
          </div>
          <Button variant="outline" onClick={() => compare.mutate()} disabled={!before || !after || compare.isPending}>Compare</Button>
          {compare.data ? <OperationResult result={compare.data} successLabel="Snapshot comparison completed" /> : null}
        </CardContent>
      </Card>
      <DataPanel title="Time machine" result={timeline.data} className="xl:col-span-2">
        {(data) => <EntityList items={(data as Record<string, unknown>).events || data} titleKey="event" metaKey="type" limit={14} />}
      </DataPanel>
    </div>
  );
}

function ActivityPanel() {
  const feed = useQuery({ queryKey: ["realtimeFeed"], queryFn: latticeApi.realtimeFeed });
  const presence = useQuery({ queryKey: ["presence"], queryFn: latticeApi.presence });
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <DataPanel title="Realtime feed" result={feed.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).events} titleKey="event_type" metaKey="area" limit={14} />}
      </DataPanel>
      <DataPanel title="Presence" result={presence.data}>
        {(data) => <PresenceView data={data as Record<string, unknown>} />}
      </DataPanel>
    </div>
  );
}

function NetworkPanel() {
  const qc = useQueryClient();
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
    <div className="grid gap-4 xl:grid-cols-[0.8fr_1.2fr]">
      <DataPanel title="Device identity" result={identity.data}>
        {(data) => <DeviceIdentityView data={data as Record<string, unknown>} />}
      </DataPanel>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Network className="h-4 w-4" /> Pair device</CardTitle>
        <CardDescription>Pair a trusted device for workspace exchange.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="device name" />
          <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="trusted device address" />
          <Input value={publicKey} onChange={(e) => setPublicKey(e.target.value)} placeholder="trusted public key" />
          <Button disabled={!name || !baseUrl || !publicKey || pair.isPending} onClick={() => pair.mutate()}>Pair device</Button>
          {pair.data ? <OperationResult result={pair.data} successLabel="Peer pairing request completed" /> : null}
        </CardContent>
      </Card>
      <DataPanel title="Peers" result={peers.data} className="xl:col-span-2">
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
                    <ActionButton label="Push workspace" action={() => latticeApi.pushPeer(id, useAppStore.getState().workspaceId)} />
                    <ActionButton label="Unpair" action={() => latticeApi.unpairPeer(id)} invalidate={["networkPeers"]} variant="destructive" />
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
    <div className="grid gap-4 xl:grid-cols-3">
      <Card>
        <CardHeader>
          <CardTitle>{t(language, "system.panel.appearance")}</CardTitle>
          <CardDescription>{t(language, "system.panel.appearance.hint")}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Button variant={theme === "dark" ? "default" : "outline"} onClick={() => setTheme("dark")}>{t(language, "system.theme.dark")}</Button>
          <Button variant={theme === "light" ? "default" : "outline"} onClick={() => setTheme("light")}>{t(language, "system.theme.light")}</Button>
          {(["basic", "advanced", "admin"] as const).map((item) => (
            <Button key={item} variant={mode === item ? "default" : "outline"} onClick={() => setMode(item)}>{t(language, `shell.mode.${item}`)}</Button>
          ))}
        </CardContent>
      </Card>
      <DataPanel title={mode === "basic" ? t(language, "system.panel.brainStatus") : "Server health"} result={health.data}>
        {(data) => <HealthView data={data as Record<string, unknown>} />}
      </DataPanel>
      <DataPanel title={mode === "basic" ? t(language, "system.panel.readiness") : "Host telemetry"} result={sys.data}>
        {(data) => mode === "basic" ? (
          <StatGrid stats={[
            { label: t(language, "system.stat.cpu"), value: `${String((data as Record<string, unknown>).cpu_pct || "0")}%` },
            { label: t(language, "system.stat.memory"), value: `${String((data as Record<string, unknown>).ram_pct || "0")}%` },
            { label: t(language, "system.stat.gpu"), value: `${String((data as Record<string, unknown>).gpu_mem_pct || "0")}%` },
            { label: t(language, "system.stat.localStatus"), value: t(language, "system.stat.ready") },
          ]} />
        ) : <StructuredView value={data} />}
      </DataPanel>
      <DataPanel title={mode === "basic" ? t(language, "system.storage.title") : "Brain storage"} result={storage.data} className="xl:col-span-3">
        {(data) => <StorageView data={data as Record<string, unknown>} mode={mode} language={language} />}
      </DataPanel>
      {mode === "basic" ? null : (
        <DataPanel title="Backup health" result={backupHealth.data} className="xl:col-span-3">
          {(data) => <BackupHealthView data={data as Record<string, unknown>} />}
        </DataPanel>
      )}
      {mode === "basic" ? null : (
      <Card className="xl:col-span-3">
        <CardHeader>
          <CardTitle>.latticebrain portability</CardTitle>
          <CardDescription>Create an encrypted portable brain file, verify one, or preview a restore before applying it.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          <div className="grid gap-2 sm:grid-cols-[1fr_1fr]">
            <Input value={archivePath} onChange={(e) => setArchivePath(e.target.value)} placeholder="export path (optional)" />
            <Input value={restorePath} onChange={(e) => setRestorePath(e.target.value)} placeholder="archive path for inspect/restore" />
          </div>
          <Input type="password" value={archivePassphrase} onChange={(e) => setArchivePassphrase(e.target.value)} placeholder="archive passphrase" />
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => archiveCreate.mutate()} disabled={!archivePassphrase || archiveCreate.isPending}>Export archive</Button>
            <Button variant="outline" onClick={() => archiveInspect.mutate()} disabled={!restorePath || archiveInspect.isPending}>Inspect</Button>
            <Button variant="outline" onClick={() => archiveVerify.mutate()} disabled={!restorePath || !archivePassphrase || archiveVerify.isPending}>Verify</Button>
            <Button variant="outline" onClick={() => archiveDryRun.mutate()} disabled={!restorePath || !archivePassphrase || archiveDryRun.isPending}>Restore dry run</Button>
            <Button variant="outline" onClick={() => archiveImportDryRun.mutate()} disabled={!restorePath || !archivePassphrase || archiveImportDryRun.isPending}>Import dry run</Button>
            <label className="flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm">
              <input type="checkbox" checked={restoreConfirm} onChange={(e) => setRestoreConfirm(e.target.checked)} />
              Confirm restore
            </label>
            <Button variant="destructive" onClick={() => archiveRestore.mutate()} disabled={!restorePath || !archivePassphrase || !restoreConfirm || archiveRestore.isPending}>Restore</Button>
            <label className="flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm">
              <input type="checkbox" checked={importConfirm} onChange={(e) => setImportConfirm(e.target.checked)} />
              Confirm import
            </label>
            <Button variant="outline" onClick={() => archiveImport.mutate()} disabled={!restorePath || !archivePassphrase || !importConfirm || archiveImport.isPending}>Import</Button>
          </div>
          {[archiveCreate.data, archiveInspect.data, archiveVerify.data, archiveDryRun.data, archiveRestore.data, archiveImportDryRun.data, archiveImport.data].filter(Boolean).map((item, i) => (
            <OperationResult key={i} result={item} successLabel="Archive request completed" />
          ))}
        </CardContent>
      </Card>
      )}
      {mode !== "basic" ? <Card className="xl:col-span-3">
        <CardHeader>
          <CardTitle>Scale mode</CardTitle>
          <CardDescription>Optional advanced storage. Local SQLite remains the default.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          <div className="grid gap-2 sm:grid-cols-[1fr_220px]">
            <Input value={dsn} onChange={(e) => setDsn(e.target.value)} placeholder="Postgres connection string" />
            <Input value={schema} onChange={(e) => setSchema(e.target.value)} placeholder="database schema" />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => docker.mutate(false)} disabled={docker.isPending}>Docker plan</Button>
            <label className="flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm">
              <input type="checkbox" checked={dockerConsent} onChange={(e) => setDockerConsent(e.target.checked)} />
              Consent to start Docker
            </label>
            <Button onClick={() => docker.mutate(true)} disabled={!dockerConsent || docker.isPending}>Start Docker</Button>
            <Button variant="outline" onClick={() => migration.mutate()} disabled={!dsn || migration.isPending}>Plan migration</Button>
          </div>
          {docker.data ? <OperationResult result={docker.data} successLabel="Docker setup request completed" /> : null}
          {migration.data ? <OperationResult result={migration.data} successLabel="Migration plan completed" /> : null}
        </CardContent>
      </Card> : null}
      {mode === "basic" ? null : (
        <DataPanel title="Computer memory" result={comp.data} className="xl:col-span-3">
          {(data) => (
            <div className="space-y-3">
              <StructuredView value={data} />
              <div className="flex gap-2">
                <ActionButton label="Enable memory" action={() => latticeApi.setComputerMemory(true)} invalidate={["computerMemory"]} />
                <ActionButton label="Disable memory" action={() => latticeApi.setComputerMemory(false)} invalidate={["computerMemory"]} variant="destructive" />
              </div>
            </div>
          )}
        </DataPanel>
      )}
    </div>
  );
}

function textValue(value: unknown, fallback = "not reported") {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "boolean") return value ? "enabled" : "disabled";
  return String(value);
}

function PresenceView({ data }: { data: Record<string, unknown> }) {
  const rows = asArray<Record<string, unknown>>(data.presence || data.clients || data);
  if (!rows.length) return <EmptyState title="No active presence" detail="No live collaborators or realtime clients are currently reported." />;
  return <EntityList items={rows} titleKey="user" metaKey="workspace_id" />;
}

function DeviceIdentityView({ data }: { data: Record<string, unknown> }) {
  const mode = useAppStore((state) => state.mode);
  const publicKey = textValue(data.public_key, "");
  if (mode === "basic") {
    return (
      <div className="space-y-3">
        <StatusCard title="This Mac" status="trusted" detail="This device can participate in local workspace exchange when you pair another trusted device." />
        <Badge variant="muted">{textValue(data.algorithm, "local identity")}</Badge>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="success">local device</Badge>
        <Badge variant="muted">{textValue(data.algorithm, "identity key")}</Badge>
      </div>
      <KeyValueList data={{
        device_id: data.device_id || data.id || "not reported",
        fingerprint: data.fingerprint || "not reported",
        public_key: publicKey ? shortId(publicKey.replace(/\s+/g, " "), 72) : "not reported",
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
        { label: "Status", value: data.status || data.ok || "reported" },
        { label: "Version", value: data.version || "not reported" },
        { label: "Mode", value: data.mode || data.environment || "local" },
        { label: "Port", value: data.port || data.backend_port || "configured" },
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
        { label: "Active engine", value: active.engine || data.engine || "sqlite" },
        { label: "SQLite default", value: active.engine === "postgres" ? "scale mode" : "enabled" },
        { label: "Vector search", value: vector || "not reported" },
        { label: "Postgres", value: postgresAvailable ? "available" : "optional" },
      ]} />
      <div className="grid gap-3 md:grid-cols-3">
        <StatusCard title="SQLite" status={active.available === false ? "unavailable" : "default"} detail={textValue(active.reason || active.path || data.path, "Local brain storage is active by default.")} />
        <StatusCard title="Vector search" status={textValue(vector, "reported")} detail={textValue(active.vector_reason || active.sqlite_vec_reason || data.vector_reason, "Uses the configured local vector capability or reports why it is unavailable.")} />
        <StatusCard title="Postgres" status={postgresAvailable ? "available" : "not enabled"} detail={textValue(postgres.reason || postgres.dsn || postgres.status, "Postgres scale mode is opt-in and never required for local use.")} />
      </div>
      {Object.keys(backup).length ? <StructuredView value={{ backup_health: backup }} /> : null}
    </div>
  );
}

function BackupHealthView({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="space-y-3">
      <StatGrid stats={[
        { label: "Available", value: data.available === false ? "no" : "yes" },
        { label: "Backups", value: data.count || data.backups || 0 },
        { label: "Encrypted", value: data.encrypted_archives || 0 },
        { label: "Zip backups", value: data.zip_backups || 0 },
      ]} />
      <KeyValueList data={{
        directory: data.directory || "not reported",
        latest: data.latest || "none reported",
        last_verified: data.last_verified || data.verified_at || "not reported",
        failure: data.error || data.reason || "none reported",
      }} />
    </div>
  );
}

function StatusCard({ title, status, detail }: { title: string; status: string; detail: string }) {
  const variant = /unavailable|failed|denied|disabled|not enabled/i.test(status) ? "warning" : "success";
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
  const startup = isRecord(data.startup) ? data.startup : {};
  const privacy = isRecord(data.privacy) ? data.privacy : {};
  const storage = isRecord(data.storage) ? data.storage : {};
  const backup = isRecord(data.backup) ? data.backup : {};
  const identity = isRecord(data.device_identity) ? data.device_identity : {};
  const permissions = isRecord(data.permissions) ? data.permissions : {};
  return (
    <div className="space-y-3">
      <StatGrid stats={[
        { label: "Version", value: data.version || "reported" },
        { label: "Local only", value: privacy.local_only_default ?? startup.local_only_default ?? "reported" },
        { label: "Storage", value: isRecord(storage.active) ? (storage.active as Record<string, unknown>).engine : "reported" },
        { label: "Backups", value: backup.count || backup.available || "reported" },
      ]} />
      <div className="grid gap-3 md:grid-cols-2">
        <StatusCard title="Startup" status={startup.network_exposed ? "network exposed" : "local-only"} detail="Lattice starts locally by default and reports when network access is enabled." />
        <StatusCard title="Integrations" status={privacy.local_only_default === false ? "review required" : "opt-in"} detail="External integrations remain disabled until the user explicitly enables them." />
        <StatusCard title="Device identity" status={textValue(identity.algorithm || identity.fingerprint, "reported")} detail={textValue(identity.storage, "Stored locally and used for signed bundle exchange.")} />
        <StatusCard title="Permissions" status={permissions.destructive_restore_requires_confirmation === false ? "review required" : "guarded"} detail="Export, import, and destructive restore permissions are surfaced through admin status." />
      </div>
    </div>
  );
}

function SecurityView({ data }: { data: Record<string, unknown> }) {
  const cards = isRecord(data.cards) ? data.cards : {};
  const severities = isRecord(data.severity_counts) ? data.severity_counts : {};
  return (
    <div className="space-y-3">
      <StatGrid stats={[
        { label: "Events today", value: cards.events_today || 0 },
        { label: "High risk", value: cards.high_risk_events || severities.high || 0 },
        { label: "Review", value: cards.review_required || 0 },
        { label: "Risk rate", value: data.risk_rate || 0 },
      ]} />
      <StructuredView value={{ severity_counts: severities, sensitive_fields: data.field_counts || {} }} />
    </div>
  );
}

function AdminPanel() {
  const mode = useAppStore((state) => state.mode);
  const summary = useQuery({ queryKey: ["adminSummary"], queryFn: latticeApi.adminSummary });
  const users = useQuery({ queryKey: ["adminUsers"], queryFn: latticeApi.adminUsers });
  const audit = useQuery({ queryKey: ["adminAudit"], queryFn: () => latticeApi.adminAudit() });
  const roles = useQuery({ queryKey: ["adminRoles"], queryFn: latticeApi.adminRoles });
  const policies = useQuery({ queryKey: ["adminPolicies"], queryFn: latticeApi.adminPolicies });
  const hardening = useQuery({ queryKey: ["adminProductHardening"], queryFn: latticeApi.adminProductHardening });
  const security = useQuery({ queryKey: ["adminSecurity"], queryFn: latticeApi.adminSecurity });
  const vpc = useQuery({ queryKey: ["vpcStatus"], queryFn: latticeApi.vpcStatus });
  if (mode !== "admin") {
    return <ModeGate title="Admin controls" detail="Switch to Admin mode to review users, audit events, policies, security posture, and private networking diagnostics." target="admin" />;
  }
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <DataPanel title="Admin summary" result={summary.data}>{(data) => <KeyValueList data={data as Record<string, unknown>} />}</DataPanel>
      <DataPanel title="Users" result={users.data}>{(data) => <EntityList items={data} titleKey="email" metaKey="role" />}</DataPanel>
      <DataPanel title="Audit" result={audit.data}>{(data) => <EntityList items={(data as Record<string, unknown>).recent_events || data} titleKey="act" metaKey="sev" />}</DataPanel>
      <DataPanel title="Roles" result={roles.data}>{(data) => <EntityList items={(data as Record<string, unknown>).roles || data} titleKey="role" metaKey="members" />}</DataPanel>
      <DataPanel title="Policies" result={policies.data}>{(data) => <EntityList items={(data as Record<string, unknown>).policies || data} titleKey="label" metaKey="enforced" />}</DataPanel>
      <DataPanel title="Product hardening" result={hardening.data}>{(data) => <HardeningView data={data as Record<string, unknown>} />}</DataPanel>
      <DataPanel title="Security overview" result={security.data}>{(data) => <SecurityView data={data as Record<string, unknown>} />}</DataPanel>
      <DataPanel title="Private VPC" result={vpc.data} className="xl:col-span-2">
        {(data) => (
          <div className="space-y-2">
            <Badge variant="muted">Community-disabled features remain honest unavailable states.</Badge>
            <StructuredView value={data} />
          </div>
        )}
      </DataPanel>
    </div>
  );
}
