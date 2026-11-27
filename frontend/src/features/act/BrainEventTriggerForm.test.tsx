import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { fail, ok, renderPage } from "@/test/renderPage";
import { BrainEventTriggerForm } from "./BrainEventTriggerForm";

const output = { id: "output", type: "output" };
const workflow = (config: Record<string, unknown>) => ({
  id: "wf-1",
  nodes: [{ id: "trigger", type: "trigger", config }, output],
});

describe("BrainEventTriggerForm", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("renders nothing for workflows without a brain event trigger", () => {
    const { container } = renderPage(
      <>
        <BrainEventTriggerForm workflow={workflow({ trigger: "manual" })} language="en" />
        <BrainEventTriggerForm workflow={{ id: "x" }} language="en" />
        <BrainEventTriggerForm workflow={{ nodes: [{ type: "trigger" }] }} language="en" />
      </>,
    );
    expect(container.textContent).toBe("");
  });

  it("saves the three conditions into the trigger node config", async () => {
    renderPage(<BrainEventTriggerForm workflow={workflow({ trigger: "brain_event", enabled: true })} language="en" />, {
      api: { updateWorkflow: ok({}) },
    });
    await userEvent.click(screen.getByText("Run conditions"));
    await userEvent.selectOptions(screen.getByRole("combobox"), "workspace");
    await userEvent.type(screen.getByRole("textbox", { name: /Source types/ }), "upload, note ,");
    await userEvent.type(screen.getByRole("textbox", { name: "Title contains" }), " invoice ");
    await userEvent.click(screen.getByRole("button", { name: "Save conditions" }));
    expect(await screen.findByText("Conditions saved.")).toBeTruthy();
    expect(latticeApi.updateWorkflow).toHaveBeenCalledWith("wf-1", {
      nodes: [
        {
          id: "trigger",
          type: "trigger",
          config: {
            trigger: "brain_event",
            enabled: true,
            owner_scope: "workspace",
            source_types: ["upload", "note"],
            title_contains: "invoice",
          },
        },
        output,
      ],
    });
  });

  it("loads existing values and drops cleared filters", async () => {
    const config = { trigger: "brain_event", owner_scope: "workspace", source_types: ["file"], title_contains: "old" };
    renderPage(<BrainEventTriggerForm workflow={{ workflow_id: "wf-2", nodes: workflow(config).nodes }} language="en" />, {
      api: { updateWorkflow: fail("not allowed", {}) },
    });
    const sources = screen.getByRole("textbox", { name: /Source types/, hidden: true });
    const title = screen.getByRole("textbox", { name: "Title contains", hidden: true });
    expect((sources as HTMLInputElement).value).toBe("file");
    await userEvent.click(screen.getByText("Run conditions"));
    await userEvent.clear(sources);
    await userEvent.clear(title);
    await userEvent.click(screen.getByRole("button", { name: "Save conditions" }));
    expect(await screen.findByText("not allowed")).toBeTruthy();
    const body = vi.mocked(latticeApi.updateWorkflow).mock.calls[0]![1] as { nodes: Array<{ config?: unknown }> };
    expect(body.nodes[0]!.config).toEqual({ trigger: "brain_event", owner_scope: "workspace" });
  });

  it("names an unexplained failure", async () => {
    renderPage(<BrainEventTriggerForm workflow={workflow({ trigger: "brain_event" })} language="en" />, {
      api: { updateWorkflow: fail("", {}) },
    });
    await userEvent.click(screen.getByText("Run conditions"));
    await userEvent.click(screen.getByRole("button", { name: "Save conditions" }));
    await waitFor(() => expect(screen.getByRole("status").className).toContain("text-warning"));
  });
});
