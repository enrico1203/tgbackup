import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot as BotIcon,
  CheckCircle2,
  Hash,
  Plus,
  Power,
  Settings2,
  ShieldAlert,
  Trash2,
  XCircle,
} from "lucide-react";

import { api } from "../lib/api";
import { formatBytes, formatDateTime } from "../lib/format";
import type { Account, BotSet, BotSetChannelCheck, Channel } from "../lib/types";
import { Alert, Card, CardHead, Empty, Field, Modal, Pill, Spinner } from "../components/ui";

// The protocol ceiling per data center. A bot is an account of its own, so every bot of a
// set has a budget of its own and the number here is per bot, not for the set.
const MAX_DC_CONNECTIONS = 20;

function CreateSetModal({
  accounts,
  onClose,
}: {
  accounts: Account[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [fromAccount, setFromAccount] = useState<number | null>(accounts[0]?.id ?? null);
  const [apiId, setApiId] = useState("");
  const [apiHash, setApiHash] = useState("");
  const [connections, setConnections] = useState("8");

  const create = useMutation({
    mutationFn: () =>
      api.post<BotSet>("/api/botsets", {
        name: name.trim(),
        from_account_id: fromAccount,
        api_id: fromAccount ? null : Number(apiId),
        api_hash: fromAccount ? null : apiHash.trim(),
        max_connections: Number(connections) || 8,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["botsets"] });
      onClose();
    },
  });

  const ready =
    Boolean(name.trim()) && (fromAccount !== null || (Boolean(apiId) && Boolean(apiHash)));

  return (
    <Modal
      title="New bot set"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn"
            disabled={!ready || create.isPending}
            onClick={() => create.mutate()}
          >
            {create.isPending ? <Spinner /> : null}
            Create
          </button>
        </>
      }
    >
      {create.isError ? <Alert>{(create.error as Error).message}</Alert> : null}

      <Alert tone="info">
        A bot signs in with its token, but opening the MTProto connection that carries the
        files still needs an api_id and an api_hash: they identify the application, not the
        user, so the ones of an account already linked work for every bot. The bots
        themselves are added afterwards, one token each.
      </Alert>

      <Field label="Set name" hint="Only for you, to recognise it when picking it on a job.">
        <input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      </Field>

      <Field
        label="Credentials"
        hint="Copied from the account you pick and stored encrypted. Nothing is done with that account."
      >
        <select
          value={fromAccount ?? ""}
          onChange={(e) => setFromAccount(e.target.value ? Number(e.target.value) : null)}
        >
          {accounts.map((account) => (
            <option key={account.id} value={account.id}>
              From {account.label} (api_id {account.api_id})
            </option>
          ))}
          <option value="">Type them by hand</option>
        </select>
      </Field>

      {fromAccount === null ? (
        <div className="grid-2">
          <Field label="api_id">
            <input
              value={apiId}
              inputMode="numeric"
              onChange={(e) => setApiId(e.target.value.replace(/\D/g, ""))}
            />
          </Field>
          <Field label="api_hash">
            <input value={apiHash} onChange={(e) => setApiHash(e.target.value)} />
          </Field>
        </div>
      ) : null}

      <Field
        label="Connections per bot"
        hint={`From 1 to ${MAX_DC_CONNECTIONS}. Every bot has a budget of its own, so this multiplies
               by the bots working: five bots at eight are forty connections on your line.`}
      >
        <input
          value={connections}
          inputMode="numeric"
          onChange={(e) => setConnections(e.target.value.replace(/\D/g, ""))}
        />
      </Field>
    </Modal>
  );
}

