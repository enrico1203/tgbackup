import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BellRing,
  CalendarClock,
  CheckCircle2,
  FileText,
  HardDrive,
  PencilLine,
  Trash2,
} from "lucide-react";

import { api } from "../lib/api";
import { formatDateTime } from "../lib/format";
import type {
  Account,
  NotifyPreferences,
  RcloneStatus,
  SchedulePreferences,
} from "../lib/types";
import RemoteBrowser from "../components/RemoteBrowser";
import { Alert, Card, CardHead, Field, Pill, Spinner } from "../components/ui";

const PLACEHOLDER = `[mycloud]
type = drive
client_id = ...
token = {"access_token":"..."}

[mycloud-crypt]
type = crypt
remote = mycloud:folder
password = ...`;

function Notifications() {
  const queryClient = useQueryClient();
  const [events, setEvents] = useState<NotifyPreferences["events"] | null>(null);
  const [accountId, setAccountId] = useState<number | null>(null);

  const { data } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api.get<NotifyPreferences>("/api/preferences/notifications"),
  });
  const { data: accounts } = useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.get<Account[]>("/api/accounts"),
  });

  const save = useMutation({
    mutationFn: (payload: NotifyPreferences) =>
      api.put<NotifyPreferences>("/api/preferences/notifications", payload),
    onSuccess: (result) => {
      queryClient.setQueryData(["notifications"], result);
      setEvents(null);
      setAccountId(null);
    },
  });

  const currentEvents = events ?? data?.events ?? "off";
  const currentAccount = accountId ?? data?.account_id ?? 0;
  const dirty =
    data !== undefined && (currentEvents !== data.events || currentAccount !== data.account_id);

  return (
    <Card>
      <CardHead title="Notifications">
        {currentEvents === "off" ? (
          <Pill tone="mute">Off</Pill>
        ) : (
          <Pill tone="ok">
            <BellRing size={11} />
            {currentEvents === "errors" ? "Errors only" : "Every run"}
          </Pill>
        )}
      </CardHead>

      <div className="card-body">
        <p style={{ margin: 0, color: "var(--muted)", maxWidth: "70ch" }}>
          A report at the end of a run, sent to the Saved Messages of a Telegram account.
          No webhook and no second service: it arrives where the backup already lives.
        </p>

        {save.isError ? <Alert>{(save.error as Error).message}</Alert> : null}

        <div className="grid-2">
          <Field label="When to send" hint="Errors also covers a run that left files behind.">
            <select
              value={currentEvents}
              onChange={(event) => setEvents(event.target.value as NotifyPreferences["events"])}
            >
              <option value="off">Never</option>
              <option value="errors">Only on errors</option>
              <option value="all">At the end of every run</option>
            </select>
          </Field>

          <Field
            label="Account that sends them"
            hint="With no choice, each job reports through its own account."
          >
            <select
              value={currentAccount}
              disabled={currentEvents === "off"}
              onChange={(event) => setAccountId(Number(event.target.value))}
            >
              <option value={0}>The account of the job</option>
              {(accounts ?? []).map((account) => (
                <option key={account.id} value={account.id}>
                  {account.label}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <div className="row">
          <button
            type="button"
            className="btn"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate({ events: currentEvents, account_id: currentAccount })}
          >
            {save.isPending ? <Spinner /> : null}
            Save
          </button>
        </div>
      </div>
    </Card>
  );
}

function Timezone() {
  const queryClient = useQueryClient();
  const [zone, setZone] = useState<string | null>(null);

  const { data } = useQuery({
    queryKey: ["schedule-preferences"],
    queryFn: () => api.get<SchedulePreferences>("/api/preferences/schedule"),
  });

  const save = useMutation({
    mutationFn: (payload: SchedulePreferences) =>
      api.put<SchedulePreferences>("/api/preferences/schedule", payload),
    onSuccess: (result) => {
      queryClient.setQueryData(["schedule-preferences"], result);
      // Every job shows its window in this zone: the two lists are now stale.
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      void queryClient.invalidateQueries({ queryKey: ["downloads"] });
      setZone(null);
    },
  });

  // The list the browser itself knows about, so no table has to be kept up to date here.
  // Older engines without it fall back to the two names that are always resolvable.
  const zones =
    typeof Intl.supportedValuesOf === "function"
      ? Intl.supportedValuesOf("timeZone")
      : ["UTC", Intl.DateTimeFormat().resolvedOptions().timeZone];
  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;

  const current = zone ?? data?.timezone ?? "UTC";
  const dirty = data !== undefined && current !== data.timezone;

  return (
    <Card>
      <CardHead title="Schedule timezone">
        <Pill tone="mute">
          <CalendarClock size={11} />
          {current}
        </Pill>
      </CardHead>

      <div className="card-body">
        <p style={{ margin: 0, color: "var(--muted)", maxWidth: "70ch" }}>
          The hours a job is allowed to run in are read in this zone, and so is the change
          of season: a window set on the night keeps running at night. Everything else,
          run times included, is stored in UTC.
        </p>

        {save.isError ? <Alert>{(save.error as Error).message}</Alert> : null}

        <div className="grid-2">
          <Field
            label="Timezone"
            hint={`This browser is on ${browserZone}.`}
          >
            <select value={current} onChange={(event) => setZone(event.target.value)}>
              {(zones.includes(current) ? zones : [current, ...zones]).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <div className="row">
          <button
            type="button"
            className="btn"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate({ timezone: current })}
          >
            {save.isPending ? <Spinner /> : null}
            Save
          </button>
          {data && data.timezone !== browserZone ? (
            <button
              type="button"
              className="btn ghost"
              onClick={() => setZone(browserZone)}
            >
              Use this browser's zone
            </button>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

export default function Settings() {
  const queryClient = useQueryClient();
  const [content, setContent] = useState("");
  const [editing, setEditing] = useState(false);
  const [browsing, setBrowsing] = useState<string | null>(null);
  // Tells "I am fixing the existing one" apart from "I am writing a new one": it only
  // changes the button text, but it avoids suggesting an edit when everything is about to
  // be replaced.
  const [mode, setMode] = useState<"edit" | "replace">("edit");

  const { data, isLoading } = useQuery({
    queryKey: ["rclone"],
    queryFn: () => api.get<RcloneStatus>("/api/rclone"),
  });

  const save = useMutation({
    mutationFn: () => api.put<RcloneStatus>("/api/rclone", { content }),
    onSuccess: (status) => {
      queryClient.setQueryData(["rclone"], status);
      // The remotes may have changed: the job form reloads them.
      void queryClient.invalidateQueries({ queryKey: ["rclone"] });
      setContent("");
      setEditing(false);
    },
  });

  const remove = useMutation({
    mutationFn: () => api.del("/api/rclone"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["rclone"] }),
  });

  // The configuration in clear is only requested here, not on page load.
  const load = useMutation({
    mutationFn: () => api.get<{ content: string }>("/api/rclone/content"),
    onSuccess: (result) => {
      setContent(result.content);
      setMode("edit");
      setEditing(true);
    },
  });

  const startFresh = () => {
    setContent("");
    setMode("replace");
    setEditing(true);
  };

  const cancel = () => {
    setEditing(false);
    setContent("");
  };

  return (
    <>
      <Card>
        <CardHead title="Rclone remotes">
          {data?.configured ? (
            <Pill tone="ok">
              <CheckCircle2 size={11} />
              {data.remotes.length} remote
            </Pill>
          ) : (
            <Pill tone="mute">Not configured</Pill>
          )}
        </CardHead>

        <div className="card-body">
          <p style={{ margin: 0, color: "var(--muted)", maxWidth: "70ch" }}>
            Pasting your <span className="mono">rclone.conf</span> here lets you use a remote
            as the source of a sync job, without mounting it. Files are listed through the API
            and read in byte ranges: no mount, no copies on disk.
          </p>

          {isLoading ? (
            <div className="row" style={{ color: "var(--muted)" }}>
              <Spinner /> Loading
            </div>
          ) : (
            <>
              <div className="row wrap" style={{ gap: 24, fontSize: 12.5 }}>
                <span style={{ color: "var(--muted)" }}>
                  rclone <span className="mono">{data?.version}</span>
                </span>
                {data?.configured ? (
                  <>
                    <span style={{ color: "var(--muted)" }} className="num">
                      {data.config_lines} configuration lines
                    </span>
                    <span style={{ color: "var(--muted)" }}>
                      updated {formatDateTime(data.updated_at)}
                    </span>
                  </>
                ) : null}
              </div>

              {data?.error ? <Alert>{data.error}</Alert> : null}
              {save.isError ? <Alert>{(save.error as Error).message}</Alert> : null}

              {data?.configured && data.remotes.length > 0 ? (
                <div>
                  <span className="section-label">
                    Available remotes, press one to see what it holds
                  </span>
                  <div className="row wrap" style={{ gap: 8, marginTop: 8 }}>
                    {data.remotes.map((remote) => (
                      <button
                        key={remote}
                        type="button"
                        className="pill mono remote-pill"
                        onClick={() => setBrowsing(remote)}
                      >
                        <HardDrive size={11} />
                        {remote}
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}

              {editing || !data?.configured ? (
                <>
                  <Field
                    label={
                      mode === "edit" && data?.configured
                        ? "Edit rclone.conf"
                        : "Contents of rclone.conf"
                    }
                    hint={
                      mode === "edit" && data?.configured
                        ? "Add or fix the sections you need, the rest stays as it is. On save rclone reloads everything."
                        : "It is stored encrypted in the database."
                    }
                  >
                    <textarea
                      className="mono config-box"
                      value={content}
                      placeholder={PLACEHOLDER}
                      spellCheck={false}
                      onChange={(event) => setContent(event.target.value)}
                    />
                  </Field>

                  <Alert tone="info">
                    This file holds your cloud credentials. Encrypted at rest, but anyone who
                    reaches this interface will be able to use those remotes.
                  </Alert>

                  <div className="row">
                    <button
                      type="button"
                      className="btn"
                      disabled={!content.trim() || save.isPending}
                      onClick={() => save.mutate()}
                    >
                      {save.isPending ? <Spinner /> : null}
                      Save and verify
                    </button>
                    {data?.configured ? (
                      <button type="button" className="btn ghost" onClick={cancel}>
                        Cancel
                      </button>
                    ) : null}
                  </div>
                </>
              ) : (
                <>
                  {load.isError ? <Alert>{(load.error as Error).message}</Alert> : null}
                  <div className="row wrap">
                    <button
                      type="button"
                      className="btn"
                      disabled={load.isPending}
                      onClick={() => load.mutate()}
                    >
                      {load.isPending ? <Spinner /> : <PencilLine size={14} />}
                      Edit the configuration
                    </button>
                    <button type="button" className="btn ghost" onClick={startFresh}>
                      <FileText size={14} />
                      Rewrite from scratch
                    </button>
                    <button
                      type="button"
                      className="btn danger"
                      onClick={() => {
                        if (
                          window.confirm(
                            "Remove the rclone configuration? Jobs using a remote will stop working.",
                          )
                        ) {
                          remove.mutate();
                        }
                      }}
                    >
                      <Trash2 size={14} />
                      Remove
                    </button>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </Card>

      <Notifications />
      <Timezone />

      {browsing ? (
        <RemoteBrowser remote={browsing} onClose={() => setBrowsing(null)} />
      ) : null}
    </>
  );
}
