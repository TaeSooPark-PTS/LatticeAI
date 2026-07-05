import * as React from "react";
import { ShieldCheck, Lock } from "lucide-react";
import { t, type Language } from "@/i18n";
import { useAppStore, type WorkspaceMode } from "@/store/appStore";

const MODES: WorkspaceMode[] = ["basic", "advanced", "admin"];

/**
 * Role-aware admin entry point for the Brain shell.
 *
 * Mode is the access proxy used across the app (SystemPage gates the admin
 * tab and AdminPanel on `mode`). When the user is in advanced/admin mode we
 * surface a discoverable Admin Console button plus a compact mode switcher.
 * In basic mode the console is hidden behind an explained affordance that
 * promotes the user to admin mode rather than failing silently.
 */
export function AdminAccessGate({ language }: { language: Language }) {
  const mode = useAppStore((state) => state.mode);
  const setMode = useAppStore((state) => state.setMode);
  const canSeeAdmin = mode === "advanced" || mode === "admin";

  // In everyday (basic) mode we keep the top bar calm and jargon-free:
  // the admin console and the basic/advanced/admin switch stay tucked away
  // in Settings → Appearance, so a first-time user is never asked to reason
  // about "modes" just to have a conversation with their Brain.
  if (mode === "basic") return null;

  return (
    <div className="admin-access-gate">
      {canSeeAdmin ? (
        <button
          type="button"
          className="admin-access-button"
          title={t(language, "shell.admin.tooltip")}
          aria-label={t(language, "shell.admin.open")}
          onClick={() => {
            window.location.hash = "/admin";
          }}
        >
          <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
          <span>{t(language, "shell.admin.label")}</span>
        </button>
      ) : (
        <button
          type="button"
          className="admin-access-button is-locked"
          title={t(language, "shell.admin.needsMode")}
          aria-label={t(language, "shell.admin.needsMode")}
          onClick={() => setMode("admin")}
        >
          <Lock className="h-3.5 w-3.5" aria-hidden="true" />
          <span>{t(language, "shell.admin.enable")}</span>
        </button>
      )}

      <div
        className="mode-switcher-mini"
        role="group"
        aria-label={t(language, "shell.mode.label")}
        title={t(language, "shell.mode.info")}
      >
        {MODES.map((item) => (
          <button
            key={item}
            type="button"
            className={mode === item ? "is-active" : ""}
            aria-pressed={mode === item}
            onClick={() => setMode(item)}
          >
            {t(language, `shell.mode.${item}`)}
          </button>
        ))}
      </div>
    </div>
  );
}