function SetSettingsModal({ set, onClose }: { set: BotSet; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(set.name);
  const [connections, setConnections] = useState(String(set.max_connections));
  const [concurrency, setConcurrency] = useState(String(set.max_concurrent_jobs));

  const save = useMutation({
    mutationFn: () =>
      api.patch<BotSet>(`/api/botsets/${set.id}`, {
        name: name.trim(),
        max_connections: Number(connections),
        max_concurrent_jobs: Number(concurrency),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["botsets"] });
      onClose();
    },
  });

  const perBot = Number(connections) || 0;
  const valid = perBot >= 1 && perBot <= MAX_DC_CONNECTIONS && Number(concurrency) >= 1;

  return (
    <Modal
      title={`Settings for ${set.name}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn"
            disabled={!name.trim() || !valid || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? <Spinner /> : null}
            Save
          </button>
        </>
      }
    >
      {save.isError ? <Alert>{(save.error as Error).message}</Alert> : null}

      <Field label="Set name">
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </Field>

      <Field
        label="Connections per bot"
        hint={`From 1 to ${MAX_DC_CONNECTIONS}, per bot and not for the whole set.`}
      >
        <input
          value={connections}
          inputMode="numeric"
          onChange={(e) => setConnections(e.target.value.replace(/\D/g, ""))}
        />
      </Field>

      <Field
        label="Jobs allowed to upload at the same time"
        hint="The bots are shared between them, so two jobs together each get fewer files in flight."
      >
        <input
          value={concurrency}
          inputMode="numeric"
          onChange={(e) => setConcurrency(e.target.value.replace(/\D/g, ""))}
        />
      </Field>

      <Alert tone="info">
        With {set.bots.length} bots and {Math.max(1, perBot)} connections each, a job using
        the whole set opens up to {set.bots.length * Math.max(1, perBot)} connections and
        uploads {set.bots.length} files at once. Files are split at{" "}
        {formatBytes(set.default_part_size)}: a bot is never Premium, so 2 GB is its ceiling
        whatever the account beside it can do.
      </Alert>
    </Modal>
  );
}

function AddBotModal({ set, onClose }: { set: BotSet; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [token, setToken] = useState("");

  const add = useMutation({
    mutationFn: () => api.post(`/api/botsets/${set.id}/bots`, { token: token.trim() }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["botsets"] });
      onClose();
    },
  });

  return (
    <Modal
      title={`Add a bot to ${set.name}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn"
            disabled={token.trim().length < 20 || add.isPending}
            onClick={() => add.mutate()}
          >
            {add.isPending ? <Spinner /> : null}
            Add and connect
          </button>
        </>
      }
    >
      {add.isError ? <Alert>{(add.error as Error).message}</Alert> : null}

      <Alert tone="info">
        Create the bot with @BotFather, then add it to the destination channel as an
        administrator with permission to post and to delete messages. Posting is what
        uploads; deleting is what removes from the channel the files that disappear from
        the source, and without it they stay there for ever.
      </Alert>

      <Field label="Bot token" hint="From BotFather, in the form 123456789:AAE... Stored encrypted.">
        <input value={token} onChange={(e) => setToken(e.target.value)} autoFocus />
      </Field>
    </Modal>
  );
}

function AddChannelModal({ set, onClose }: { set: BotSet; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [identifier, setIdentifier] = useState("");

  const add = useMutation({
    mutationFn: () =>
      api.post<Channel>(`/api/botsets/${set.id}/channels`, { identifier: identifier.trim() }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["botset-channels", set.id] });
      onClose();
    },
  });

  return (
    <Modal
      title="Add a channel to this set"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn"
            disabled={!identifier.trim() || add.isPending}
            onClick={() => add.mutate()}
          >
            {add.isPending ? <Spinner /> : null}
            Add
          </button>
        </>
      }
    >
      {add.isError ? <Alert>{(add.error as Error).message}</Alert> : null}

      <Alert tone="info">
        A bot has no list of chats to pick from, so the channel is named: paste its id, in
        either form, or its public username. A channel already known here, whichever account
        found it, is reused rather than added twice.
      </Alert>

      <Field label="Channel id or username" hint="For example -1001234567890 or @mychannel.">
        <input
          value={identifier}
          onChange={(e) => setIdentifier(e.target.value)}
          autoFocus
        />
      </Field>
    </Modal>
  );
}

