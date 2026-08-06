/**
 * The palette carries its own search UI and query wiring, so the shell keeps it
 * out of the initial chunk and loads it on the first Cmd/Ctrl+K. Two things can
 * go wrong with that and neither shows up in a screenshot: the gesture that
 * loads the chunk not also *opening* the palette (so the first press appears to
 * do nothing), and the host still listening after it has handed over.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { CommandPaletteHost } from "./CommandPaletteHost";

beforeEach(() => {
  vi.spyOn(latticeApi, "commandBriefing").mockResolvedValue({
    ok: false, status: 503, source: "unavailable", data: {},
  } as never);
});

function renderHost() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <CommandPaletteHost language="ko" />
    </QueryClientProvider>,
  );
}

describe("CommandPaletteHost", () => {
  it("renders nothing, and loads nothing, until the palette is asked for", async () => {
    const { container } = renderHost();
    expect(container.innerHTML).toBe("");

    await userEvent.keyboard("a");
    await userEvent.keyboard("{Meta>}j{/Meta}");
    expect(screen.queryByTestId("command-palette")).toBeNull();
  });

  it("opens the palette on the same Ctrl+K that loads it", async () => {
    renderHost();
    await userEvent.keyboard("{Control>}k{/Control}");
    // `initialOpen` is the whole point: a first press that only primed the
    // chunk would read as a dead shortcut.
    expect(await screen.findByTestId("command-palette")).toBeTruthy();
  });

  it("opens on the custom open event, from anywhere in the app", async () => {
    renderHost();
    fireEvent(window, new Event("lattice:open-command"));
    expect(await screen.findByTestId("command-palette")).toBeTruthy();

    // Once handed over, the palette owns the shortcut: the host has stopped
    // listening, so Cmd+K now toggles rather than re-activating.
    await userEvent.keyboard("{Meta>}k{/Meta}");
    expect(screen.queryByTestId("command-palette")).toBeNull();
    await userEvent.keyboard("{Meta>}k{/Meta}");
    expect(screen.getByTestId("command-palette")).toBeTruthy();
  });
});
