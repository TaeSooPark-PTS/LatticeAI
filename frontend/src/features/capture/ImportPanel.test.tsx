import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { t } from "@/i18n";
import * as folderPicker from "@/lib/folderPicker";
import { fail, ok, renderPage, type RenderPageOptions } from "@/test/renderPage";
import ImportPanel from "./ImportPanel";

const STATUS = ok({ sources: { notion: { available: true }, git: { available: true }, mail: { available: true } } });
const PROBE = ok({ permission_required: true, approval_token: "tok" });
const OBSIDIAN_PREVIEW = ok({ files_scanned: 3, notes_found: 25, wikilinks_detected: 4, sample_files: ["a.md", "b.md"] });
const DONE = ok({
  ingested: 2,
  duplicates: 1,
  failed: 2,
  errors: [{ file: "bad.md", error: "broken note" }, { title: "Event", error: "no body" }],
});

type Body = { approved: boolean; dry_run?: boolean };

function respond(overrides: Partial<Record<"probe" | "preview" | "run", unknown>> = {}) {
  return (async (...args: unknown[]) => {
    const body = args[0] as Body;
    if (!body.approved) return overrides.probe ?? PROBE;
    if (body.dry_run) return overrides.preview ?? OBSIDIAN_PREVIEW;
    return overrides.run ?? DONE;
  });
}

function render(api: RenderPageOptions["api"] = {}) {
  return renderPage(<ImportPanel />, {
    language: "en",
    api: { interopStatus: STATUS, importFromTool: respond(), approvePermission: ok({}), ...api },
  });
}

const button = (key: string) => screen.getByRole("button", { name: t("en", key) });
const input = () => screen.getByRole("textbox", { name: t("en", "capture.import.pathLabel") });
const tab = (id: string) => screen.getByRole("button", { name: t("en", `capture.import.source.${id}`) });

async function toConsent(path = "/vault") {
  await userEvent.type(input(), path);
  await userEvent.click(button("capture.import.continue"));
  await screen.findByText(t("en", "capture.import.consentTitle"));
}

async function toPreview() {
  await toConsent();
  await userEvent.click(button("capture.import.allow"));
  await screen.findByText(t("en", "capture.import.previewTitle"));
}

afterEach(() => {
  vi.restoreAllMocks();
  delete window.latticeDesktop;
});

describe("ImportPanel flow", () => {
  it("probes, asks consent, previews, imports and lists per-item errors", async () => {
    const view = render();
    const invalidate = vi.spyOn(view.client, "invalidateQueries");
    expect(button("capture.import.continue")).toBeDisabled();
    await toConsent();
    expect(latticeApi.approvePermission).not.toHaveBeenCalled();
    await userEvent.click(button("capture.import.allow"));
    expect(latticeApi.approvePermission).toHaveBeenCalledWith("tok");
    expect(await screen.findByText("a.md")).toBeInTheDocument();
    expect(screen.getByText(t("en", "capture.import.more", { count: 23 }))).toBeInTheDocument();
    expect(latticeApi.importFromTool).toHaveBeenLastCalledWith({
      source: "obsidian", path: "/vault", approved: true, approval_token: "tok", dry_run: true,
    });
    await userEvent.click(button("capture.import.run"));
    expect(await screen.findByText("broken note")).toBeInTheDocument();
    expect(screen.getByText("Event")).toBeInTheDocument();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["documents"] });
    await userEvent.click(button("capture.import.again"));
    expect(input()).toHaveValue("");
  });

  it("shows zero counts and no error list when the server omits them", async () => {
    render({ importFromTool: respond({ run: ok({}) }) });
    await toPreview();
    await userEvent.click(button("capture.import.run"));
    await screen.findByText(t("en", "capture.import.resultTitle"));
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(screen.getAllByText("0")).toHaveLength(3);
  });

  it("offers no import when the preview finds nothing", async () => {
    render({ importFromTool: respond({ preview: ok({}) }) });
    await userEvent.click(tab("git"));
    await toPreview();
    expect(screen.getByText(t("en", "capture.import.emptyTitle"))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: t("en", "capture.import.run") })).not.toBeInTheDocument();
    await userEvent.click(button("capture.import.cancel"));
    expect(screen.queryByText(t("en", "capture.import.previewTitle"))).not.toBeInTheDocument();
  });

  it("treats an empty obsidian preview as nothing found", async () => {
    render({ importFromTool: respond({ preview: ok({}) }) });
    await toPreview();
    expect(screen.getByText(t("en", "capture.import.emptyTitle"))).toBeInTheDocument();
  });

  it("previews interop titles without a remainder line", async () => {
    render({ importFromTool: respond({ preview: ok({ items_found: 1, sample_titles: ["Roadmap"] }) }) });
    await userEvent.click(tab("notion"));
    await toPreview();
    expect(screen.getByText("Roadmap")).toBeInTheDocument();
    expect(screen.queryByText(/ more$/)).not.toBeInTheDocument();
  });

  it("previews counts even when no sample names are returned", async () => {
    render({ importFromTool: respond({ preview: ok({ items_found: 2 }) }) });
    await userEvent.click(tab("mail"));
    await toPreview();
    expect(screen.getByText(t("en", "capture.import.more", { count: 2 }))).toBeInTheDocument();
  });

  it("cancels at the consent step", async () => {
    render();
    await toConsent();
    await userEvent.click(button("capture.import.cancel"));
    expect(screen.queryByText(t("en", "capture.import.consentTitle"))).not.toBeInTheDocument();
  });
});

