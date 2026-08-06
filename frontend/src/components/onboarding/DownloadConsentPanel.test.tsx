import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { DownloadConsentPanel } from "./DownloadConsentPanel";
import { fallbackModel, type RecommendedModel } from "./recommendationModel";

/**
 * The consent panel is an honesty surface: before anything is fetched it has
 * to say how big the download is, where it will live, and which external host
 * it comes from — or admit that a fact is unknown rather than invent one.
 */

function model(overrides: Partial<RecommendedModel> = {}): RecommendedModel {
  return { ...fallbackModel(), ...overrides };
}

beforeEach(() => {
  useAppStore.setState({ language: "ko" });
});

describe("DownloadConsentPanel", () => {
  it("lists size, location and host for a download that is required", () => {
    render(
      <DownloadConsentPanel
        model={model({ downloadRequired: true, downloadSize: "4.6GB", externalHost: "huggingface" })}
      />,
    );
    expect(screen.getByRole("region", { name: t("ko", "flow.consent.title") })).toBeTruthy();
    expect(screen.getByText("4.6GB")).toBeTruthy();
    expect(screen.getByText("huggingface")).toBeTruthy();
    expect(screen.getByText("~/.latticeai/models")).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.consent.body"))).toBeTruthy();
  });

  it("admits unknown size and host instead of inventing them", () => {
    render(
      <DownloadConsentPanel model={model({ downloadRequired: true, downloadSize: "", externalHost: "" })} />,
    );
    // Size and host share the same "checked later" copy in Korean, so both
    // unknown cells must carry it.
    const unknowns = [t("ko", "flow.consent.sizeUnknown"), t("ko", "flow.consent.externalUnknown")];
    const cells = screen.getAllByText((text) => unknowns.includes(text));
    expect(cells.length).toBeGreaterThanOrEqual(2);
  });

  it("describes an already-local model with no external host at all", () => {
    render(<DownloadConsentPanel model={model({ downloadRequired: false })} />);
    expect(screen.getByRole("region", { name: t("ko", "flow.consent.readyTitle") })).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.consent.stateReady"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.consent.externalNone"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.consent.ready"))).toBeTruthy();
  });
});
