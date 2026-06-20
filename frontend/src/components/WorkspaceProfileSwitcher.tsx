import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, UserCircle, Building2, ArrowRightLeft } from "lucide-react";
import { latticeApi } from "@/api/client";
import { t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { asArray } from "@/lib/utils";

type WorkspaceRow = Record<string, unknown>;

function workspaceId(row: WorkspaceRow): string {
  return String(row.workspace_id || row.id || "");
}

function workspaceName(language: Language, row: WorkspaceRow): string {
  const name = row.name;
  if (typeof name === "string" && name.trim()) return name;
  const id = workspaceId(row);
  return id || t(language, "shell.workspace.personal");
}

function ownerEmail(profile: unknown): string | null {
  if (!profile || typeof profile !== "object") return null;
  const record = profile as Record<string, unknown>;
  const candidate = record.email || record.owner_email || record.username || record.name;
  return typeof candidate === "string" && candidate.trim() ? candidate : null;
}

/**
 * Discoverable workspace + profile switcher for the Brain shell.
 * Surfaces the active workspace and signed-in owner, and lets the user
 * switch workspace or jump to account/workspace settings without hunting
 * through the Settings tabs.
 */
export function WorkspaceProfileSwitcher({ language }: { language: Language }) {
  const qc = useQueryClient();
  const workspaceIdState = useAppStore((state) => state.workspaceId);
  const setWorkspaceId = useAppStore((state) => state.setWorkspaceId);
  const [open, setOpen] = React.useState(false);
  const containerRef = React.useRef<HTMLDivElement | null>(null);

  const profile = useQuery({ queryKey: ["profile"], queryFn: latticeApi.profile });
  const registry = useQuery({ queryKey: ["workspaceRegistry"], queryFn: latticeApi.workspaceRegistry });

  const workspaces = asArray<WorkspaceRow>(
    (registry.data?.data as Record<string, unknown> | undefined)?.workspaces,
  );
  const owner = ownerEmail(profile.data?.data);

  const activeWorkspace = workspaces.find((row) => workspaceId(row) === workspaceIdState);
  const activeLabel = activeWorkspace
    ? workspaceName(language, activeWorkspace)
    : workspaceIdState || t(language, "shell.workspace.personal");

  React.useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onPointer);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onPointer);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const handleSwitch = (id: string) => {
    if (id && id !== workspaceIdState) {
      setWorkspaceId(id);
      // Workspace scoping changes server responses; drop cached views.
      qc.invalidateQueries();
    }
    setOpen(false);
  };

  return (
    <div className="workspace-profile-switcher" ref={containerRef}>
      <button
        type="button"
        className="workspace-profile-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={t(language, "shell.workspace.label")}
        onClick={() => setOpen((value) => !value)}
      >
        <Building2 className="h-3.5 w-3.5" aria-hidden="true" />
        <span className="workspace-profile-trigger-text">
          <span className="workspace-profile-trigger-name">{activeLabel}</span>
          <span className="workspace-profile-trigger-owner">{owner || t(language, "shell.profile.signedOut")}</span>
        </span>
        <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
      </button>

      {open ? (
        <div className="workspace-profile-popover" role="dialog" aria-label={t(language, "shell.workspace.label")}>
          <section className="workspace-profile-section" aria-label={t(language, "shell.profile.label")}>
            <div className="workspace-profile-section-head">
              <UserCircle className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{t(language, "shell.profile.label")}</span>
            </div>
            <div className="workspace-profile-owner-row">
              <span className="workspace-profile-owner-label">{t(language, "shell.profile.owner")}</span>
              <span className="workspace-profile-owner-value">{owner || t(language, "shell.profile.signedOut")}</span>
            </div>
            <button
              type="button"
              className="workspace-profile-link"
              onClick={() => {
                setOpen(false);
                window.location.hash = "/account";
              }}
            >
              {t(language, "shell.profile.manage")}
            </button>
          </section>

          <section className="workspace-profile-section" aria-label={t(language, "shell.workspace.current")}>
            <div className="workspace-profile-section-head">
              <Building2 className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{t(language, "shell.workspace.current")}</span>
            </div>
            {workspaces.length === 0 ? (
              <p className="workspace-profile-empty">{t(language, "shell.workspace.empty")}</p>
            ) : (
              <ul className="workspace-profile-list">
                {workspaces.map((row) => {
                  const id = workspaceId(row);
                  const isActive = id === workspaceIdState;
                  return (
                    <li key={id}>
                      <button
                        type="button"
                        className={`workspace-profile-item ${isActive ? "is-active" : ""}`}
                        aria-current={isActive ? "true" : undefined}
                        onClick={() => handleSwitch(id)}
                      >
                        <span className="workspace-profile-item-name">{workspaceName(language, row)}</span>
                        {isActive ? (
                          <span className="workspace-profile-item-flag">
                            <Check className="h-3 w-3" aria-hidden="true" />
                            {t(language, "shell.workspace.active")}
                          </span>
                        ) : (
                          <span className="workspace-profile-item-switch">
                            <ArrowRightLeft className="h-3 w-3" aria-hidden="true" />
                            {t(language, "shell.workspace.switch")}
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
            <button
              type="button"
              className="workspace-profile-link"
              onClick={() => {
                setOpen(false);
                window.location.hash = "/workspace-admin";
              }}
            >
              {t(language, "shell.workspace.manageSpaces")}
            </button>
          </section>
        </div>
      ) : null}
    </div>
  );
}