describe("ImportPanel failures", () => {
  it("surfaces a probe error and a probe without a token", async () => {
    const replies = [fail("ingestion off", {}), ok({})];
    render({ importFromTool: async () => replies.shift() });
    await userEvent.type(input(), "/v{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("ingestion off");
    await userEvent.click(button("capture.import.continue"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(t("en", "capture.import.unknownError")));
  });

  it("lets a non-admin continue after someone else approves", async () => {
    render({ approvePermission: fail("admin only", {}, 403) });
    await toConsent();
    await userEvent.click(button("capture.import.allow"));
    expect(await screen.findByText(t("en", "capture.import.needsAdmin"))).toBeInTheDocument();
    await userEvent.click(button("capture.import.afterApproval"));
    expect(await screen.findByText(t("en", "capture.import.previewTitle"))).toBeInTheDocument();
  });

  it("stays on consent when approval fails for another reason", async () => {
    render({ approvePermission: fail("expired", {}, 410) });
    await toConsent();
    await userEvent.click(button("capture.import.allow"));
    expect(await screen.findByRole("alert")).toHaveTextContent("expired");
    expect(button("capture.import.allow")).toBeEnabled();
  });

  it("keeps the step on preview and import errors", async () => {
    render({ importFromTool: respond({ preview: fail("no such folder", {}, 400) }) });
    await toConsent();
    await userEvent.click(button("capture.import.allow"));
    expect(await screen.findByRole("alert")).toHaveTextContent("no such folder");
  });

  it("reports an import failure and keeps the preview", async () => {
    render({ importFromTool: respond({ run: fail("token expired", {}, 403) }) });
    await toPreview();
    await userEvent.click(button("capture.import.run"));
    expect(await screen.findByRole("alert")).toHaveTextContent("token expired");
    expect(screen.getByText(t("en", "capture.import.previewTitle"))).toBeInTheDocument();
  });
});

describe("ImportPanel availability and input", () => {
  it("keeps obsidian usable but blocks interop when the catalog fails", async () => {
    render({ interopStatus: fail("offline", { sources: {} }) });
    expect(input()).toBeInTheDocument();
    await userEvent.click(tab("notion"));
    expect(await screen.findByText("offline")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    await userEvent.click(button("capture.retry"));
    await waitFor(() => expect(latticeApi.interopStatus).toHaveBeenCalledTimes(2));
  });

  it("explains a missing git binary", async () => {
    render({ interopStatus: ok({ sources: { git: { available: false } } }) });
    await userEvent.click(tab("git"));
    expect(await screen.findByText(t("en", "capture.import.toolMissing"))).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("shows a loading line while the catalog is pending and locks source changes while busy", async () => {
    const never = () => new Promise(() => {});
    render({ interopStatus: never, importFromTool: never });
    await userEvent.click(tab("mail"));
    expect(screen.getByText(t("en", "capture.import.statusLoading"))).toBeInTheDocument();
    await userEvent.type(input(), "/m");
    fireEvent.submit(input().closest("form") as HTMLFormElement);
    expect(await screen.findByText(t("en", "capture.import.busy.checking"))).toBeInTheDocument();
    fireEvent.submit(input().closest("form") as HTMLFormElement);
    await userEvent.click(tab("git"));
    expect(tab("mail")).toHaveAttribute("aria-pressed", "true");
    expect(tab("git")).toBeDisabled();
    expect(latticeApi.importFromTool).toHaveBeenCalledTimes(1);
  });

  it("ignores an empty submit and Enter during IME composition", async () => {
    render();
    fireEvent.submit(input().closest("form") as HTMLFormElement);
    expect(fireEvent.keyDown(input(), { key: "Enter", isComposing: true })).toBe(false);
    expect(fireEvent.keyDown(input(), { key: "Enter" })).toBe(true);
    expect(fireEvent.keyDown(input(), { key: "a", isComposing: true })).toBe(true);
    expect(latticeApi.importFromTool).not.toHaveBeenCalled();
  });

  it("fills the path from the desktop picker and reports a picker failure", async () => {
    window.latticeDesktop = { selectFolder: async () => null };
    const pick = vi.spyOn(folderPicker, "pickFolder")
      .mockResolvedValueOnce({ kind: "path", path: "/picked" })
      .mockResolvedValueOnce({ kind: "cancelled" })
      .mockResolvedValueOnce({ kind: "unavailable" });
    render();
    await userEvent.click(button("capture.import.browse"));
    expect(input()).toHaveValue("/picked");
    await userEvent.click(button("capture.import.browse"));
    expect(screen.queryByText(t("en", "capture.local.pickUnavailable"))).not.toBeInTheDocument();
    await userEvent.click(button("capture.import.browse"));
    expect(await screen.findByText(t("en", "capture.local.pickUnavailable"))).toBeInTheDocument();
    expect(pick).toHaveBeenCalledTimes(3);
  });
});
