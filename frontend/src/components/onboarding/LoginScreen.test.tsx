import { fireEvent, screen, waitFor } from "@testing-library/react";
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
    // ProductPromise moved to the left column (preceding the form card).
    expect(form.compareDocumentPosition(promise) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });

  it("signs in and reports success once the API accepts the credentials", async () => {
    const onSuccess = renderLogin({ login: ok({ token: "t" }) });

    await userEvent.type(screen.getByLabelText(t("ko", "flow.password")), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: t("ko", "flow.login.submit") }));

    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alert")).toBeNull();
    // Success remembers who this Brain belongs to.
    expect(JSON.parse(localStorage.getItem("lattice.productFlow.user") || "{}")).toMatchObject({
      email: "you@local",
    });
  });

  it("still refuses a submit forced through with a missing password", async () => {
    renderLogin();
    // The button is disabled, but a form can be submitted by other means; the
    // guard has to answer for itself.
    fireEvent.submit(screen.getByRole("form", { name: t("ko", "flow.login.title") }));
    expect((await screen.findByRole("alert")).textContent).toBe(t("ko", "flow.login.missing"));
  });

  it("prefills identity saved by an earlier visit", () => {
    localStorage.setItem(
      "lattice.productFlow.user",
      JSON.stringify({ email: "saved@example.com", name: "Saved Person" }),
    );
    renderLogin();
    expect((screen.getByLabelText(t("ko", "flow.email")) as HTMLInputElement).value).toBe("saved@example.com");
    expect((screen.getByLabelText(t("ko", "flow.name")) as HTMLInputElement).value).toBe("Saved Person");
  });

  it("falls back to defaults when the saved identity is corrupt", () => {
    localStorage.setItem("lattice.productFlow.user", "{not json");
    renderLogin();
    expect((screen.getByLabelText(t("ko", "flow.email")) as HTMLInputElement).value).toBe("you@local");
    expect((screen.getByLabelText(t("ko", "flow.name")) as HTMLInputElement).value).toBe("You");
  });

  it("waves a live session through when login fails but the profile answers", async () => {
    const register = vi.fn();
    const onSuccess = renderLogin({
      login: fail("no password auth", {}, 401),
      profile: ok({ email: "you@local" }),
      register,
    });
    await userEvent.type(screen.getByLabelText(t("ko", "flow.password")), "whatever");
    await userEvent.click(screen.getByRole("button", { name: t("ko", "flow.login.submit") }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
    expect(register).not.toHaveBeenCalled();
    expect(JSON.parse(localStorage.getItem("lattice.productFlow.user") || "{}")).toMatchObject({
      email: "you@local",
    });
  });

  it("accepts the profile bypass for the same saved email", async () => {
    localStorage.setItem(
      "lattice.productFlow.user",
      JSON.stringify({ email: "you@local", name: "You" }),
    );
    const onSuccess = renderLogin({
      login: fail("bad", {}, 401),
      profile: ok({ email: "you@local" }),
    });
    await userEvent.type(screen.getByLabelText(t("ko", "flow.password")), "whatever");
    await userEvent.click(screen.getByRole("button", { name: t("ko", "flow.login.submit") }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
  });

  it("names the mismatch when a different email owns this Brain", async () => {
    localStorage.setItem(
      "lattice.productFlow.user",
      JSON.stringify({ email: "owner@example.com", name: "Owner" }),
    );
    // Even a live profile does not excuse the wrong email.
    const onSuccess = renderLogin({
      login: fail("bad", {}, 401),
      profile: ok({ email: "owner@example.com" }),
    });
    // The saved owner prefills the field, so type a different address over it.
    const email = screen.getByLabelText(t("ko", "flow.email"));
    await userEvent.clear(email);
    await userEvent.type(email, "intruder@local");
    await userEvent.type(screen.getByLabelText(t("ko", "flow.password")), "whatever");
    await userEvent.click(screen.getByRole("button", { name: t("ko", "flow.login.submit") }));
    expect((await screen.findByRole("alert")).textContent).toBe(t("ko", "flow.login.otherEmail"));
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it("registers a brand-new person and signs them straight in", async () => {
    const login = vi.fn()
      .mockResolvedValueOnce(fail("unknown user", {}, 401))
      .mockResolvedValueOnce(ok({ token: "fresh" }));
    const register = vi.fn().mockResolvedValue(ok({ id: "u1" }));
    const onSuccess = renderLogin({
      login,
      profile: fail("no session", {}, 401),
      register,
    });

    const nameField = screen.getByLabelText(t("ko", "flow.name"));
    await userEvent.clear(nameField);
    await userEvent.type(screen.getByLabelText(t("ko", "flow.password")), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: t("ko", "flow.login.submit") }));

    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
    expect(login).toHaveBeenCalledTimes(2);
    // The blank name fell back to the email's local part.
    expect(register).toHaveBeenCalledWith(expect.objectContaining({ name: "you", nickname: "you" }));
  });

  it("uses a plain 'You' when even the email has no local part", async () => {
    const login = vi.fn()
      .mockResolvedValueOnce(fail("unknown", {}, 401))
      .mockResolvedValueOnce(ok({ token: "t" }));
    const register = vi.fn().mockResolvedValue(ok({}));
    renderLogin({ login, profile: fail("no", {}, 401), register });

    await userEvent.clear(screen.getByLabelText(t("ko", "flow.name")));
    const email = screen.getByLabelText(t("ko", "flow.email"));
    await userEvent.clear(email);
    await userEvent.type(email, "@example.com");
    await userEvent.type(screen.getByLabelText(t("ko", "flow.password")), "hunter2");
    // "@example.com" fails native email validation, so force the submit the
    // way a password manager or script would.
    fireEvent.submit(screen.getByRole("form", { name: t("ko", "flow.login.title") }));

    await waitFor(() => expect(register).toHaveBeenCalledWith(expect.objectContaining({ name: "You" })));
  });

  it("admits the local profile is unavailable when nothing works", async () => {
    const onSuccess = renderLogin({
      login: fail("down", {}, 503),
      profile: fail("down", {}, 503),
      register: fail("down", {}, 503),
    });
    await userEvent.type(screen.getByLabelText(t("ko", "flow.password")), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: t("ko", "flow.login.submit") }));
    expect((await screen.findByRole("alert")).textContent).toBe(t("ko", "flow.login.unavailable"));
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it("shows the working label and freezes the button while signing in", async () => {
    let release: (value: unknown) => void = () => {};
    const login = vi.fn(() => new Promise((resolve) => { release = resolve; }));
    renderLogin({ login });
    await userEvent.type(screen.getByLabelText(t("ko", "flow.password")), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: t("ko", "flow.login.submit") }));

    const busy = await screen.findByRole("button", { name: t("ko", "flow.login.busy") });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    release(ok({ token: "t" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: t("ko", "flow.login.busy") })).toBeNull());
  });
});
