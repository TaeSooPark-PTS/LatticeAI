import { fireEvent, render, screen } from "@testing-library/react";
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAppStore } from "@/store/appStore";
import {
  ErrorBoundary,
  ErrorFallback,
  LazyPanel,
  LazyRoute,
  PanelLoader,
  shouldShowErrorDetails,
} from "./ErrorBoundary";

function Boom({ message = "panel exploded" }: { message?: string }): never {
  throw new Error(message);
}

function Healthy({ label = "healthy child" }: { label?: string }) {
  return <div>{label}</div>;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("shouldShowErrorDetails", () => {
  it("honours an explicit override over the env flag", () => {
    expect(shouldShowErrorDetails(true, { DEV: false })).toBe(true);
    expect(shouldShowErrorDetails(false, { DEV: true })).toBe(false);
  });

  it("follows the Vite DEV flag when nothing is passed", () => {
    expect(shouldShowErrorDetails(undefined, { DEV: true })).toBe(true);
    expect(shouldShowErrorDetails(undefined, { DEV: false })).toBe(false);
    expect(shouldShowErrorDetails(undefined, {})).toBe(false);
    expect(shouldShowErrorDetails()).toBe(
      Boolean((import.meta as { env?: { DEV?: boolean } }).env?.DEV),
    );
  });
});

describe("ErrorBoundary", () => {
  it("renders a throwing child as the calm fallback and logs the error", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    useAppStore.setState({ language: "ko" });

    render(
      <ErrorBoundary surface="route" showDetails={false}>
        <Boom />
      </ErrorBoundary>,
    );

    expect(screen.getByTestId("error-boundary-route")).toBeTruthy();
    expect(screen.getByRole("alert", { name: "화면을 불러오지 못했어요" })).toBeTruthy();
    expect(screen.getByText("문제가 생겼어요")).toBeTruthy();
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeTruthy();
    expect(screen.queryByTestId("error-boundary-details")).toBeNull();
    expect(spy).toHaveBeenCalled();
    expect(
      spy.mock.calls.some((call) => call.some((arg) => String(arg).includes("ErrorBoundary(route)"))),
    ).toBe(true);
  });

  it("shows collapsed details in dev mode and hides them otherwise", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    useAppStore.setState({ language: "en" });

    const { unmount } = render(
      <ErrorBoundary surface="panel" showDetails>
        <Boom message="stack goes here" />
      </ErrorBoundary>,
    );

    const details = screen.getByTestId("error-boundary-details");
    expect(details).toBeTruthy();
    expect(details.textContent).toContain("stack goes here");
    expect(screen.getByText("Something went wrong")).toBeTruthy();
    expect(screen.getByTestId("error-boundary-panel")).toBeTruthy();
    unmount();

    render(
      <ErrorBoundary surface="panel" showDetails={false}>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.queryByTestId("error-boundary-details")).toBeNull();
  });

  it("retry remounts the child after it stops throwing", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    useAppStore.setState({ language: "ko" });
    let shouldThrow = true;

    function Flip() {
      if (shouldThrow) throw new Error("once");
      return <div>recovered</div>;
    }

    render(
      <ErrorBoundary showDetails={false}>
        <Flip />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("error-boundary-route")).toBeTruthy();

    shouldThrow = false;
    fireEvent.click(screen.getByTestId("error-boundary-retry"));
    expect(screen.getByText("recovered")).toBeTruthy();
    expect(screen.queryByTestId("error-boundary-route")).toBeNull();
  });

  it("resets when resetKey changes after a failure", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    useAppStore.setState({ language: "ko" });
    let shouldThrow = true;

    function Flip() {
      if (shouldThrow) throw new Error("once");
      return <div>moved on</div>;
    }

    const { rerender } = render(
      <ErrorBoundary resetKey="act" showDetails={false}>
        <Flip />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("error-boundary-route")).toBeTruthy();

    shouldThrow = false;
    rerender(
      <ErrorBoundary resetKey="system" showDetails={false}>
        <Flip />
      </ErrorBoundary>,
    );
    expect(screen.getByText("moved on")).toBeTruthy();
  });

  it("does not reset on an unrelated prop change while still failed", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    useAppStore.setState({ language: "ko" });

    const { rerender } = render(
      <ErrorBoundary resetKey="act" showDetails>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("error-boundary-details")).toBeTruthy();

    rerender(
      <ErrorBoundary resetKey="act" showDetails={false}>
        <Boom />
      </ErrorBoundary>,
    );
    // Still failed — only resetKey is allowed to clear the captured error.
    expect(screen.getByTestId("error-boundary-route")).toBeTruthy();
  });

  it("passes a healthy tree through unchanged", () => {
    render(
      <ErrorBoundary>
        <Healthy />
      </ErrorBoundary>,
    );
    expect(screen.getByText("healthy child")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("ErrorFallback", () => {
  it("prefers an explicit language over the store", () => {
    useAppStore.setState({ language: "en" });
    render(
      <ErrorFallback
        error={new Error("")}
        surface="route"
        showDetails
        onRetry={() => {}}
        language="ko"
      />,
    );
    expect(screen.getByText("문제가 생겼어요")).toBeTruthy();
    // Empty message still renders the details block; the pre is just empty.
    expect(screen.getByTestId("error-boundary-details").querySelector("pre")?.textContent).toBe("");
  });

  it("stringifies an error-like value that has no message string", () => {
    useAppStore.setState({ language: "en" });
    render(
      <ErrorFallback
        error={{ name: "Nope" } as Error}
        surface="route"
        showDetails
        onRetry={() => {}}
      />,
    );
    expect(screen.getByTestId("error-boundary-details").querySelector("pre")?.textContent).toBe("[object Object]");
  });
});

describe("wrapped surface classes", () => {
  it("route surface: a throwing page is contained by LazyRoute", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    useAppStore.setState({ language: "ko" });

    render(
      <LazyRoute language="ko" resetKey="/act" fallback={<div>loading route</div>}>
        <Boom message="act page" />
      </LazyRoute>,
    );

    expect(await screen.findByTestId("error-boundary-route")).toBeTruthy();
    expect(screen.queryByTestId("error-boundary-panel")).toBeNull();
  });

  it("panel surface: a throwing lazy panel is contained by LazyPanel", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    useAppStore.setState({ language: "en" });

    render(
      <LazyPanel language="en" resetKey="workflows">
        <Boom message="graph widget" />
      </LazyPanel>,
    );

    expect(await screen.findByTestId("error-boundary-panel")).toBeTruthy();
    expect(screen.queryByTestId("error-boundary-route")).toBeNull();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("LazyPanel shows its token-styled loader while the child is pending", async () => {
    let release!: () => void;
    const Pending = React.lazy(
      () =>
        new Promise<{ default: React.ComponentType }>((resolve) => {
          release = () => resolve({ default: () => <div>panel ready</div> });
        }),
    );

    render(
      <LazyPanel language="ko" fallback={<PanelLoader language="ko" />}>
        <Pending />
      </LazyPanel>,
    );

    expect(screen.getByTestId("panel-loader").textContent).toBe("이 부분을 불러오는 중…");
    release();
    expect(await screen.findByText("panel ready")).toBeTruthy();
  });

  it("LazyRoute uses the fallback the shell passed in", async () => {
    const Pending = React.lazy(
      () =>
        new Promise<{ default: React.ComponentType }>((resolve) => {
          resolve({ default: () => <div>route ready</div> });
        }),
    );

    render(
      <LazyRoute language="en" fallback={<div data-testid="route-loader">Loading Brain workspace...</div>}>
        <Pending />
      </LazyRoute>,
    );

    expect(await screen.findByText("route ready")).toBeTruthy();
  });
});
