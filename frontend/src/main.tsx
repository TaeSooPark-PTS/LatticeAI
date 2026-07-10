import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./styles.css";
import { clearScopedClientState, queryClient } from "@/queryClient";
import { useAppStore } from "@/store/appStore";

useAppStore.subscribe((state, previousState) => {
  if (state.workspaceId !== previousState.workspaceId) {
    clearScopedClientState();
  }
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
);
