import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { LANGUAGE_LABELS, t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { LanguageChooser } from "./LanguageChooser";

/**
 * The first control a new person can reach: the onboarding language toggle.
 * It must show both languages, mark the active one, and actually switch the
 * store (and its persisted copy) when pressed.
 */

beforeEach(() => {
  useAppStore.setState({ language: "ko" });
});

describe("LanguageChooser", () => {
  it("offers both languages with the active one pressed", () => {
    render(<LanguageChooser />);
    const group = screen.getByLabelText(t("ko", "language.label"));
    const buttons = Array.from(group.querySelectorAll("button"));
    expect(buttons.map((button) => button.textContent)).toEqual([
      LANGUAGE_LABELS.ko,
      LANGUAGE_LABELS.en,
    ]);
    expect(buttons[0].getAttribute("aria-pressed")).toBe("true");
    expect(buttons[0].className).toContain("is-active");
    expect(buttons[1].getAttribute("aria-pressed")).toBe("false");
  });

  it("switches and persists the language on click", () => {
    render(<LanguageChooser />);
    fireEvent.click(screen.getByRole("button", { name: LANGUAGE_LABELS.en }));
    expect(useAppStore.getState().language).toBe("en");
    expect(localStorage.getItem("lattice.language")).toBe("en");
    // Now the labels re-render with English active.
    expect(
      screen.getByRole("button", { name: LANGUAGE_LABELS.en }).getAttribute("aria-pressed"),
    ).toBe("true");
  });
});
