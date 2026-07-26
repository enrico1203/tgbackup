import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Crown, Hash, Plus, RefreshCcw, Settings2, Trash2, UserCircle2 } from "lucide-react";

import { api } from "../lib/api";
import { formatBytes, formatDateTime } from "../lib/format";
import type { Account, AccountStep, Channel } from "../lib/types";
import { Alert, Card, CardHead, Empty, Field, Modal, Pill, Spinner } from "../components/ui";

type Step = "details" | "code" | "password";

function LinkAccountModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [step, setStep] = useState<Step>("details");
  const [accountId, setAccountId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [label, setLabel] = useState("");
  const [apiId, setApiId] = useState("");
  const [apiHash, setApiHash] = useState("");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Operazione non riuscita");
    } finally {
      setBusy(false);
    }
  };

  const startLogin = () =>
    run(async () => {
      const response = await api.post<AccountStep>("/api/accounts/start", {
        label: label.trim(),
        api_id: Number(apiId),
        api_hash: apiHash.trim(),
        phone: phone.trim(),
      });
      setAccountId(response.account_id);
      setStep("code");
    });

  const sendCode = () =>
    run(async () => {
      const response = await api.post<AccountStep>(`/api/accounts/${accountId}/code`, {
        code: code.trim(),
      });
      if (response.needs === "password") {
        setStep("password");
        return;
      }
      await queryClient.invalidateQueries({ queryKey: ["accounts"] });
      onClose();
    });

  const sendPassword = () =>
    run(async () => {
      await api.post<AccountStep>(`/api/accounts/${accountId}/password`, { password });
      await queryClient.invalidateQueries({ queryKey: ["accounts"] });
      onClose();
    });

  const stepIndex = step === "details" ? 0 : step === "code" ? 1 : 2;

  return (
    <Modal
      title="Collega un account Telegram"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn ghost" onClick={onClose} disabled={busy}>
            Annulla
          </button>
          {step === "details" ? (
            <button
              type="button"
              className="btn"
              disabled={busy || !label || !apiId || !apiHash || !phone}
              onClick={startLogin}
            >
              {busy ? <Spinner /> : null}
              Invia il codice
            </button>
          ) : step === "code" ? (
            <button type="button" className="btn" disabled={busy || code.length < 3} onClick={sendCode}>
              {busy ? <Spinner /> : null}
              Verifica il codice
            </button>
          ) : (
            <button type="button" className="btn" disabled={busy || !password} onClick={sendPassword}>
              {busy ? <Spinner /> : null}
              Conferma
            </button>
          )}
        </>
      }
    >
      <div className="steps">
        {["Credenziali", "Codice", "Verifica in due passaggi"].map((name, index) => (
          <div
            key={name}
            className={`step ${index === stepIndex ? "active" : index < stepIndex ? "done" : ""}`}
          >
            <span className="step-num">{index + 1}</span>
            <span>{name}</span>
            {index < 2 ? <span className="step-line" /> : null}
          </div>
        ))}
      </div>

      {error ? <Alert>{error}</Alert> : null}

      {step === "details" ? (
        <>
          <Alert tone="info">
            api_id e api_hash si ottengono su my.telegram.org, sezione API development tools.
            Vengono salvati cifrati nel database.
          </Alert>
          <Field label="Nome dell'account" hint="Serve solo a te per riconoscerlo nell'elenco.">
            <input value={label} onChange={(e) => setLabel(e.target.value)} autoFocus />
          </Field>
          <div className="grid-2">
            <Field label="api_id">
              <input value={apiId} onChange={(e) => setApiId(e.target.value.replace(/\D/g, ""))} inputMode="numeric" />
            </Field>
            <Field label="api_hash">
              <input value={apiHash} onChange={(e) => setApiHash(e.target.value)} />
            </Field>
          </div>
          <Field label="Numero di telefono" hint="Con il prefisso internazionale, ad esempio +39...">
            <input value={phone} onChange={(e) => setPhone(e.target.value)} />
          </Field>
        </>
      ) : step === "code" ? (
        <>
          <Alert tone="info">
            Telegram ha inviato un codice all'app collegata al numero {phone}.
          </Alert>
          <Field label="Codice di accesso">
            <input
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              inputMode="numeric"
              autoFocus
            />
          </Field>
        </>
      ) : (
        <>
          <Alert tone="info">
            L'account ha la verifica in due passaggi attiva. Inserisci la password del cloud
            Telegram.
          </Alert>
          <Field label="Password della verifica in due passaggi">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
            />
          </Field>
        </>
      )}
    </Modal>
  );
}

