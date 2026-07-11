import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ApiResult } from "@/api/base";
import { ActionButton } from "./primitives";

function renderActionButton(result: ApiResult<unknown>, onSuccess = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  render(
    <QueryClientProvider client={queryClient}>
      <ActionButton
        label="Run"
        action={async () => result}
        onSuccess={onSuccess}
        invalidate={["memoryManager"]}
      />
    </QueryClientProvider>,
  );
  return { invalidate, onSuccess };
}

describe("ActionButton", () => {
  it("does not call success callbacks or invalidate queries for ok:false results", async () => {
    const spies = renderActionButton({
      ok: false,
      status: 503,
      data: {},
      source: "unavailable",
      error: "service offline",
    });

    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    await screen.findByText("service offline");

    expect(spies.onSuccess).not.toHaveBeenCalled();
    expect(spies.invalidate).not.toHaveBeenCalled();
  });

  it("runs success callbacks and invalidation only for ok:true results", async () => {
    const spies = renderActionButton({ ok: true, status: 200, data: {}, source: "live" });

    await userEvent.click(screen.getByRole("button", { name: "Run" }));

    await waitFor(() => expect(spies.onSuccess).toHaveBeenCalledOnce());
    expect(spies.invalidate).toHaveBeenCalledWith({ queryKey: ["memoryManager"] });
  });
});
