import * as React from "react";

import { t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";

export type ErrorSurface = "route" | "panel";

type ErrorBoundaryProps = {
  children: React.ReactNode;
  surface?: ErrorSurface;
  resetKey?: string | number;
  showDetails?: boolean;
};

type ErrorBoundaryState = {
  error: Error | null;
};

export function shouldShowErrorDetails(explicit?: boolean, env?: { DEV?: boolean }): boolean {
  if (explicit !== undefined) return explicit;
  if (env) return Boolean(env.DEV);
  return Boolean((import.meta as { env?: { DEV?: boolean } }).env?.DEV);
}

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    const surface = this.props.surface ?? "route";
    console.error(`ErrorBoundary(${surface})`, error, info.componentStack);
  }

  componentDidUpdate(prevProps: ErrorBoundaryProps) {
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.reset();
    }
  }

  reset = () => {
    this.setState({ error: null });
  };

  render() {
    if (this.state.error) {
      return (
        <ErrorFallback
          error={this.state.error}
          surface={this.props.surface ?? "route"}
          showDetails={shouldShowErrorDetails(this.props.showDetails)}
          onRetry={this.reset}
        />
      );
    }
    return this.props.children;
  }
}

export function ErrorFallback({
  error,
  surface,
  showDetails,
  onRetry,
  language,
}: {
  error: Error;
  surface: ErrorSurface;
  showDetails: boolean;
  onRetry: () => void;
  language?: Language;
}) {
  const storeLanguage = useAppStore((state) => state.language);
  const lang = language ?? storeLanguage;
  const title = t(lang, "feedback.error.title");
  const detailText = typeof error.message === "string" ? error.message : String(error);
  const testId = surface === "panel" ? "error-boundary-panel" : "error-boundary-route";

  return (
    <section
      className={surface === "panel" ? "error-boundary is-panel" : "error-boundary"}
      data-testid={testId}
      role="alert"
      aria-label={t(lang, "feedback.error.aria")}
    >
      <strong className="error-boundary-title">{title}</strong>
      <p className="error-boundary-body">{t(lang, "feedback.error.body")}</p>
      <button
        type="button"
        className="error-boundary-retry"
        data-testid="error-boundary-retry"
        onClick={onRetry}
      >
        {t(lang, "feedback.retry")}
      </button>
      {showDetails ? (
        <details className="error-boundary-details" data-testid="error-boundary-details">
          <summary>{t(lang, "feedback.error.details")}</summary>
          <pre>{detailText}</pre>
        </details>
      ) : null}
    </section>
  );
}

export function PanelLoader({ language }: { language: Language }) {
  return (
    <div className="panel-loader" role="status" data-testid="panel-loader">
      {t(language, "shell.loading.panel")}
    </div>
  );
}

export function LazyPanel({
  children,
  language,
  resetKey,
  fallback,
}: {
  children: React.ReactNode;
  language: Language;
  resetKey?: string | number;
  fallback?: React.ReactNode;
}) {
  return (
    <ErrorBoundary surface="panel" resetKey={resetKey}>
      <React.Suspense fallback={fallback ?? <PanelLoader language={language} />}>
        {children}
      </React.Suspense>
    </ErrorBoundary>
  );
}

export function LazyRoute({
  children,
  language: _language,
  resetKey,
  fallback,
}: {
  children: React.ReactNode;
  language: Language;
  resetKey?: string | number;
  fallback?: React.ReactNode;
}) {
  return (
    <ErrorBoundary surface="route" resetKey={resetKey}>
      <React.Suspense fallback={fallback}>
        {children}
      </React.Suspense>
    </ErrorBoundary>
  );
}
