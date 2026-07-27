import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Hammer, RefreshCcw, ShieldCheck, Wrench } from "lucide-react";

import { api } from "../lib/api";
import { formatDateTime, percent } from "../lib/format";
import type { Account, Channel, MaintenanceTask } from "../lib/types";
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

interface ChannelJob {
  id: number;
  name: string;
}

const LABELS: Record<string, string> = {
  files_checked: "Files checked",
  parts_checked: "Parts checked",
  parts_missing: "Parts gone from the channel",
  parts_wrong_size: "Parts of the wrong size",
  files_broken: "Damaged files",
  files_marked: "Marked for re-upload",
  messages_read: "Messages read",
  messages_without_document: "Messages without a file",
  messages_unreadable: "Messages that could not be read",
  files_found: "Files found",
  files_incomplete: "Files with parts missing",
  files_written: "Files written to the index",
  files_skipped: "Files already in the index",
  parts_written: "Parts written",
  job_name: "Job",
  job_action: "What happened to the job",
};

function Result({ task }: { task: MaintenanceTask }) {
  const entries = Object.entries(task.result).filter(([key]) => key !== "sample");
  const sample = (task.result.sample as string[] | undefined) ?? [];

  return (
    <>
      <div className="table-wrap">
        <table>
          <tbody>
            {entries.map(([key, value]) => (
              <tr key={key}>
                <td>{LABELS[key] ?? key}</td>
                <td className="right num">
                  {typeof value === "number" ? value.toLocaleString("en-US") : String(value)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {sample.length > 0 ? (
        <div className="card-body">
          <span className="section-label">The first damaged files</span>
          <div className="mono" style={{ fontSize: 12, color: "var(--muted)", marginTop: 8 }}>
            {sample.map((line) => (
              <div key={line} className="truncate">
                {line}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </>
  );
}

function TaskCard({ task }: { task: MaintenanceTask }) {
  const running = task.phase === "running";
  const title = task.kind === "check" ? "Check" : "Rebuild";

  return (
    <Card>
      <CardHead title={`${title} of ${task.channel_title}`}>
        {running ? (
          <Pill tone="ok" live>
            {task.step || "running"}
          </Pill>
        ) : task.phase === "error" ? (
          <Pill tone="bad">Failed</Pill>
        ) : (
          <Pill tone="mute">Finished</Pill>
        )}
      </CardHead>

      <div className="card-body">
        {task.error ? <Alert>{task.error}</Alert> : null}

        {running ? (
          <>
            <ProgressBar done={task.processed} total={task.total} />
            <div className="row wrap num" style={{ gap: 20, fontSize: 12.5 }}>
              <strong>
                {task.processed.toLocaleString("en-US")}
                {task.total ? ` of ${task.total.toLocaleString("en-US")}` : ""}
              </strong>
              {task.total ? (
                <span style={{ color: "var(--muted)" }}>
                  {percent(task.processed, task.total).toFixed(0)} per cent
                </span>
              ) : null}
              <span style={{ color: "var(--muted)" }}>
                started {formatDateTime(task.started_at)}
              </span>
            </div>
          </>
        ) : (
          <div className="row wrap" style={{ gap: 20, fontSize: 12.5, color: "var(--muted)" }}>
            <span>started {formatDateTime(task.started_at)}</span>
            <span>finished {formatDateTime(task.finished_at)}</span>
          </div>
        )}
      </div>

      {!running && Object.keys(task.result).length > 0 ? <Result task={task} /> : null}
    </Card>
  );
}

function RebuildForm({
  channelId,
  onClose,
}: {
  channelId: number;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"create" | "merge">("create");
  const [jobId, setJobId] = useState<number | null>(null);
  const [jobName, setJobName] = useState("");

  const { data: jobs } = useQuery({
    queryKey: ["channel-jobs", channelId],
    queryFn: () => api.get<ChannelJob[]>(`/api/maintenance/channels/${channelId}/jobs`),
  });

  const start = useMutation({
    mutationFn: () =>
      api.post<MaintenanceTask>("/api/maintenance/rebuild", {
        channel_id: channelId,
        mode,
        job_id: mode === "merge" ? jobId : null,
        job_name: jobName.trim(),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["maintenance"] });
      onClose();
    },
  });

  const valid = mode === "create" || jobId !== null;

  return (
    <Modal
      title="Rebuild the index from the channel"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn"
            disabled={!valid || start.isPending}
            onClick={() => start.mutate()}
          >
            {start.isPending ? <Spinner /> : null}
            Start
          </button>
        </>
      }
    >
      {start.isError ? <Alert>{(start.error as Error).message}</Alert> : null}

      <Alert tone="info">
        Every message of the channel is read and the index is put back together out of the
        captions, which carry the name, the folder and the part number. Paths the index
        already knows are left alone, so this can be run again safely. The modification
        time is nowhere in a message: it is adopted from the source at the first scan,
        without re-uploading anything.
      </Alert>

      <Field label="Where the files go">
        <div className="row" style={{ gap: 8 }}>
          <button
            type="button"
            className={mode === "create" ? "btn small" : "btn ghost small"}
            onClick={() => setMode("create")}
          >
            A new job
          </button>
          <button
            type="button"
            className={mode === "merge" ? "btn small" : "btn ghost small"}
            onClick={() => setMode("merge")}
            disabled={(jobs ?? []).length === 0}
            title={
              (jobs ?? []).length === 0 ? "No job writes to this channel yet" : undefined
            }
          >
            An existing job
          </button>
        </div>
      </Field>

      {mode === "create" ? (
        <Field
          label="Name of the new job"
          hint="It arrives disabled and with no source: point it at the right folder or remote before enabling it."
        >
          <input
            value={jobName}
            placeholder="Leave empty to name it after the channel"
            onChange={(event) => setJobName(event.target.value)}
          />
        </Field>
      ) : (
        <Field label="Job to add the files to">
          <select
            value={jobId ?? ""}
            onChange={(event) => setJobId(Number(event.target.value))}
          >
            <option value="" disabled>
              Pick a job
            </option>
            {(jobs ?? []).map((job) => (
              <option key={job.id} value={job.id}>
                {job.name}
              </option>
            ))}
          </select>
        </Field>
      )}
    </Modal>
  );
}

export default function Maintenance() {
  const queryClient = useQueryClient();
  const [accountId, setAccountId] = useState<number | null>(null);
  const [channelId, setChannelId] = useState<number | null>(null);
  const [repair, setRepair] = useState(true);
  const [rebuilding, setRebuilding] = useState(false);

  const { data: accounts } = useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.get<Account[]>("/api/accounts"),
  });

  const account = accountId ?? accounts?.[0]?.id ?? null;

  const { data: channels, isFetching: loadingChannels } = useQuery({
    queryKey: ["channels", account],
    queryFn: () => api.get<Channel[]>(`/api/accounts/${account}/channels`),
    enabled: account !== null,
  });

  const refreshChannels = useMutation({
    mutationFn: () => api.get<Channel[]>(`/api/accounts/${account}/channels?refresh=true`),
    onSuccess: (result) => queryClient.setQueryData(["channels", account], result),
  });

  const { data: tasks } = useQuery({
    queryKey: ["maintenance"],
    queryFn: () => api.get<MaintenanceTask[]>("/api/maintenance/tasks"),
    // While something is running the page has to move: the check and the rebuild take
    // minutes on a large channel.
    refetchInterval: (query) =>
      (query.state.data ?? []).some((task) => task.phase === "running") ? 2000 : 10000,
  });

  const check = useMutation({
    mutationFn: () =>
      api.post<MaintenanceTask>("/api/maintenance/check", {
        channel_id: channelId,
        repair,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["maintenance"] }),
  });

  return (
    <>
      <Card>
        <CardHead title="Channel to work on" />
        <div className="card-body">
          <p style={{ margin: 0, color: "var(--muted)", maxWidth: "76ch" }}>
            The index in the database is the only thing that knows what the files on
            Telegram are. Checking goes from the index to the channel and reports what is
            no longer there; rebuilding goes the other way and puts the index back together
            by reading the messages, which is what a lost database needs.
          </p>

          <div className="grid-2">
            <Field label="Telegram account">
              <select
                value={account ?? ""}
                onChange={(event) => {
                  setAccountId(Number(event.target.value));
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

            <Field label="Channel" hint="Any channel of the account, even one with no job.">
              <div className="row" style={{ gap: 8 }}>
                <select
                  value={channelId ?? ""}
                  disabled={account === null || loadingChannels}
                  onChange={(event) => setChannelId(Number(event.target.value))}
                >
                  <option value="" disabled>
                    {loadingChannels ? "Loading" : "Pick a channel"}
                  </option>
                  {(channels ?? []).map((channel) => (
                    <option key={channel.id} value={channel.id}>
                      {channel.title}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn ghost small"
                  disabled={account === null || refreshChannels.isPending}
                  onClick={() => refreshChannels.mutate()}
                  title="Read the channel list from Telegram again"
                >
                  {refreshChannels.isPending ? <Spinner /> : <RefreshCcw size={13} />}
                  Refresh
                </button>
              </div>
            </Field>
          </div>

          {check.isError ? <Alert>{(check.error as Error).message}</Alert> : null}

          <label className="switch">
            <input
              type="checkbox"
              checked={repair}
              onChange={(event) => setRepair(event.target.checked)}
            />
            <span>
              While checking, mark the damaged files so the job uploads them again
            </span>
          </label>

          <div className="row wrap">
            <button
              type="button"
              className="btn"
              disabled={channelId === null || check.isPending}
              onClick={() => check.mutate()}
            >
              {check.isPending ? <Spinner /> : <ShieldCheck size={14} />}
              Check the channel
            </button>
            <button
              type="button"
              className="btn ghost"
              disabled={channelId === null}
              onClick={() => setRebuilding(true)}
            >
              <Hammer size={14} />
              Rebuild the index
            </button>
          </div>

          <span style={{ fontSize: 12, color: "var(--muted)" }}>
            Neither of the two can run while a sync job is uploading to that channel: they
            change the index that job is writing. A check that only reports, with the box
            above unticked, is always allowed.
          </span>
        </div>
      </Card>

      {!tasks || tasks.length === 0 ? (
        <Card>
          <Empty
            icon={<Wrench size={26} color="var(--muted)" />}
            title="Nothing has been run yet"
            hint="The result of a check or a rebuild shows up here, and stays until the backend restarts."
          />
        </Card>
      ) : (
        tasks.map((task) => <TaskCard key={task.id} task={task} />)
      )}

      {rebuilding && channelId !== null ? (
        <RebuildForm channelId={channelId} onClose={() => setRebuilding(false)} />
      ) : null}
    </>
  );
}
