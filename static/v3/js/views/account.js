import { t } from "../core/i18n.js";

export async function render(ctx) {
  const { h, icon, api, store, c, toast } = ctx;
  const host = h("div.lt3-stack-6", c.loading({ lines: 4 }));

  async function load() {
    const [profile, sso] = await Promise.all([api.profile(), api.ssoConfig()]);
    host.replaceChildren(
      c.viewHeader({
        eyebrow: t("account.eyebrow"),
        title: t("account.title"),
        sub: t("account.sub"),
        actions: [c.sourceBadge(profile.ok ? "live" : "unavailable")],
      }),
      profile.ok && profile.data ? signedInPanel(profile.data, sso.data || {}) : authPanel(sso.data || {}),
    );
    if (profile.ok && profile.data) {
      store.setUser({
        email: profile.data.email || "",
        nickname: profile.data.nickname || profile.data.name || profile.data.email || t("shell.you"),
        role: profile.data.role || "user",
      });
    }
  }

  function authPanel(sso) {
    const email = input("email", t("account.email"));
    const password = input("password", t("account.password"));
    const regEmail = input("email", t("account.email"));
    const regName = input("text", t("account.name"));
    const regNick = input("text", t("account.nickname"));
    const regPassword = input("password", t("account.password"));

    async function doLogin() {
      const res = await api.login(email.value.trim(), password.value);
      toast(resultText(res, t("account.loginOk")), res.ok ? "ok" : "err");
      if (res.ok) load();
    }
    async function doRegister() {
      const res = await api.register({
        email: regEmail.value.trim(),
        password: regPassword.value,
        name: regName.value.trim(),
        nickname: regNick.value.trim(),
      });
      toast(resultText(res, t("account.registerOk")), res.ok ? "ok" : "err");
      if (res.ok) load();
    }

    return h("div.lt3-grid-2",
      c.panel({
        title: t("account.login"),
        children: h("div.lt3-stack-4",
          field(ctx, t("account.email"), email),
          field(ctx, t("account.password"), password),
          h("button.lt3-btn.lt3-btn--primary", { on: { click: doLogin } }, icon("login"), t("account.login")),
        ),
      }),
      c.panel({
        title: t("account.register"),
        sub: t("account.passwordRule"),
        children: h("div.lt3-stack-4",
          field(ctx, t("account.email"), regEmail),
          field(ctx, t("account.name"), regName),
          field(ctx, t("account.nickname"), regNick),
          field(ctx, t("account.password"), regPassword),
          h("button.lt3-btn.lt3-btn--primary", { on: { click: doRegister } }, icon("user-plus"), t("account.register")),
          sso && sso.enabled ? c.banner(t("account.sso"), "info") : null,
        ),
      }),
    );
  }

  function signedInPanel(profile, sso) {
    const name = input("text", t("account.name"), profile.name || "");
    const nick = input("text", t("account.nickname"), profile.nickname || "");
    const current = input("password", t("account.currentPassword"));
    const next = input("password", t("account.newPassword"));

    async function saveProfile() {
      const res = await api.updateProfile({ name: name.value.trim(), nickname: nick.value.trim() });
      toast(resultText(res, t("account.profileOk")), res.ok ? "ok" : "err");
      if (res.ok) load();
    }
    async function savePassword() {
      const res = await api.changePassword(current.value, next.value);
      toast(resultText(res, t("account.passwordOk")), res.ok ? "ok" : "err");
      if (res.ok) { current.value = ""; next.value = ""; }
    }
    async function doLogout() {
      const res = await api.logout();
      toast(resultText(res, t("account.logoutOk")), res.ok ? "ok" : "err");
      if (res.ok) load();
    }

    return h("div.lt3-grid-2",
      c.panel({
        title: t("account.profile"),
        actions: [c.statePill(t("account.signedIn"))],
        children: h("div.lt3-stack-4",
          h("dl.lt3-keyval",
            h("dt", t("account.email")), h("dd", h("span.lt3-mono", profile.email || "—")),
            h("dt", t("common.role")), h("dd", c.pill(profile.role || "user", "info")),
            h("dt", t("account.sso")), h("dd", c.statePill(sso && sso.enabled ? "ready" : "idle")),
          ),
          field(ctx, t("account.name"), name),
          field(ctx, t("account.nickname"), nick),
          h("div.lt3-row-2",
            h("button.lt3-btn.lt3-btn--primary", { on: { click: saveProfile } }, icon("device-floppy"), t("common.save")),
            h("button.lt3-btn.lt3-btn--ghost", { on: { click: doLogout } }, icon("logout"), t("account.logout")),
          ),
        ),
      }),
      c.panel({
        title: t("account.changePassword"),
        sub: t("account.passwordRule"),
        children: h("div.lt3-stack-4",
          field(ctx, t("account.currentPassword"), current),
          field(ctx, t("account.newPassword"), next),
          h("button.lt3-btn.lt3-btn--primary", { on: { click: savePassword } }, icon("key"), t("account.changePassword")),
        ),
      }),
    );
  }

  await load();
  return host;

  function input(type, label, value = "") {
    return h("input.lt3-input", { type, value, autocomplete: type === "password" ? "current-password" : "on", "aria-label": label });
  }
}

function field({ h }, label, control) {
  return h("div.lt3-field", h("label.lt3-label", label), control);
}

function resultText(res, okText) {
  if (res && res.ok) return okText;
  const data = (res && res.data) || {};
  return String(data.detail || data.error || res?.error || t("common.unavailable"));
}
