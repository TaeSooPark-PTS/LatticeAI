import { useQuery } from "@tanstack/react-query";

import { latticeApi } from "@/api/client";
import { t, type Language } from "@/i18n";
import { useConversationSession } from "./conversationSession";
import { navigateHash } from "./navigation";

function scrollToBoundaryPanel(attempt = 0) {
  const panel = document.getElementById("network-boundary-panel");
  if (panel) {
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if (attempt < 8) window.setTimeout(() => scrollToBoundaryPanel(attempt + 1), 50);
}

export function openNetworkBoundaryPanel() {
  navigateHash("/settings");
  scrollToBoundaryPanel();
}

export function CloudBoundaryHint({ language }: { language: Language }) {
  const preferLocalOnly = useConversationSession((state) => state.preferLocalOnly);
  const setPreferLocalOnly = useConversationSession((state) => state.setPreferLocalOnly);
  const boundary = useQuery({
    queryKey: ["networkBoundary"],
    queryFn: latticeApi.networkBoundary,
  });
  const allowsCloud = Boolean(boundary.data?.ok && boundary.data.data.allows_cloud);
  if (!allowsCloud) return null;

  return (
    <div className="brain-cloud-hint" data-testid="cloud-boundary-hint">
      <button
        type="button"
        className="brain-cloud-hint-link"
        data-testid="cloud-boundary-allowed"
        aria-label={t(language, "brain.cloud.allowed.aria")}
        onClick={openNetworkBoundaryPanel}
      >
        {t(language, "brain.cloud.allowed")}
      </button>
      <label className="brain-cloud-hint-toggle">
        <input
          type="checkbox"
          data-testid="cloud-local-only-toggle"
          checked={preferLocalOnly}
          onChange={(event) => setPreferLocalOnly(event.target.checked)}
          aria-label={t(language, "brain.cloud.localOnly.aria")}
        />
        {t(language, "brain.cloud.localOnly")}
      </label>
    </div>
  );
}