function ChannelList({ accountId }: { accountId: number }) {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["channels", accountId],
    queryFn: () => api.get<Channel[]>(`/api/accounts/${accountId}/channels`),
  });

  const refresh = useMutation({
    mutationFn: () => api.get<Channel[]>(`/api/accounts/${accountId}/channels?refresh=true`),
    onSuccess: (channels) => queryClient.setQueryData(["channels", accountId], channels),
  });

  const privateOnes = (data ?? []).filter((channel) => channel.is_private);

  return (
    <div className="card-body">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="section-label">
          Canali privati ({privateOnes.length} su {data?.length ?? 0} chat)
        </span>
        <button
          type="button"
          className="btn ghost small"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
        >
          {refresh.isPending ? <Spinner size={13} /> : <RefreshCcw size={13} />}
          Aggiorna
        </button>
      </div>

      {refresh.isError ? <Alert>{(refresh.error as Error).message}</Alert> : null}

      {isLoading ? (
        <div className="row" style={{ color: "var(--muted)" }}>
          <Spinner /> Caricamento dei canali
        </div>
      ) : privateOnes.length === 0 ? (
        <Empty
          icon={<Hash size={24} color="var(--muted)" />}
          title="Nessun canale privato trovato"
          hint="Crea un canale privato su Telegram, poi premi Aggiorna."
        />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Canale</th>
                <th>Tipo</th>
                <th className="right">Iscritti</th>
              </tr>
            </thead>
            <tbody>
              {privateOnes.map((channel) => (
                <tr key={channel.id}>
                  <td>{channel.title}</td>
                  <td>
                    <Pill tone="mute">
                      {channel.kind === "channel"
                        ? "Canale"
                        : channel.kind === "supergroup"
                          ? "Supergruppo"
                          : "Gruppo"}
                    </Pill>
                  </td>
                  <td className="right num">{channel.participants ?? "n.d."}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const MAX_DC_CONNECTIONS = 20;

function AccountSettingsModal({
  account,
  onClose,
}: {
  account: Account;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [label, setLabel] = useState(account.label);
  const [concurrency, setConcurrency] = useState(String(account.max_concurrent_jobs));

  const save = useMutation({
    mutationFn: () =>
      api.patch<Account>(`/api/accounts/${account.id}`, {
        label: label.trim(),
        max_concurrent_jobs: Number(concurrency),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["accounts"] });
      onClose();
    },
  });

  const jobs = Math.max(1, Number(concurrency) || 1);
  const perJob = Math.max(1, Math.floor(MAX_DC_CONNECTIONS / jobs));

  return (
    <Modal
      title={`Impostazioni di ${account.label}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn ghost" onClick={onClose}>
            Annulla
          </button>
          <button
            type="button"
            className="btn"
            disabled={!label.trim() || jobs < 1 || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? <Spinner /> : null}
            Salva
          </button>
        </>
      }
    >
      {save.isError ? <Alert>{(save.error as Error).message}</Alert> : null}

      <Field label="Nome dell'account">
        <input value={label} onChange={(e) => setLabel(e.target.value)} />
      </Field>

      <Field
        label="Job che possono caricare contemporaneamente"
        hint="Da 1 a 20. Oltre questo numero i job restano in coda e la loro scheda mostra In coda."
      >
        <input
          value={concurrency}
          inputMode="numeric"
          onChange={(e) => setConcurrency(e.target.value.replace(/\D/g, ""))}
        />
      </Field>

      <Alert tone="info">
        Telegram accetta al massimo {MAX_DC_CONNECTIONS} connessioni per data center, quindi il
        budget viene diviso: con {jobs} job insieme ognuno ne usa {perJob}. Alzare questo numero
        non aumenta la banda totale, la distribuisce fra piu job.
      </Alert>
    </Modal>
  );
}

export default function Accounts() {
  const queryClient = useQueryClient();
  const [linking, setLinking] = useState(false);
  const [editing, setEditing] = useState<Account | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.get<Account[]>("/api/accounts"),
    refetchInterval: 15000,
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/api/accounts/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["accounts"] }),
  });

  return (
    <>
      {remove.isError ? <Alert>{(remove.error as Error).message}</Alert> : null}

      <Card>
        <CardHead title="Account collegati">
          <button type="button" className="btn small" onClick={() => setLinking(true)}>
            <Plus size={14} />
            Collega account
          </button>
        </CardHead>

        {isLoading ? (
          <div className="card-body row" style={{ color: "var(--muted)" }}>
            <Spinner /> Caricamento
          </div>
        ) : !data || data.length === 0 ? (
          <Empty
            icon={<UserCircle2 size={26} color="var(--muted)" />}
            title="Nessun account Telegram collegato"
            hint="Collega un account per elencare i tuoi canali privati e iniziare a caricare i backup."
          />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Stato</th>
                  <th>Parte massima</th>
                  <th className="right">Job insieme</th>
                  <th className="right">Canali</th>
                  <th>Collegato il</th>
                  <th className="right">Azioni</th>
                </tr>
              </thead>
              <tbody>
                {data.map((account) => (
                  <tr
                    key={account.id}
                    className="clickable"
                    onClick={() => setExpanded(expanded === account.id ? null : account.id)}
                  >
                    <td>
                      <div style={{ fontWeight: 600 }}>{account.label}</div>
                      <div style={{ color: "var(--muted)", fontSize: 12 }}>
                        {account.first_name ?? account.phone}
                        {account.username ? ` (${account.username})` : ""}
                      </div>
                    </td>
                    <td>
                      {account.connected ? (
                        <Pill tone="ok">Connesso</Pill>
                      ) : account.status === "error" ? (
                        <Pill tone="bad">Errore</Pill>
                      ) : (
                        <Pill tone="mute">Non connesso</Pill>
                      )}
                    </td>
                    <td className="num">
                      <span className="row" style={{ gap: 6 }}>
                        {account.is_premium ? <Crown size={13} color="var(--warning)" /> : null}
                        {formatBytes(account.default_part_size)}
                      </span>
                    </td>
                    <td className="right num">{account.max_concurrent_jobs}</td>
                    <td className="right num">{account.channels_count}</td>
                    <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(account.created_at)}</td>
                    <td className="right">
                      <button
                        type="button"
                        className="btn ghost small"
                        style={{ marginRight: 8 }}
                        onClick={(event) => {
                          event.stopPropagation();
                          setEditing(account);
                        }}
                      >
                        <Settings2 size={13} />
                        Impostazioni
                      </button>
                      <button
                        type="button"
                        className="btn danger small"
                        onClick={(event) => {
                          event.stopPropagation();
                          if (
                            window.confirm(
                              `Disconnettere l'account ${account.label}? La sessione salvata viene eliminata.`,
                            )
                          ) {
                            remove.mutate(account.id);
                          }
                        }}
                      >
                        <Trash2 size={13} />
                        Disconnetti
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {data?.some((account) => account.last_error) ? (
          <div className="card-body">
            {data
              .filter((account) => account.last_error)
              .map((account) => (
                <Alert key={account.id}>
                  {account.label}: {account.last_error}
                </Alert>
              ))}
          </div>
        ) : null}
      </Card>

      {expanded !== null ? (
        <Card>
          <CardHead title={`Canali di ${data?.find((a) => a.id === expanded)?.label ?? ""}`} />
          <ChannelList accountId={expanded} />
        </Card>
      ) : null}

      {linking ? <LinkAccountModal onClose={() => setLinking(false)} /> : null}
      {editing ? (
        <AccountSettingsModal account={editing} onClose={() => setEditing(null)} />
      ) : null}
    </>
  );
}
