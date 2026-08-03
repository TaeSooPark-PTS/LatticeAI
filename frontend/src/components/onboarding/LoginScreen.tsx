import * as React from "react";
import { latticeApi } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";

const FLOW_USER_KEY = "lattice.productFlow.user";

function readSavedFlowUser(): { email?: string; name?: string } | null {
  try {
    const raw = localStorage.getItem(FLOW_USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {}
  return null;
}

export function LoginScreen({ onSuccess }: { onSuccess: () => void }) {
  const language = useAppStore((state) => state.language);
  const [email, setEmail] = React.useState(() => {
    return readSavedFlowUser()?.email || "you@local";
  });
  const [password, setPassword] = React.useState("");
  const [name, setName] = React.useState(() => readSavedFlowUser()?.name || "You");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const cleanEmail = email.trim();
    const cleanPassword = password.trim();
    const cleanName = name.trim() || cleanEmail.split("@")[0] || "You";
    if (!cleanEmail || !cleanPassword) {
      setError(t(language, "flow.login.missing"));
      return;
    }
    setBusy(true);
    setError(null);
    const savedUser = readSavedFlowUser();
    let result = await latticeApi.login(cleanEmail, cleanPassword);
    if (!result.ok) {
      const profile = await latticeApi.profile();
      if (profile.ok && (!savedUser?.email || savedUser.email === cleanEmail)) {
        try { localStorage.setItem(FLOW_USER_KEY, JSON.stringify({ email: cleanEmail, name: cleanName })); } catch {}
        setBusy(false);
        onSuccess();
        return;
      }
      if (savedUser?.email && savedUser.email !== cleanEmail) {
        setBusy(false);
        setError(t(language, "flow.login.otherEmail"));
        return;
      }
      if (savedUser?.email === cleanEmail) {
        setBusy(false);
        setError(t(language, "flow.login.wrongPassword"));
        return;
      }
      const registered = await latticeApi.register({
        email: cleanEmail,
        password: cleanPassword,
        name: cleanName,
        nickname: cleanName,
      });
      if (registered.ok) result = await latticeApi.login(cleanEmail, cleanPassword);
    }
    if (!result.ok) {
      setBusy(false);
      setError(t(language, "flow.login.unavailable"));
      return;
    }
    try { localStorage.setItem(FLOW_USER_KEY, JSON.stringify({ email: cleanEmail, name: cleanName })); } catch {}
    setBusy(false);
    onSuccess();
  }

  return (
    <div className="ritual-login">
      <div className="ritual-login-grid">
        <div className="ritual-login-left">
          <p className="ritual-login-statement">{t(language, "flow.login.statement")}</p>
        </div>

        <div className="ritual-login-right">
          <header>
            <h1 id="login-title" className="ritual-title">
              {t(language, "flow.login.title")}
            </h1>
            <p className="ritual-subtitle">{t(language, "flow.login.body")}</p>
          </header>

          {/* The form is the whole job of this screen, so it is the only raised
              surface on it. */}
          <div className="ritual-login-card">
            <form onSubmit={submit} className="ritual-form" aria-labelledby="login-title">
              <div className="ritual-field-stack">
                <div>
                  <label htmlFor="login-name" className="ritual-field-label">
                    {t(language, "flow.name")}
                  </label>
                  <Input
                    id="login-name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder={t(language, "flow.name.placeholder")}
                    autoComplete="name"
                  />
                </div>
                <div>
                  <label htmlFor="login-email" className="ritual-field-label">
                    {t(language, "flow.email")}
                  </label>
                  <Input
                    id="login-email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    type="email"
                    placeholder={t(language, "flow.email.placeholder")}
                    autoComplete="email"
                    required
                    aria-invalid={error ? true : undefined}
                    aria-describedby={error ? "login-error" : undefined}
                  />
                </div>
                <div>
                  <label htmlFor="login-password" className="ritual-field-label">
                    {t(language, "flow.password")}
                  </label>
                  <Input
                    id="login-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    type="password"
                    placeholder={t(language, "flow.password.placeholder")}
                    autoComplete="current-password"
                    required
                    aria-invalid={error ? true : undefined}
                    aria-describedby={error ? "login-error" : undefined}
                  />
                </div>
              </div>

              {error && <div id="login-error" className="ritual-error" role="alert">{error}</div>}

              <Button type="submit" disabled={busy || !email.trim() || !password.trim()} className="ritual-full-button">
                {busy ? t(language, "flow.login.busy") : t(language, "flow.login.submit")}
              </Button>

              <div className="ritual-login-footnotes">
                <div className="ritual-muted-hint">{t(language, "flow.login.passwordLocal")}</div>
                <div className="ritual-note">{t(language, "flow.login.note")}</div>
              </div>
            </form>
          </div>
        </div>
      </div>
      <ProductPromise />
    </div>
  );
}

function ProductPromise() {
  const language = useAppStore((state) => state.language);
  return (
    // Three bordered cards competing with the form became one quiet bar with
    // hairline separators — same three facts, no second set of boxes under the
    // one box that matters. The `.is-quiet` modifier carries that in styles.css;
    // border/background utilities here would have lost to `.ritual-promise div`
    // and only added an outer box around the inner ones.
    <aside className="ritual-promise is-quiet" aria-label={t(language, "flow.promise.aria")}>
      <div>
        <span>{t(language, "flow.promise.memory.k")}</span>
        <strong>{t(language, "flow.promise.memory.v")}</strong>
      </div>
      <div>
        <span>{t(language, "flow.promise.model.k")}</span>
        <strong>{t(language, "flow.promise.model.v")}</strong>
      </div>
      <div>
        <span>{t(language, "flow.promise.ownership.k")}</span>
        <strong>{t(language, "flow.promise.ownership.v")}</strong>
      </div>
    </aside>
  );
}