function ChannelRights({ set }: { set: BotSet }) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [checking, setChecking] = useState<number | null>(null);

  const { data: channels, isLoading } = useQuery({
    queryKey: ["botset-channels", set.id],
    queryFn: () => api.get<Channel[]>(`/api/botsets/${set.id}/channels`),
  });

  const { data: check, isFetching } = useQuery({
    queryKey: ["botset-check", set.id, checking],
    queryFn: () =>
      api.get<BotSetChannelCheck>(`/api/botsets/${set.id}/channels/${checking}/check`),
    enabled: checking !== null,
  });

  return (
    <div className="card-body">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="section-label">Channels this set can be pointed at</span>
        <button type="button" className="btn ghost small" onClick={() => setAdding(true)}>
          <Plus size={13} />
          Add a channel
        </button>
      </div>

      {isLoading ? (
        <div className="row" style={{ color: "var(--muted)" }}>
          <Spinner /> Loading
        </div>
      ) : !channels || channels.length === 0 ? (
        <Empty
          icon={<Hash size={24} color="var(--muted)" />}
          title="No channel known yet"
          hint="Add the channel your bots are in, by id or by username."
        />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Channel</th>
                <th>Type</th>
                <th className="right">Bots</th>
              </tr>
            </thead>
            <tbody>
              {channels.map((channel) => (
                <tr key={channel.id}>
                  <td>{channel.title}</td>
                  <td>
                    <Pill tone="mute">{channel.is_private ? "Private" : "Public"}</Pill>
                  </td>
                  <td className="right">
                    <button
                      type="button"
                      className="btn ghost small"
                      onClick={() => {
                        setChecking(channel.id);
                        void queryClient.invalidateQueries({
                          queryKey: ["botset-check", set.id, channel.id],
                        });
                      }}
                    >
                      Check the bots
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {checking !== null ? (
        <div style={{ marginTop: 14 }}>
          <span className="section-label">
            {check ? `In ${check.channel_title}` : "Checking"}
          </span>
          {isFetching ? (
            <div className="row" style={{ color: "var(--muted)" }}>
              <Spinner /> Asking every bot
            </div>
          ) : (
            (check?.bots ?? []).map((status) => (
              <div key={status.bot_id} className="row wrap" style={{ gap: 10, padding: "4px 0" }}>
                {status.member && status.can_delete ? (
                  <CheckCircle2 size={14} color="var(--success)" />
                ) : status.member ? (
                  <ShieldAlert size={14} color="var(--warning)" />
                ) : (
                  <XCircle size={14} color="var(--danger)" />
                )}
                <strong>{status.label}</strong>
                <span style={{ color: "var(--muted)", fontSize: 12.5 }}>
                  {!status.member
                    ? (status.error ?? "not in the channel")
                    : status.can_delete
                      ? "member, administrator, can delete"
                      : status.admin
                        ? "administrator, but cannot delete messages"
                        : "member without administrator rights: it cannot delete"}
                </span>
              </div>
            ))
          )}
        </div>
      ) : null}

      {adding ? <AddChannelModal set={set} onClose={() => setAdding(false)} /> : null}
    </div>
  );
}

export default function TelegramBots() {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<BotSet | null>(null);
  const [addingTo, setAddingTo] = useState<BotSet | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["botsets"],
    queryFn: () => api.get<BotSet[]>("/api/botsets"),
    refetchInterval: 15000,
  });
  const { data: accounts } = useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.get<Account[]>("/api/accounts"),
  });

  const removeSet = useMutation({
    mutationFn: (id: number) => api.del(`/api/botsets/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["botsets"] }),
  });
  const removeBot = useMutation({
    mutationFn: (input: { setId: number; botId: number }) =>
      api.del(`/api/botsets/${input.setId}/bots/${input.botId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["botsets"] }),
  });
  const toggleBot = useMutation({
    mutationFn: (input: { setId: number; botId: number; enabled: boolean }) =>
      api.patch(`/api/botsets/${input.setId}/bots/${input.botId}`, {
        enabled: input.enabled,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["botsets"] }),
  });

  const failed = removeSet.isError || removeBot.isError || toggleBot.isError;

  return (
    <>
      {failed ? (
        <Alert>
          {
            ((removeSet.error ?? removeBot.error ?? toggleBot.error) as Error | null)
              ?.message
          }
        </Alert>
      ) : null}

      <Card>
        <CardHead title="Bot sets">
          <button
            type="button"
            className="btn small"
            onClick={() => setCreating(true)}
            disabled={!accounts}
          >
            <Plus size={14} />
            New set
          </button>
        </CardHead>

        <div className="card-body">
          <Alert tone="info">
            A set is a group of bots that are administrators of the same channel. A sync job
            pointed at a set uploads one file per bot at the same time, because each bot is a
            Telegram account of its own with its own limits. Every bot has to be added to the
            channel by hand, as an administrator able to post and to delete messages, and
            files are split at 2 GB since no bot is Premium. Browsing, restoring and the
            channel check still need a user account in the channel: bots only upload.
          </Alert>
        </div>

        {isLoading ? (
          <div className="card-body row" style={{ color: "var(--muted)" }}>
            <Spinner /> Loading
          </div>
        ) : !data || data.length === 0 ? (
          <Empty
            icon={<BotIcon size={26} color="var(--muted)" />}
            title="No bot set yet"
            hint="Create a set, add the tokens of your bots, and point a sync job at it."
          />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Set</th>
                  <th className="right">Bots</th>
                  <th className="right">Ready</th>
                  <th className="right">Connections each</th>
                  <th>Max part</th>
                  <th className="right">Jobs</th>
                  <th>Created</th>
                  <th className="right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {data.map((set) => (
                  <tr
                    key={set.id}
                    className="clickable"
                    onClick={() => setExpanded(expanded === set.id ? null : set.id)}
                  >
                    <td style={{ fontWeight: 600 }}>{set.name}</td>
                    <td className="right num">{set.bots.length}</td>
                    <td className="right">
                      {set.bots_ready === 0 ? (
                        <Pill tone="bad">none</Pill>
                      ) : set.bots_ready < set.bots.length ? (
                        <Pill tone="warn">{set.bots_ready}</Pill>
                      ) : (
                        <Pill tone="ok">{set.bots_ready}</Pill>
                      )}
                    </td>
                    <td className="right num">{set.max_connections}</td>
                    <td className="num">{formatBytes(set.default_part_size)}</td>
                    <td className="right num">{set.jobs_count}</td>
                    <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(set.created_at)}</td>
                    <td className="right">
                      <button
                        type="button"
                        className="btn ghost small"
                        style={{ marginRight: 8 }}
                        onClick={(event) => {
                          event.stopPropagation();
                          setAddingTo(set);
                        }}
                      >
                        <Plus size={13} />
                        Bot
                      </button>
                      <button
                        type="button"
                        className="btn ghost small"
                        style={{ marginRight: 8 }}
                        onClick={(event) => {
                          event.stopPropagation();
                          setEditing(set);
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
                          if (window.confirm(`Delete the set ${set.name} and its tokens?`)) {
                            removeSet.mutate(set.id);
                          }
                        }}
                      >
                        <Trash2 size={13} />
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {expanded !== null && data?.some((set) => set.id === expanded) ? (
        <Card>
          <CardHead title={`Bots of ${data.find((set) => set.id === expanded)?.name ?? ""}`} />
          {(() => {
            const set = data.find((item) => item.id === expanded)!;
            return (
              <>
                {set.bots.length === 0 ? (
                  <Empty
                    icon={<BotIcon size={24} color="var(--muted)" />}
                    title="No bot in this set"
                    hint="Add one with the token BotFather gave you."
                  />
                ) : (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Bot</th>
                          <th>Status</th>
                          <th className="right">Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {set.bots.map((bot) => (
                          <tr key={bot.id}>
                            <td>
                              <div style={{ fontWeight: 600 }}>
                                {bot.username ? `@${bot.username}` : (bot.first_name ?? "bot")}
                              </div>
                              <div style={{ color: "var(--muted)", fontSize: 12 }}>
                                id {bot.tg_id ?? "unknown"}
                                {bot.last_error ? ` — ${bot.last_error}` : ""}
                              </div>
                            </td>
                            <td>
                              {!bot.enabled ? (
                                <Pill tone="mute">Disabled</Pill>
                              ) : bot.connected ? (
                                <Pill tone="ok">Connected</Pill>
                              ) : bot.status === "error" ? (
                                <Pill tone="bad">Error</Pill>
                              ) : (
                                <Pill tone="mute">Not connected</Pill>
                              )}
                            </td>
                            <td className="right">
                              <button
                                type="button"
                                className="btn ghost small"
                                style={{ marginRight: 8 }}
                                onClick={() =>
                                  toggleBot.mutate({
                                    setId: set.id,
                                    botId: bot.id,
                                    enabled: !bot.enabled,
                                  })
                                }
                              >
                                <Power size={13} />
                                {bot.enabled ? "Disable" : "Enable"}
                              </button>
                              <button
                                type="button"
                                className="btn danger small"
                                onClick={() => {
                                  if (window.confirm("Remove this bot from the set?")) {
                                    removeBot.mutate({ setId: set.id, botId: bot.id });
                                  }
                                }}
                              >
                                <Trash2 size={13} />
                                Remove
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <ChannelRights set={set} />
              </>
            );
          })()}
        </Card>
      ) : null}

      {creating ? (
        <CreateSetModal accounts={accounts ?? []} onClose={() => setCreating(false)} />
      ) : null}
      {editing ? <SetSettingsModal set={editing} onClose={() => setEditing(null)} /> : null}
      {addingTo ? <AddBotModal set={addingTo} onClose={() => setAddingTo(null)} /> : null}
    </>
  );
}
