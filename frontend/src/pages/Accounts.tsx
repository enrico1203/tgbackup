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
      setError(exc instanceof Error ? exc.message : "Operation failed");
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
      title="Link a Telegram account"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          {step === "details" ? (
            <button
              type="button"
              className="btn"
              disabled={busy || !label || !apiId || !apiHash || !phone}
              onClick={startLogin}
            >
              {busy ? <Spinner /> : null}
              Send the code
            </button>
          ) : step === "code" ? (
            <button type="button" className="btn" disabled={busy || code.length < 3} onClick={sendCode}>
              {busy ? <Spinner /> : null}
              Verify the code
            </button>
          ) : (
            <button type="button" className="btn" disabled={busy || !password} onClick={sendPassword}>
              {busy ? <Spinner /> : null}
              Confirm
            </button>
          )}
        </>
      }
    >
      <div className="steps">
        {["Credentials", "Code", "Two-step verification"].map((name, index) => (
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
            api_id and api_hash come from my.telegram.org, API development tools section.
            They are stored encrypted in the database.
          </Alert>
          <Field label="Account name" hint="Only for you, to recognise it in the list.">
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
          <Field label="Phone number" hint="With the international prefix, for example +44...">
            <input value={phone} onChange={(e) => setPhone(e.target.value)} />
          </Field>
        </>
      ) : step === "code" ? (
        <>
          <Alert tone="info">
            Telegram sent a code to the app linked to {phone}.
          </Alert>
          <Field label="Sign in code">
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
            The account has two-step verification enabled. Enter the Telegram cloud
            password.
          </Alert>
          <Field label="Two-step verification password">
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
          Private channels ({privateOnes.length} of {data?.length ?? 0} chats)
        </span>
        <button
          type="button"
          className="btn ghost small"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
        >
          {refresh.isPending ? <Spinner size={13} /> : <RefreshCcw size={13} />}
          Refresh
        </button>
      </div>

      {refresh.isError ? <Alert>{(refresh.error as Error).message}</Alert> : null}

      {isLoading ? (
        <div className="row" style={{ color: "var(--muted)" }}>
          <Spinner /> Loading the channels
        </div>
      ) : privateOnes.length === 0 ? (
        <Empty
          icon={<Hash size={24} color="var(--muted)" />}
          title="No private channels found"
          hint="Create a private channel on Telegram, then press Refresh."
        />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Channel</th>
                <th>Type</th>
                <th className="right">Members</th>
              </tr>
            </thead>
            <tbody>
              {privateOnes.map((channel) => (
                <tr key={channel.id}>
                  <td>{channel.title}</td>
                  <td>
                    <Pill tone="mute">
                      {channel.kind === "channel"
                        ? "Channel"
                        : channel.kind === "supergroup"
                          ? "Supergroup"
                          : "Group"}
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

// The protocol ceiling, and what a new account gets: the last connections before the
// ceiling are where Telegram starts cutting, and they buy the least. Kept in step with
// models.DEFAULT_MAX_CONNECTIONS on the backend, which is what actually decides.
const MAX_DC_CONNECTIONS = 20;
const DEFAULT_CONNECTIONS = 15;

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
  const [connections, setConnections] = useState(String(account.max_connections));

  const save = useMutation({
    mutationFn: () =>
      api.patch<Account>(`/api/accounts/${account.id}`, {
        label: label.trim(),
        max_concurrent_jobs: Number(concurrency),
        max_connections: Number(connections),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["accounts"] });
      onClose();
    },
  });

  const jobs = Number(concurrency) || 0;
  const budget = Number(connections) || 0;
  const valid =
    jobs >= 1 && jobs <= MAX_DC_CONNECTIONS && budget >= 1 && budget <= MAX_DC_CONNECTIONS;
  const perJob = Math.max(1, Math.floor(Math.max(1, budget) / Math.max(1, jobs)));

  return (
    <Modal
      title={`Settings for ${account.label}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn"
            disabled={!label.trim() || !valid || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? <Spinner /> : null}
            Save
          </button>
        </>
      }
    >
      {save.isError ? <Alert>{(save.error as Error).message}</Alert> : null}

      <Field label="Account name">
        <input value={label} onChange={(e) => setLabel(e.target.value)} />
      </Field>

      <Field
        label="Jobs allowed to upload at the same time"
        hint="From 1 to 20. Beyond this number jobs stay queued and their card shows Queued."
      >
        <input
          value={concurrency}
          inputMode="numeric"
          onChange={(e) => setConcurrency(e.target.value.replace(/\D/g, ""))}
        />
      </Field>

      <Field
        label="Connections to Telegram"
        hint={`From 1 to ${MAX_DC_CONNECTIONS}, ${DEFAULT_CONNECTIONS} by default. This is the budget of the
               account, not of one job: everything transferring on it divides this number.`}
      >
        <input
          value={connections}
          inputMode="numeric"
          onChange={(e) => setConnections(e.target.value.replace(/\D/g, ""))}
        />
      </Field>

      <Alert tone="info">
        Telegram blocks them all beyond {MAX_DC_CONNECTIONS} connections per data center, which is
        why {MAX_DC_CONNECTIONS} is the ceiling here. This account opens {Math.max(1, budget)}, and
        with {Math.max(1, jobs)} jobs together each gets {perJob}. Raising the number of jobs does
        not increase total bandwidth, it spreads the same bandwidth across more jobs; lowering the
        connections is what an account that Telegram keeps holding back answers with, and a run
        already going lowers them further on its own while it is being cut.
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
        <CardHead title="Linked accounts">
          <button type="button" className="btn small" onClick={() => setLinking(true)}>
            <Plus size={14} />
            Link account
          </button>
        </CardHead>

        {isLoading ? (
          <div className="card-body row" style={{ color: "var(--muted)" }}>
            <Spinner /> Loading
          </div>
        ) : !data || data.length === 0 ? (
          <Empty
            icon={<UserCircle2 size={26} color="var(--muted)" />}
            title="No Telegram account linked"
            hint="Link an account to list your private channels and start uploading backups."
          />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Status</th>
                  <th>Max part</th>
                  <th className="right">Jobs together</th>
                  <th className="right">Connections</th>
                  <th className="right">Channels</th>
                  <th>Linked on</th>
                  <th className="right">Actions</th>
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
                        <Pill tone="ok">Connected</Pill>
                      ) : account.status === "error" ? (
                        <Pill tone="bad">Error</Pill>
                      ) : (
                        <Pill tone="mute">Not connected</Pill>
                      )}
                    </td>
                    <td className="num">
                      <span className="row" style={{ gap: 6 }}>
                        {account.is_premium ? <Crown size={13} color="var(--warning)" /> : null}
                        {formatBytes(account.default_part_size)}
                      </span>
                    </td>
                    <td className="right num">{account.max_concurrent_jobs}</td>
                    <td className="right num">{account.max_connections}</td>
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
                        Settings
                      </button>
                      <button
                        type="button"
                        className="btn danger small"
                        onClick={(event) => {
                          event.stopPropagation();
                          if (
                            window.confirm(
                              `Disconnect account ${account.label}? The stored session is deleted.`,
                            )
                          ) {
                            remove.mutate(account.id);
                          }
                        }}
                      >
                        <Trash2 size={13} />
                        Disconnect
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
          <CardHead title={`Channels of ${data?.find((a) => a.id === expanded)?.label ?? ""}`} />
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
