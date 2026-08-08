import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import {
  BellOff,
  CalendarClock,
  CloudUpload,
  Filter,
  FolderOpen,
  FolderSearch,
  Gauge,
  HardDrive,
  Pencil,
  Play,
  Plus,
  ShieldAlert,
  Square,
  Trash2,
} from "lucide-react";

import { api } from "../lib/api";
import { formatBytes, formatDateTime, formatInterval } from "../lib/format";
import { useProgress } from "../lib/progress";
import { isHeld } from "../components/FloodNotice";
import JobActivity, { phaseLabel } from "../components/JobActivity";
import ScheduleGrid, { ALWAYS, describeSchedule } from "../components/ScheduleGrid";
import RemoteBrowser from "../components/RemoteBrowser";
import type { Account, Channel, Job, RcloneStatus } from "../lib/types";
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

const GIGA = 1_000_000_000;
const MEGA = 1_000_000;

function JobForm({ job, onClose }: { job: Job | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const { data: accounts } = useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.get<Account[]>("/api/accounts"),
  });

  const [name, setName] = useState(job?.name ?? "");
  const [accountId, setAccountId] = useState<number | null>(job?.account_id ?? null);
  const [channelId, setChannelId] = useState<number | null>(job?.channel_id ?? null);
  const [sourceType, setSourceType] = useState<"local" | "rclone">(job?.source_type ?? "local");
  const [localPath, setLocalPath] = useState(job?.local_path ?? "");
  const [remote, setRemote] = useState(job?.remote ?? "");
  const [browsing, setBrowsing] = useState<string | null>(null);
  const [intervalHours, setIntervalHours] = useState(String(job?.interval_hours ?? 24));
  const [scanRate, setScanRate] = useState(String(job?.scan_files_per_sec ?? 0));
  const [partSize, setPartSize] = useState(
    job ? String(job.part_size_bytes / GIGA) : "",
  );
  const [includeGlobs, setIncludeGlobs] = useState(job?.include_globs ?? "");
  const [excludeGlobs, setExcludeGlobs] = useState(job?.exclude_globs ?? "");
  const [maxFileSize, setMaxFileSize] = useState(
    job?.max_file_size ? String(job.max_file_size / GIGA) : "",
  );
  const [scheduleHours, setScheduleHours] = useState(job?.schedule_hours ?? ALWAYS);
  const [stopOutsideWindow, setStopOutsideWindow] = useState(job?.stop_outside_window ?? false);
  const [throttle, setThrottle] = useState(
    job?.throttle_bps ? String(job.throttle_bps / MEGA) : "",
  );
  const [guardPercent, setGuardPercent] = useState(String(job?.delete_guard_percent ?? 20));
  const [guardFiles, setGuardFiles] = useState(String(job?.delete_guard_files ?? 0));
  const [trashDays, setTrashDays] = useState(String(job?.trash_days ?? 0));
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

  const account = accounts?.find((item) => item.id === accountId);

  useEffect(() => {
    if (!job && account && partSize === "") {
      setPartSize(String(account.default_part_size / GIGA));
    }
  }, [account, job, partSize]);

  const save = useMutation({
    mutationFn: async () => {
      const payload = {
        name: name.trim(),
        account_id: accountId,
        channel_id: channelId,
        source_type: sourceType,
        local_path: sourceType === "local" ? localPath.trim() : "",
        remote: sourceType === "rclone" ? remote.trim() : null,
        interval_hours: Number(intervalHours),
        scan_files_per_sec: Number(scanRate),
        part_size_bytes: Math.round(Number(partSize) * GIGA),
        include_globs: includeGlobs.trim(),
        exclude_globs: excludeGlobs.trim(),
        max_file_size: maxFileSize ? Math.round(Number(maxFileSize) * GIGA) : 0,
        schedule_hours: scheduleHours,
        stop_outside_window: stopOutsideWindow,
        throttle_bps: throttle ? Math.round(Number(throttle) * MEGA) : 0,
        delete_guard_percent: Number(guardPercent) || 0,
        delete_guard_files: Number(guardFiles) || 0,
        trash_days: Number(trashDays) || 0,
        silence_alerts: silenceAlerts,
        enabled,
      };
      if (job) {
        const { account_id: _ignored, ...rest } = payload;
        return api.patch<Job>(`/api/jobs/${job.id}`, rest);
      }
      return api.post<Job>("/api/jobs", payload);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
      onClose();
    },
    onError: (exc) => setError(exc instanceof Error ? exc.message : "Saving failed"),
  });

  const privateChannels = (channels ?? []).filter((channel) => channel.is_private);
  const sourceReady = sourceType === "local" ? Boolean(localPath.trim()) : Boolean(remote.trim());
  const valid =
    Boolean(name.trim()) && accountId !== null && channelId !== null && sourceReady &&
    Number(intervalHours) > 0 && Number(partSize) > 0;

  return (
    <>
    <Modal
      title={job ? `Edit ${job.name}` : "New sync job"}
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

        <Field label="Destination private channel">
          <select
            value={channelId ?? ""}
            disabled={accountId === null}
            onChange={(e) => setChannelId(Number(e.target.value))}
          >
            <option value="" disabled>
              {accountId === null ? "Pick an account first" : "Pick a channel"}
            </option>
            {privateChannels.map((channel) => (
              <option key={channel.id} value={channel.id}>
                {channel.title}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <Field label="File source">
        <div className="row" style={{ gap: 8 }}>
          <button
            type="button"
            className={sourceType === "local" ? "btn small" : "btn ghost small"}
            onClick={() => setSourceType("local")}
          >
            <FolderOpen size={13} />
            Local folder
          </button>
          <button
            type="button"
            className={sourceType === "rclone" ? "btn small" : "btn ghost small"}
            onClick={() => setSourceType("rclone")}
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

      {sourceType === "local" ? (
        <Field
          label="Local folder"
          hint="Path inside the container, exactly as mounted in docker-compose.yml."
        >
          <input
            value={localPath}
            onChange={(e) => setLocalPath(e.target.value)}
            placeholder="/mnt/documenti"
            className="mono"
          />
        </Field>
      ) : (
        <Field
          label="Rclone remote"
          hint="Remote name with the colon, optionally followed by a subfolder. Read through the API, no mount."
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
              placeholder="miocloud-crypt:Film"
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

      <div className="grid-2">
        <Field label="Every how many hours" hint="The wait starts from the end of the previous run.">
          <input
            value={intervalHours}
            onChange={(e) => setIntervalHours(e.target.value.replace(/[^\d.]/g, ""))}
            inputMode="decimal"
          />
        </Field>

        <Field
          label="Scan rate"
          hint="Files per second. Zero means no limit."
        >
          <input
            value={scanRate}
            onChange={(e) => setScanRate(e.target.value.replace(/\D/g, ""))}
            inputMode="numeric"
          />
        </Field>
      </div>

      <Field
        label="Maximum size of a part (GB)"
        hint={
          account?.is_premium
            ? "Premium account: the Telegram limit is about 3.9 GB per file."
            : "Account without Premium: the Telegram limit is 2 GB per file."
        }
      >
        <input
          value={partSize}
          onChange={(e) => setPartSize(e.target.value.replace(/[^\d.]/g, ""))}
          inputMode="decimal"
        />
      </Field>

      <div className="grid-2">
        <Field
          label="Exclude these files"
          hint="One pattern per line. *.tmp catches the name at any depth, cache/ the whole folder, ** crosses folders. Case does not matter."
        >
          <textarea
            className="mono glob-box"
            value={excludeGlobs}
            spellCheck={false}
            placeholder={"*.tmp\n.DS_Store\nsample/\n**/Trash/**"}
            onChange={(e) => setExcludeGlobs(e.target.value)}
          />
        </Field>

        <Field
          label="Include only these"
          hint="Empty means everything. When it is not empty, only what matches is backed up. Exclude still wins."
        >
          <textarea
            className="mono glob-box"
            value={includeGlobs}
            spellCheck={false}
            placeholder={"*.mkv\n*.mp4"}
            onChange={(e) => setIncludeGlobs(e.target.value)}
          />
        </Field>
      </div>

      <Field
        label="Skip files larger than (GB)"
        hint="Empty or zero means no ceiling."
      >
        <input
          value={maxFileSize}
          onChange={(e) => setMaxFileSize(e.target.value.replace(/[^\d.]/g, ""))}
          inputMode="decimal"
          placeholder="0"
        />
      </Field>

      {job && (includeGlobs.trim() || excludeGlobs.trim() || Number(maxFileSize) > 0) ? (
        <Alert tone="info">
          A filter applies to the whole backup, not only to what comes next: files already
          on Telegram that the filter now leaves out are deleted from the channel on the
          next run, exactly as if they had disappeared from the source.
        </Alert>
      ) : null}

      <Field
        label="Hours the job may run in"
        hint="Drag to paint, click a day or an hour to toggle the whole line. Outside these hours a run that becomes due waits for the next opening instead of being skipped. Times are read in the timezone set in Settings."
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
            ? "Applied in the hours painted as limited. The rest of the week the job goes as fast as it can."
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

      {scheduleHours.includes("2") && !Number(throttle) ? (
        <Alert tone="info">
          Hours are painted as limited but no speed is set, so they behave as ordinary
          open hours.
        </Alert>
      ) : null}

      <div className="grid-2">
        <Field
          label="Stop the run if more than this share disappears (%)"
          hint="A source that fails to mount looks like a source where everything was deleted. Zero turns the check off. Not applied to jobs holding fewer than ten files."
        >
          <input
            value={guardPercent}
            onChange={(e) => setGuardPercent(e.target.value.replace(/\D/g, "").slice(0, 3))}
            inputMode="numeric"
            placeholder="20"
          />
        </Field>

        <Field
          label="...or more than this many files"
          hint="An absolute ceiling, checked whatever the size of the job. Zero turns it off."
        >
          <input
            value={guardFiles}
            onChange={(e) => setGuardFiles(e.target.value.replace(/\D/g, ""))}
            inputMode="numeric"
            placeholder="0"
          />
        </Field>
      </div>

      <Field
        label="Keep deleted files in the channel for (days)"
        hint="A file that disappears from the source goes to the trash instead of being deleted: it stays downloadable, it comes back for free if it returns to the source unchanged, and only then are its messages deleted. Zero deletes straight away. Telegram charges nothing for the space."
      >
        <input
          value={trashDays}
          onChange={(e) => setTrashDays(e.target.value.replace(/\D/g, "").slice(0, 4))}
          inputMode="numeric"
          placeholder="0"
        />
      </Field>

      {Number(guardPercent) === 0 && Number(guardFiles) === 0 ? (
        <Alert tone="info">
          No limit on deletions: if the source becomes unreachable while still looking
          like an empty folder, the next run empties the channel message by message.
        </Alert>
      ) : null}

      <label className="switch">
        <input
          type="checkbox"
          checked={silenceAlerts}
          onChange={(e) => setSilenceAlerts(e.target.checked)}
        />
        <span>
          Warn me if this job stops finishing runs. Turn it off for a job meant to sit
          idle, or it will report itself.
        </span>
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

function JobCard({ job, onEdit }: { job: Job; onEdit: (job: Job) => void }) {
  const queryClient = useQueryClient();
  const progress = useProgress().jobs.get(job.id);
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["jobs"] });

  const start = useMutation({ mutationFn: () => api.post(`/api/jobs/${job.id}/run`), onSuccess: invalidate });
  const stop = useMutation({ mutationFn: () => api.post(`/api/jobs/${job.id}/stop`), onSuccess: invalidate });
  const remove = useMutation({ mutationFn: () => api.del(`/api/jobs/${job.id}`), onSuccess: invalidate });
  const allow = useMutation({
    mutationFn: () => api.post(`/api/jobs/${job.id}/allow-deletions`),
    onSuccess: invalidate,
  });

  const running = job.status === "running";
  const failed = job.status === "error";
  // The one error whose right answer is sometimes "yes, go ahead": the run stopped
  // because it was about to delete more than the job allows.
  const guardTripped = failed && (job.last_error ?? "").startsWith("Deletion guard");

  return (
    <Card>
      <CardHead title={job.name}>
        <div className="row" style={{ gap: 8 }}>
          {running && isHeld(progress) ? (
            <Pill tone="bad" live>
              Flood wait
            </Pill>
          ) : running ? (
            <Pill
              tone={(progress?.phase ?? job.phase) === "waiting" ? "mute" : "ok"}
              live={(progress?.phase ?? job.phase) !== "waiting"}
            >
              {phaseLabel(progress?.phase ?? job.phase ?? "")}
            </Pill>
          ) : failed ? (
            <Pill tone="bad">Error</Pill>
          ) : job.enabled ? (
            <Pill tone="mute">Idle</Pill>
          ) : (
            <Pill tone="warn">Disabled</Pill>
          )}

          {job.silence_alerted_at && job.status !== "running" ? (
            <Pill tone="warn">
              <BellOff size={11} />
              Reported as silent
            </Pill>
          ) : null}

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

          <button type="button" className="btn ghost small" onClick={() => onEdit(job)} disabled={running}>
            <Pencil size={13} />
            Edit
          </button>

          <button
            type="button"
            className="btn danger small"
            disabled={running}
            onClick={() => {
              if (window.confirm(`Delete job ${job.name}? The files on Telegram stay.`)) {
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

        {guardTripped ? (
          job.delete_guard_bypass ? (
            <Alert tone="info">
              The next run of this job is allowed to go through with those deletions.
              Press Run now when the source is ready.
            </Alert>
          ) : (
            <div className="row" style={{ gap: 8 }}>
              <button
                type="button"
                className="btn danger small"
                disabled={running || allow.isPending}
                onClick={() => {
                  if (
                    window.confirm(
                      "The files the guard stopped will be deleted from the channel on " +
                        "the next run of this job. This cannot be undone. Continue?",
                    )
                  ) {
                    allow.mutate();
                  }
                }}
              >
                <ShieldAlert size={13} />
                Allow this deletion once
              </button>
              <span style={{ fontSize: 12, color: "var(--muted)" }}>
                Only after checking that the source is mounted and points where it should.
              </span>
            </div>
          )
        ) : null}

        {allow.isError ? <Alert>{(allow.error as Error).message}</Alert> : null}
        {start.isError ? <Alert>{(start.error as Error).message}</Alert> : null}
        {remove.isError ? <Alert>{(remove.error as Error).message}</Alert> : null}

        <div className="row wrap" style={{ gap: 24, fontSize: 12.5, color: "var(--muted)" }}>
          <span className="row" style={{ gap: 6 }}>
            {job.source_type === "rclone" ? <HardDrive size={12} /> : <FolderOpen size={12} />}
            <span className="mono">
              {job.source_type === "rclone" ? job.remote : job.local_path}
            </span>
          </span>
          <span>to {job.channel_title}</span>
          <span>account {job.account_label}</span>
          <span>every {formatInterval(job.interval_hours)}</span>
          {job.schedule_hours.includes("0") ? (
            <span className="row" style={{ gap: 6 }}>
              <CalendarClock size={12} />
              {describeSchedule(job.schedule_hours)}
            </span>
          ) : null}
          <span>parts of {formatBytes(job.part_size_bytes)}</span>
          {job.throttle_bps > 0 ? (
            <span className="row" style={{ gap: 6 }}>
              <Gauge size={12} />
              {(job.throttle_bps / MEGA).toFixed(1)} MB/s
              {job.schedule_hours.includes("2") ? " in the limited hours" : ""}
            </span>
          ) : null}
          {job.trash_days > 0 ? (
            <span className="row" style={{ gap: 6 }}>
              <Trash2 size={12} />
              deleted files kept {job.trash_days} days
            </span>
          ) : null}
          {job.exclude_globs || job.include_globs || job.max_file_size ? (
            <span className="row" style={{ gap: 6 }}>
              <Filter size={12} />
              {[
                job.include_globs ? `${job.include_globs.split("\n").length} include` : "",
                job.exclude_globs ? `${job.exclude_globs.split("\n").length} exclude` : "",
                job.max_file_size ? `under ${formatBytes(job.max_file_size)}` : "",
              ]
                .filter(Boolean)
                .join(", ")}
            </span>
          ) : null}
        </div>

        {running && progress ? (
          <JobActivity progress={progress} />
        ) : (
          <>
            <ProgressBar done={job.stats.bytes_uploaded} total={job.stats.bytes_total} />
            <div className="row wrap num" style={{ gap: 20, fontSize: 12.5, color: "var(--muted)" }}>
              <span>
                {job.stats.files_uploaded.toLocaleString("en-US")} of{" "}
                {job.stats.files_total.toLocaleString("en-US")} files on Telegram
              </span>
              <span>{formatBytes(job.stats.bytes_uploaded)}</span>
              {job.stats.files_pending > 0 ? (
                <span>{job.stats.files_pending.toLocaleString("en-US")} pending</span>
              ) : null}
              {job.stats.files_error > 0 ? (
                <span style={{ color: "var(--danger)" }}>
                  {job.stats.files_error.toLocaleString("en-US")} in error
                </span>
              ) : null}
              {job.stats.files_trashed > 0 ? (
                <Link to={`/explorer?channel=${job.channel_id}&view=trash`}>
                  {job.stats.files_trashed.toLocaleString("en-US")} in the trash,{" "}
                  {formatBytes(job.stats.bytes_trashed)}
                </Link>
              ) : null}
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
          <Link to={`/files?job=${job.id}`}>See the files</Link>
        </div>
      </div>
    </Card>
  );
}

export default function Jobs() {
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Job | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.get<Job[]>("/api/jobs"),
    refetchInterval: 8000,
  });

  return (
    <>
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <button type="button" className="btn" onClick={() => setCreating(true)}>
          <Plus size={15} />
          New sync job
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
            icon={<CloudUpload size={26} color="var(--muted)" />}
            title="No sync jobs"
            hint="A sync job keeps a source mirrored inside a private channel: it uploads new files, deletes the ones that disappeared and re-uploads the changed ones."
          />
        </Card>
      ) : (
        data.map((job) => <JobCard key={job.id} job={job} onEdit={setEditing} />)
      )}

      {creating ? <JobForm job={null} onClose={() => setCreating(false)} /> : null}
      {editing ? <JobForm job={editing} onClose={() => setEditing(null)} /> : null}
    </>
  );
}
