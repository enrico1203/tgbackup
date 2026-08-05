import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import {
  CalendarClock,
  CloudDownload,
  FolderOpen,
  FolderSearch,
  HardDrive,
  Pencil,
  Play,
  Plus,
  Square,
  Trash2,
} from "lucide-react";

import { api } from "../lib/api";
import { formatBytes, formatDateTime, formatInterval } from "../lib/format";
import { useProgress } from "../lib/progress";
import ScheduleGrid, { ALWAYS, describeSchedule } from "../components/ScheduleGrid";

const MEGA = 1_000_000;
import DownloadActivity, { downloadPhaseLabel } from "../components/DownloadActivity";
import RemoteBrowser from "../components/RemoteBrowser";
import type { Account, Channel, DownloadJob, RcloneStatus } from "../lib/types";
import {
  Alert,
  Card,
  CardHead,
  Empty,
  Field,
  Modal,
  Pill,
  ProgressBar,
  Spinner,
} from "../components/ui";

function DownloadForm({ job, onClose }: { job: DownloadJob | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const { data: accounts } = useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.get<Account[]>("/api/accounts"),
  });

  const [name, setName] = useState(job?.name ?? "");
  const [accountId, setAccountId] = useState<number | null>(job?.account_id ?? null);
  const [channelId, setChannelId] = useState<number | null>(job?.channel_id ?? null);
  const [destType, setDestType] = useState<"local" | "rclone">(job?.dest_type ?? "local");
  const [localPath, setLocalPath] = useState(job?.local_path ?? "");
  const [remote, setRemote] = useState(job?.remote ?? "");
  const [browsing, setBrowsing] = useState<string | null>(null);
  const [intervalHours, setIntervalHours] = useState(String(job?.interval_hours ?? 24));
  const [scheduleHours, setScheduleHours] = useState(job?.schedule_hours ?? ALWAYS);
  const [stopOutsideWindow, setStopOutsideWindow] = useState(job?.stop_outside_window ?? false);
  const [throttle, setThrottle] = useState(
    job?.throttle_bps ? String(job.throttle_bps / MEGA) : "",
  );
  const [silenceAlerts, setSilenceAlerts] = useState(job?.silence_alerts ?? true);
  const [enabled, setEnabled] = useState(job?.enabled ?? true);

  const { data: channels } = useQuery({
    queryKey: ["channels", accountId],
    queryFn: () => api.get<Channel[]>(`/api/accounts/${accountId}/channels`),
    enabled: accountId !== null,
  });

  const { data: rcloneStatus } = useQuery({
    queryKey: ["rclone"],
    queryFn: () => api.get<RcloneStatus>("/api/rclone"),
  });

  const save = useMutation({
    mutationFn: async () => {
      const payload = {
        name: name.trim(),
        account_id: accountId,
        channel_id: channelId,
        dest_type: destType,
        local_path: destType === "local" ? localPath.trim() : "",
        remote: destType === "rclone" ? remote.trim() : null,
        interval_hours: Number(intervalHours),
        schedule_hours: scheduleHours,
        stop_outside_window: stopOutsideWindow,
        throttle_bps: throttle ? Math.round(Number(throttle) * MEGA) : 0,
        silence_alerts: silenceAlerts,
        enabled,
      };
      if (job) {
        const { account_id: _ignored, ...rest } = payload;
        return api.patch<DownloadJob>(`/api/downloads/${job.id}`, rest);
      }
      return api.post<DownloadJob>("/api/downloads", payload);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["downloads"] });
      onClose();
    },
    onError: (exc) => setError(exc instanceof Error ? exc.message : "Saving failed"),
  });

  const destinationReady =
    destType === "local" ? Boolean(localPath.trim()) : Boolean(remote.trim());
  const valid =
    Boolean(name.trim()) &&
    accountId !== null &&
    channelId !== null &&
    destinationReady &&
    Number(intervalHours) > 0;

  return (
    <>
      <Modal
        title={job ? `Edit ${job.name}` : "New download job"}
        onClose={onClose}
        footer={
          <>
            <button type="button" className="btn ghost" onClick={onClose}>
              Cancel
            </button>
            <button
              type="button"
              className="btn"
              disabled={!valid || save.isPending}
              onClick={() => save.mutate()}
            >
              {save.isPending ? <Spinner /> : null}
              {job ? "Save" : "Create the job"}
            </button>
          </>
        }
      >
        {error ? <Alert>{error}</Alert> : null}

        <Alert tone="info">
          A download job writes the files a channel holds to the destination and never
          deletes anything from it. What it can write is what the index knows: the files
          uploaded by a sync job of this instance, or those of an index imported from
          another one.
        </Alert>

        <Field label="Job name">
          <input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>

        <div className="grid-2">
          <Field label="Telegram account">
            <select
              value={accountId ?? ""}
              disabled={Boolean(job)}
              onChange={(e) => {
                setAccountId(Number(e.target.value));
                setChannelId(null);
              }}
            >
              <option value="" disabled>
                Pick an account
              </option>
              {(accounts ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Channel to download">
            <select
              value={channelId ?? ""}
              disabled={accountId === null}
              onChange={(e) => setChannelId(Number(e.target.value))}
            >
              <option value="" disabled>
                {accountId === null ? "Pick an account first" : "Pick a channel"}
              </option>
              {(channels ?? []).map((channel) => (
                <option key={channel.id} value={channel.id}>
                  {channel.title}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <Field label="Destination">
          <div className="row" style={{ gap: 8 }}>
            <button
              type="button"
              className={destType === "local" ? "btn small" : "btn ghost small"}
              onClick={() => setDestType("local")}
            >
              <FolderOpen size={13} />
              Local folder
            </button>
            <button
              type="button"
              className={destType === "rclone" ? "btn small" : "btn ghost small"}
              onClick={() => setDestType("rclone")}
              disabled={!rcloneStatus?.configured}
              title={
                rcloneStatus?.configured
                  ? undefined
                  : "Paste your rclone.conf in the Settings page first"
              }
            >
              <HardDrive size={13} />
              Rclone remote
            </button>
          </div>
        </Field>

        {destType === "local" ? (
          <Field
            label="Local folder"
            hint="Path inside the container. It has to be a writable volume: the folders to back up are mounted read-only and cannot be written to."
          >
            <input
              value={localPath}
              onChange={(e) => setLocalPath(e.target.value)}
              placeholder="/mnt/restore"
              className="mono"
            />
          </Field>
        ) : (
          <Field
            label="Rclone remote"
            hint="Remote name with the colon, optionally followed by a subfolder. Written through the API, no mount and nothing staged on disk."
          >
            <div className="row" style={{ gap: 8 }}>
              <select
                style={{ width: 180 }}
                value={(rcloneStatus?.remotes ?? []).find((r) => remote.startsWith(r)) ?? ""}
                onChange={(e) => setRemote(e.target.value)}
              >
                <option value="" disabled>
                  Pick a remote
                </option>
                {(rcloneStatus?.remotes ?? []).map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
              <input
                value={remote}
                onChange={(e) => setRemote(e.target.value)}
                placeholder="miocloud-crypt:Restored"
                className="mono"
              />
              <button
                type="button"
                className="btn ghost small"
                disabled={!remote.includes(":")}
                onClick={() => setBrowsing(remote)}
                title="Browse the contents and pick a subfolder"
              >
                <FolderSearch size={13} />
                Browse
              </button>
            </div>
          </Field>
        )}

        <Field
          label="Every how many hours"
          hint="The wait starts from the end of the previous run. A run with nothing missing costs one scan of the destination."
        >
          <input
            value={intervalHours}
            onChange={(e) => setIntervalHours(e.target.value.replace(/[^\d.]/g, ""))}
            inputMode="decimal"
          />
        </Field>

        <Field
          label="Hours the job may run in"
          hint="Drag to paint, click a day or an hour to toggle the whole line. A run that becomes due outside these hours waits for the next opening. Times are read in the timezone set in Settings."
        >
          <ScheduleGrid value={scheduleHours} onChange={setScheduleHours} />
        </Field>

        {scheduleHours.includes("0") ? (
          <label className="switch">
            <input
              type="checkbox"
              checked={stopOutsideWindow}
              onChange={(e) => setStopOutsideWindow(e.target.checked)}
            />
            <span>Stop a run in progress when the window closes</span>
          </label>
        ) : null}

        {scheduleHours.includes("1") || scheduleHours.includes("2") ? null : (
          <Alert tone="info">
            Every hour is closed: the job will only ever run when you press Run now.
          </Alert>
        )}

        <Field
          label="Speed limit (MB/s)"
          hint={
            scheduleHours.includes("2")
              ? "Applied in the hours painted as limited. The rest of the week the job goes as fast as the line allows."
              : "Applied at every hour, since no hour is painted as limited. Empty or zero means no limit."
          }
        >
          <input
            value={throttle}
            onChange={(e) => setThrottle(e.target.value.replace(/[^\d.]/g, ""))}
            inputMode="decimal"
            placeholder="0"
          />
        </Field>

        <label className="switch">
          <input
            type="checkbox"
            checked={silenceAlerts}
            onChange={(e) => setSilenceAlerts(e.target.checked)}
          />
          <span>Warn me if this job stops finishing runs</span>
        </label>

        <label className="switch">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          <span>Run automatically on the interval</span>
        </label>
      </Modal>

      {browsing ? (
        <RemoteBrowser
          remote={browsing}
          onClose={() => setBrowsing(null)}
          onPick={(path) => {
            setRemote(path);
            setBrowsing(null);
          }}
        />
      ) : null}
    </>
  );
}

function DownloadCard({
  job,
  onEdit,
}: {
  job: DownloadJob;
  onEdit: (job: DownloadJob) => void;
}) {
  const queryClient = useQueryClient();
  const progress = useProgress().downloads.get(job.id);
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["downloads"] });

  const start = useMutation({
    mutationFn: () => api.post(`/api/downloads/${job.id}/run`),
    onSuccess: invalidate,
  });
  const stop = useMutation({
    mutationFn: () => api.post(`/api/downloads/${job.id}/stop`),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/api/downloads/${job.id}`),
    onSuccess: invalidate,
  });

  const running = job.status === "running";
  const failed = job.status === "error";

  return (
    <Card>
      <CardHead title={job.name}>
        <div className="row" style={{ gap: 8 }}>
          {running ? (
            <Pill
              tone={(progress?.phase ?? job.phase) === "waiting" ? "mute" : "ok"}
              live={(progress?.phase ?? job.phase) !== "waiting"}
            >
              {downloadPhaseLabel(progress?.phase ?? job.phase ?? "")}
            </Pill>
          ) : failed ? (
            <Pill tone="bad">Error</Pill>
          ) : job.enabled ? (
            <Pill tone="mute">Idle</Pill>
          ) : (
            <Pill tone="warn">Disabled</Pill>
          )}

          {running ? (
            <button type="button" className="btn ghost small" onClick={() => stop.mutate()}>
              <Square size={13} />
              Stop
            </button>
          ) : (
            <button type="button" className="btn small" onClick={() => start.mutate()}>
              <Play size={13} />
              Run now
            </button>
          )}

          <button
            type="button"
            className="btn ghost small"
            onClick={() => onEdit(job)}
            disabled={running}
          >
            <Pencil size={13} />
            Edit
          </button>

          <button
            type="button"
            className="btn danger small"
            disabled={running}
            onClick={() => {
              if (
                window.confirm(
                  `Delete job ${job.name}? The files already downloaded stay where they are.`,
                )
              ) {
                remove.mutate();
              }
            }}
          >
            <Trash2 size={13} />
          </button>
        </div>
      </CardHead>

      <div className="card-body">
        {job.last_error ? <Alert>{job.last_error}</Alert> : null}
        {start.isError ? <Alert>{(start.error as Error).message}</Alert> : null}
        {remove.isError ? <Alert>{(remove.error as Error).message}</Alert> : null}

        <div className="row wrap" style={{ gap: 24, fontSize: 12.5, color: "var(--muted)" }}>
          <span>from {job.channel_title}</span>
          <span className="row" style={{ gap: 6 }}>
            {job.dest_type === "rclone" ? <HardDrive size={12} /> : <FolderOpen size={12} />}
            <span className="mono">
              {job.dest_type === "rclone" ? job.remote : job.local_path}
            </span>
          </span>
          <span>account {job.account_label}</span>
          <span>every {formatInterval(job.interval_hours)}</span>
          {job.schedule_hours.includes("0") ? (
            <span className="row" style={{ gap: 6 }}>
              <CalendarClock size={12} />
              {describeSchedule(job.schedule_hours)}
            </span>
          ) : null}
        </div>

        {running && progress ? (
          <DownloadActivity progress={progress} />
        ) : (
          <>
            <ProgressBar
              done={job.stats.bytes_at_destination}
              total={job.stats.bytes_indexed}
            />
            <div className="row wrap num" style={{ gap: 20, fontSize: 12.5, color: "var(--muted)" }}>
              <span>
                {job.stats.files_at_destination.toLocaleString("en-US")} of{" "}
                {job.stats.files_indexed.toLocaleString("en-US")} files at the destination
              </span>
              <span>{formatBytes(job.stats.bytes_at_destination)}</span>
              {job.stats.files_failed > 0 ? (
                <span style={{ color: "var(--danger)" }}>
                  {job.stats.files_failed.toLocaleString("en-US")} failed on the last run
                </span>
              ) : null}
              {job.stats.last_run_at === null ? <span>never run</span> : null}
            </div>
          </>
        )}

        <div className="row wrap" style={{ gap: 24, fontSize: 12, color: "var(--muted)" }}>
          <span>last run {formatDateTime(job.last_finished_at)}</span>
          <span>
            next{" "}
            {!job.enabled
              ? "disabled"
              : job.window_open
                ? formatDateTime(job.next_run_at)
                : job.next_window_at
                  ? `window opens ${formatDateTime(job.next_window_at)}`
                  : "never, the window is closed at every hour"}
          </span>
          <Link to={`/files?channel=${job.channel_id}`}>See the files</Link>
        </div>
      </div>
    </Card>
  );
}

export default function Downloads() {
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<DownloadJob | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["downloads"],
    queryFn: () => api.get<DownloadJob[]>("/api/downloads"),
    refetchInterval: 8000,
  });

  return (
    <>
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <button type="button" className="btn" onClick={() => setCreating(true)}>
          <Plus size={15} />
          New download job
        </button>
      </div>

      {isLoading ? (
        <Card>
          <div className="card-body row" style={{ color: "var(--muted)" }}>
            <Spinner /> Loading
          </div>
        </Card>
      ) : !data || data.length === 0 ? (
        <Card>
          <Empty
            icon={<CloudDownload size={26} color="var(--muted)" />}
            title="No download jobs"
            hint="A download job is a sync job the other way round: it takes what a channel holds and writes it to a folder or an rclone remote, skipping what is already there and deleting nothing."
          />
        </Card>
      ) : (
        data.map((job) => <DownloadCard key={job.id} job={job} onEdit={setEditing} />)
      )}

      {creating ? <DownloadForm job={null} onClose={() => setCreating(false)} /> : null}
      {editing ? <DownloadForm job={editing} onClose={() => setEditing(null)} /> : null}
    </>
  );
}
