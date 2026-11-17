import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { latticeApi } from "@/api/client";
import { useRealtimeStream } from "@/hooks/useRealtimeStream";
import { t, type Language } from "@/i18n";
import { asArray } from "@/lib/utils";
import { clearScopedClientState } from "@/queryClient";
import { useAppStore } from "@/store/appStore";

export function useAccountPanel() {
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

  return {
    profile,
    sso,
    email,
    setEmail,
    password,
    setPassword,
    name,
    setName,
    nickname,
    setNickname,
    newPassword,
    setNewPassword,
    resetIdentityScope,
    login,
    register,
    saveProfile,
    changePassword,
  };
}

export function useWorkspacePanel() {
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

  return {
    setWorkspaceId,
    registry,
    invites,
    orgName,
    setOrgName,
    inviteEmail,
    setInviteEmail,
    inviteToken,
    setInviteToken,
    createOrg,
    createInvite,
    accept,
    workspaces,
  };
}

export function useSnapshotsPanel(language: Language) {
  const snaps = useQuery({ queryKey: ["snapshots"], queryFn: latticeApi.snapshots });
  const timeline = useQuery({ queryKey: ["timeMachine"], queryFn: latticeApi.timeMachine });
  const [name, setName] = React.useState("");
  const [before, setBefore] = React.useState("");
  const [after, setAfter] = React.useState("");
  const create = useMutation({ mutationFn: () => latticeApi.createSnapshot(name || t(language, "system.snapshots.defaultName")) });
  const compare = useMutation({ mutationFn: () => latticeApi.compareSnapshots(before, after) });
  const rows = asArray<Record<string, unknown>>((snaps.data?.data as Record<string, unknown>)?.snapshots);

  return {
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
  };
}

export function useActivityPanel() {
  const { transport, pollingInterval } = useRealtimeStream(true);
  const feed = useQuery({
    queryKey: ["realtimeFeed"],
    queryFn: latticeApi.realtimeFeed,
    refetchInterval: pollingInterval,
  });
  const presence = useQuery({
    queryKey: ["presence"],
    queryFn: latticeApi.presence,
    refetchInterval: pollingInterval,
  });
  return { feed, presence, transport };
}

export function useNetworkPanel() {
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

  return {
    identity,
    peers,
    name,
    setName,
    baseUrl,
    setBaseUrl,
    publicKey,
    setPublicKey,
    pair,
  };
}

export function useSettingsPanel() {
  const qc = useQueryClient();
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

  return {
    health,
    sys,
    comp,
    storage,
    backupHealth,
    dsn,
    setDsn,
    schema,
    setSchema,
    dockerConsent,
    setDockerConsent,
    archivePath,
    setArchivePath,
    restorePath,
    setRestorePath,
    archivePassphrase,
    setArchivePassphrase,
    restoreConfirm,
    setRestoreConfirm,
    importConfirm,
    setImportConfirm,
    docker,
    migration,
    archiveCreate,
    archiveInspect,
    archiveVerify,
    archiveDryRun,
    archiveRestore,
    archiveImportDryRun,
    archiveImport,
  };
}

export function useAdminPanel() {
  const summary = useQuery({ queryKey: ["adminSummary"], queryFn: latticeApi.adminSummary });
  const users = useQuery({ queryKey: ["adminUsers"], queryFn: latticeApi.adminUsers });
  const audit = useQuery({ queryKey: ["adminAudit"], queryFn: () => latticeApi.adminAudit() });
  const roles = useQuery({ queryKey: ["adminRoles"], queryFn: latticeApi.adminRoles });
  const policies = useQuery({ queryKey: ["adminPolicies"], queryFn: latticeApi.adminPolicies });
  const hardening = useQuery({ queryKey: ["adminProductHardening"], queryFn: latticeApi.adminProductHardening });
  const security = useQuery({ queryKey: ["adminSecurity"], queryFn: latticeApi.adminSecurity });
  const vpc = useQuery({ queryKey: ["vpcStatus"], queryFn: latticeApi.vpcStatus });

  return { summary, users, audit, roles, policies, hardening, security, vpc };
}
