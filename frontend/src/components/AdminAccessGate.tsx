import { ShieldCheck, Lock } from "lucide-react";
import { t, type Language } from "@/i18n";
import { useAppStore, type WorkspaceMode } from "@/store/appStore";

const MODES: WorkspaceMode[] = ["basic", "advanced", "admin"];

export function AdminAccessGate({ language }: { language: Language }) {
  const mode = useAppStore((state) => state.mode);
  const setMode = useAppStore((state) => state.setMode);
  const canSeeAdmin = mode === "advanced" || mode === "admin";

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
