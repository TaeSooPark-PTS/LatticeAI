import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { t } from "@/i18n";
import { fail, ok, renderPage, type RenderPageOptions } from "@/test/renderPage";
import { LoginScreen } from "./LoginScreen";

/**
 * The first screen anyone sees, and until 10.6.x the only one of the five
 * reorganised screens with no test at all — the rewrite that moved the promise
 * bar below the form and turned the field captions into real <label>s passed a
 * full green suite without anything having looked at it.
 *
 * What is asserted here is what a screenshot cannot see: that each input is
 * reachable by its visible caption, that an error is announced and tied to the
 * fields it describes, and that the promise bar is a named complementary
 * region rather than three loose divs.
 */

function renderLogin(api: RenderPageOptions["api"] = {}, onSuccess = vi.fn()) {
  renderPage(<LoginScreen onSuccess={onSuccess} />, { language: "ko", api });
  return onSuccess;
}

beforeEach(() => {
  localStorage.clear();
});

describe("LoginScreen", () => {
  it("labels every field so it is reachable by its visible caption", () => {
    renderLogin();
    // getByLabelText resolves through htmlFor/id. Before the rewrite these
    // captions were plain divs and none of these three queries would resolve.
    expect(screen.getByLabelText(t("ko", "flow.name"))).toBeTruthy();
    expect(screen.getByLabelText(t("ko", "flow.email"))).toBeTruthy();
    expect(screen.getByLabelText(t("ko", "flow.password"))).toBeTruthy();
  });

  it("names the form from the heading it sits under", () => {
    renderLogin();
    expect(screen.getByRole("form", { name: t("ko", "flow.login.title") })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: t("ko", "flow.login.title") })).toBeTruthy();
  });

  it("marks only the fields that are genuinely required", () => {
    renderLogin();
    // The name falls back to the email's local part, so it is optional. It had
    // been marked aria-required alongside the other two, which tells a screen
    // reader user to fill in something the submit button does not wait for.
    expect(screen.getByLabelText(t("ko", "flow.name")).hasAttribute("required")).toBe(false);
    expect(screen.getByLabelText(t("ko", "flow.email")).hasAttribute("required")).toBe(true);
    expect(screen.getByLabelText(t("ko", "flow.password")).hasAttribute("required")).toBe(true);
  });

  it("keeps submit disabled until both required fields carry a value", async () => {
    renderLogin();
    const submit = screen.getByRole("button", { name: t("ko", "flow.login.submit") });
    // The email is prefilled; the password is not.
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    await userEvent.type(screen.getByLabelText(t("ko", "flow.password")), "hunter2");
    expect((submit as HTMLButtonElement).disabled).toBe(false);
  });

  it("announces a failure and points the fields at the message describing it", async () => {
    localStorage.setItem(
      "lattice.productFlow.user",
      JSON.stringify({ email: "you@local", name: "You" }),
    );
    // A rejected login for a known email is the wrong-password branch — but
    // only once the profile probe also fails. A profile that still answers
    // means the session is live and the screen waves the person through.
    renderLogin({
      login: fail("bad", {}, 401),
      profile: fail("no session", {}, 401),
    });

    await userEvent.type(screen.getByLabelText(t("ko", "flow.password")), "wrong-one");
    await userEvent.click(screen.getByRole("button", { name: t("ko", "flow.login.submit") }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(t("ko", "flow.login.wrongPassword"));

    const password = screen.getByLabelText(t("ko", "flow.password"));
    expect(password.getAttribute("aria-invalid")).toBe("true");
    // The description has to resolve to the alert, not merely be present.
    expect(password.getAttribute("aria-describedby")).toBe(alert.id);
    expect(alert.id).toBeTruthy();
  });

  it("carries no aria-invalid before anything has gone wrong", () => {
    renderLogin();
    // aria-invalid="false" on a pristine field is still announced by some
    // screen readers, so the attribute is omitted rather than set to false.
    expect(screen.getByLabelText(t("ko", "flow.email")).hasAttribute("aria-invalid")).toBe(false);
    expect(screen.getByLabelText(t("ko", "flow.password")).hasAttribute("aria-describedby")).toBe(false);
  });

  it("keeps the promise bar as a named region below the form", () => {
    renderLogin();
    const promise = screen.getByRole("complementary", { name: t("ko", "flow.promise.aria") });
    expect(promise).toBeTruthy();
    // Reordered, not deleted: all three promises survive the move.
    expect(promise.textContent).toContain(t("ko", "flow.promise.memory.v"));
    expect(promise.textContent).toContain(t("ko", "flow.promise.model.v"));
    expect(promise.textContent).toContain(t("ko", "flow.promise.ownership.v"));

    const form = screen.getByRole("form", { name: t("ko", "flow.login.title") });
    // DOCUMENT_POSITION_FOLLOWING === 4: the bar reads after the form now.
    expect(form.compareDocumentPosition(promise) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("signs in and reports success once the API accepts the credentials", async () => {
    const onSuccess = renderLogin({ login: ok({ token: "t" }) });

    await userEvent.type(screen.getByLabelText(t("ko", "flow.password")), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: t("ko", "flow.login.submit") }));

    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
