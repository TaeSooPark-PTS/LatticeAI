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
    <div>
      <div className="ritual-title">{t(language, "flow.login.title")}</div>
      <div className="ritual-subtitle">{t(language, "flow.login.body")}</div>

      <ProductPromise />

      <form onSubmit={submit} className="ritual-card ritual-form">
        <div className="ritual-field-stack">
          <div>
            <div className="ritual-field-label">{t(language, "flow.name")}</div>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t(language, "flow.name.placeholder")}
              autoComplete="name"
            />
          </div>
          <div>
            <div className="ritual-field-label">{t(language, "flow.email")}</div>
            <Input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              type="email"
              placeholder={t(language, "flow.email.placeholder")}
              autoComplete="email"
            />
          </div>
          <div>
            <div className="ritual-field-label">{t(language, "flow.password")}</div>
            <Input
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
              placeholder={t(language, "flow.password.placeholder")}
              autoComplete="current-password"
            />
          </div>
        </div>

        {error && <div className="ritual-error" role="alert">{error}</div>}
        <div className="ritual-muted-hint">{t(language, "flow.login.passwordLocal")}</div>

        <Button type="submit" disabled={busy || !email.trim() || !password.trim()} className="ritual-full-button">
          {busy ? t(language, "flow.login.busy") : t(language, "flow.login.submit")}
        </Button>
        <div className="ritual-note">
          {t(language, "flow.login.note")}
        </div>
      </form>
    </div>
  );
}

function ProductPromise() {
  const language = useAppStore((state) => state.language);
  return (
    <div className="ritual-promise" aria-label={t(language, "flow.promise.aria")}>
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
    </div>
  );
}
