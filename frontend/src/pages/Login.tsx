import { useState } from "react";
import { CloudUpload } from "lucide-react";

import { useAuth } from "../lib/auth";
import { Alert, Field, Spinner } from "../components/ui";

export default function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Accesso non riuscito");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-screen">
      <form className="auth-panel" onSubmit={submit}>
        <div className="auth-head">
          <div className="auth-mark">
            <CloudUpload size={26} />
          </div>
          <h1>tgbackup</h1>
          <p>Accedi per gestire i backup delle tue cartelle su Telegram.</p>
        </div>

        {error ? <Alert>{error}</Alert> : null}

        <Field label="Nome utente">
          <input
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            autoFocus
            required
          />
        </Field>

        <Field label="Password">
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
          />
        </Field>

        <button className="btn" type="submit" disabled={busy || !username || !password}>
          {busy ? <Spinner /> : null}
          {busy ? "Accesso in corso" : "Accedi"}
        </button>
      </form>
    </div>
  );
}
