import { UserCircle, Users } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EntityList, KeyValueList, OperationResult } from "@/components/primitives";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { SignInOptionsView } from "./SystemViews";
import { useAccountPanel, useWorkspacePanel } from "./hooks/useSystemPanels";

export function AccountPanel() {
  const language = useAppStore((state) => state.language);
  const {
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
  } = useAccountPanel();

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

export function WorkspacePanel() {
  const language = useAppStore((state) => state.language);
  const {
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
  } = useWorkspacePanel();

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
