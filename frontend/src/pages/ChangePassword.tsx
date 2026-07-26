import { useState } from "react";
import { KeyRound } from "lucide-react";

import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Alert, Field, Spinner } from "../components/ui";

const MIN_LENGTH = 8;

export default function ChangePassword() {
  const { refresh, logout } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const mismatch = repeat.length > 0 && next !== repeat;
  const tooShort = next.length > 0 && next.length < MIN_LENGTH;
  const valid = current.length > 0 && next.length >= MIN_LENGTH && next === repeat;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/auth/change-password", {
        current_password: current,
        new_password: next,
      });
      await refresh();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Password change failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-screen">
      <form className="auth-panel" onSubmit={submit}>
        <div className="auth-head">
          <div className="auth-mark">
            <KeyRound size={24} />
          </div>
          <h1>Choose a password</h1>
          <p>
            You are using the initial password. Set your own to continue, it is stored in the
            database.
          </p>
        </div>

        {error ? <Alert>{error}</Alert> : null}

        <Field label="Current password">
          <input
            type="password"
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
            autoComplete="current-password"
            autoFocus
            required
          />
        </Field>

        <Field label="New password" hint={`At least ${MIN_LENGTH} characters.`}>
          <input
            type="password"
            value={next}
            onChange={(event) => setNext(event.target.value)}
            autoComplete="new-password"
            required
          />
        </Field>

        <Field label="Repeat the new password">
          <input
            type="password"
            value={repeat}
            onChange={(event) => setRepeat(event.target.value)}
            autoComplete="new-password"
            required
          />
        </Field>

        {tooShort ? <Alert>The password must be at least {MIN_LENGTH} characters.</Alert> : null}
        {mismatch ? <Alert>The two passwords do not match.</Alert> : null}

        <button className="btn" type="submit" disabled={busy || !valid}>
          {busy ? <Spinner /> : null}
          {busy ? "Saving" : "Save and continue"}
        </button>

        <button type="button" className="btn ghost" onClick={logout}>
          Sign out
        </button>
      </form>
    </div>
  );
}
