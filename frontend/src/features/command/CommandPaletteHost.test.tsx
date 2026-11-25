
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
    expect(await screen.findByTestId("command-palette")).toBeTruthy();
  });

  it("opens on the custom open event, from anywhere in the app", async () => {
    renderHost();
    fireEvent(window, new Event("lattice:open-command"));
    expect(await screen.findByTestId("command-palette")).toBeTruthy();

    await userEvent.keyboard("{Meta>}k{/Meta}");
    expect(screen.queryByTestId("command-palette")).toBeNull();
    await userEvent.keyboard("{Meta>}k{/Meta}");
    expect(screen.getByTestId("command-palette")).toBeTruthy();
  });
});
