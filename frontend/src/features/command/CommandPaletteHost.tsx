import * as React from "react";
import type { Language } from "@/i18n";

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

  return (
    <React.Suspense fallback={null}>
      <CommandPalette language={language} initialOpen />
    </React.Suspense>
  );
}
