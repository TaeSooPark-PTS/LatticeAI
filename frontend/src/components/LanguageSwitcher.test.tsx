import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { LanguageSwitcher } from "./LanguageSwitcher";

/**
 * The one control a reader who cannot read the current UI still has to be able
 * to find. It has to announce which language is on (`aria-pressed`) — the
 * active style is the only other signal, and it is invisible to a screen
 * reader — and it has to survive being rendered in either of its two shapes.
 */

beforeEach(() => {
  useAppStore.setState({ language: "ko" });
});

describe("LanguageSwitcher", () => {
  it("marks the current language as pressed and switches on click", () => {
    render(<LanguageSwitcher />);

    const korean = screen.getByRole("button", { name: "한국어" });
    const english = screen.getByRole("button", { name: "English" });
    expect(korean.getAttribute("aria-pressed")).toBe("true");
    expect(english.getAttribute("aria-pressed")).toBe("false");
    expect(korean.className).toBe("is-active");

    fireEvent.click(english);

    expect(useAppStore.getState().language).toBe("en");
    expect(english.getAttribute("aria-pressed")).toBe("true");
    expect(korean.getAttribute("aria-pressed")).toBe("false");
  });

  it("names itself for a screen reader in whichever language is on", () => {
    const { rerender } = render(<LanguageSwitcher />);
    expect(screen.getByLabelText(t("ko", "language.label"))).toBeTruthy();

    useAppStore.setState({ language: "en" });
    rerender(<LanguageSwitcher />);
    expect(screen.getByLabelText(t("en", "language.label"))).toBeTruthy();
  });

  it("keeps the same controls in the roomy and the compact shape", () => {
    const { container, rerender } = render(<LanguageSwitcher />);
    // The topbar renders the compact copy and the onboarding screens the full
    // one; only the class differs, so the two must not drift into different
    // controls.
    expect(container.firstElementChild?.className).toBe("language-switcher");
    expect(screen.getAllByRole("button")).toHaveLength(2);

    rerender(<LanguageSwitcher compact />);
    expect(container.firstElementChild?.className).toBe("language-switcher compact");
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });
});
