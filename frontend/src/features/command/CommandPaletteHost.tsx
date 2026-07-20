import * as React from "react";
import type { Language } from "@/i18n";

// The command palette carries its own search UI and query wiring. It is only
// ever needed once the user reaches for Cmd/Ctrl+K (or the custom open event),
// so we keep it out of the initial shell chunk and lazy-load it on first use.
const CommandPalette = React.lazy(() =>
  import("./CommandPalette").then((module) => ({ default: module.CommandPalette })),
);

export function CommandPaletteHost({ language }: { language: Language }) {
  const [activated, setActivated] = React.useState(false);

  React.useEffect(() => {
    if (activated) return;
    const activate = () => setActivated(true);
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        activate();
      }
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("lattice:open-command", activate);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("lattice:open-command", activate);
    };
  }, [activated]);

  if (!activated) return null;

  // `initialOpen` opens the palette on the same gesture that loaded it, so the
  // first Cmd+K does not silently prime the chunk without opening.
  return (
    <React.Suspense fallback={null}>
      <CommandPalette language={language} initialOpen />
    </React.Suspense>
  );
}
