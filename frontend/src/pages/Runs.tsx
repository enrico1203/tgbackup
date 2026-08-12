import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CloudDownload, CloudUpload, History } from "lucide-react";

import { api } from "../lib/api";
import { formatBytes, formatDateTime, formatDuration } from "../lib/format";
import type { DownloadJob, DownloadRun, Job, JobRun } from "../lib/types";
import { Alert, Card, CardHead, Empty, Pill, Spinner } from "../components/ui";

const STATUS: Record<string, { text: string; tone: "ok" | "bad" | "accent" | "mute" }> = {
  ok: { text: "Completed", tone: "ok" },
  error: { text: "Error", tone: "bad" },
  running: { text: "Running", tone: "accent" },
  stopped: { text: "Interrupted", tone: "mute" },
};

function duration(run: { started_at: string; finished_at: string | null }): string {
  if (!run.finished_at) return "running";
  const seconds =
    (new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000;
  return formatDuration(seconds);
}

function Outcome({ status }: { status: string }) {
  const entry = STATUS[status] ?? { text: status, tone: "mute" as const };
  return (
    <Pill tone={entry.tone} live={status === "running"}>
      {entry.text}
    </Pill>
  );
}

function Errors({ runs }: { runs: { id: number; started_at: string; error: string | null }[] }) {
  const failed = runs.filter((run) => run.error);
  if (failed.length === 0) return null;
  return (
    <div className="card-body">
      {failed.slice(0, 5).map((run) => (
        <Alert key={run.id}>
          {formatDateTime(run.started_at)}: {run.error}
        </Alert>
      ))}
    </div>
  );
}

function SyncHistory({ jobs }: { jobs: Job[] }) {
  const [jobId, setJobId] = useState<number | null>(null);
  const selected = jobId ?? jobs[0]?.id ?? null;

  const { data, isLoading } = useQuery({
    queryKey: ["runs", selected],
    queryFn: () => api.get<JobRun[]>(`/api/jobs/${selected}/runs?limit=50`),
    enabled: selected !== null,
    refetchInterval: 10000,
  });

  return (
    <Card>
      <CardHead title="Sync jobs">
        <select
          style={{ width: 220 }}
          value={selected ?? ""}
          onChange={(event) => setJobId(Number(event.target.value))}
        >
          {jobs.map((job) => (
            <option key={job.id} value={job.id}>
              {job.name}
            </option>
          ))}
        </select>
      </CardHead>

      {isLoading ? (
        <div className="card-body row" style={{ color: "var(--muted)" }}>
          <Spinner /> Loading
        </div>
      ) : !data || data.length === 0 ? (
        <Empty icon={<CloudUpload size={26} color="var(--muted)" />} title="This job has never run" />
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Duration</th>
                  <th>Outcome</th>
                  <th className="right">Examined</th>
                  <th className="right">New</th>
                  <th className="right">Modified</th>
                  <th className="right">Trashed</th>
                  <th className="right">Removed</th>
                  <th className="right">Files uploaded</th>
                  <th className="right">Data uploaded</th>
                </tr>
              </thead>
              <tbody>
                {data.map((run) => (
                  <tr key={run.id}>
                    <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(run.started_at)}</td>
                    <td className="num">{duration(run)}</td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        <Outcome status={run.status} />
                        {/* A run held back by Telegram succeeded and took four times as
                            long, which nothing else in this table would ever say. */}
                        {run.limited_events ? (
                          <Pill tone="warn">Limited {run.limited_events}x</Pill>
                        ) : null}
                      </div>
                    </td>
                    <td className="right num">{run.scanned.toLocaleString("en-US")}</td>
                    <td className="right num">{run.added.toLocaleString("en-US")}</td>
                    <td className="right num">{run.modified.toLocaleString("en-US")}</td>
                    <td className="right num">
                      {run.trashed.toLocaleString("en-US")}
                      {run.revived ? ` (-${run.revived.toLocaleString("en-US")})` : ""}
                    </td>
                    <td className="right num">{run.removed.toLocaleString("en-US")}</td>
                    <td className="right num">{run.uploaded_files.toLocaleString("en-US")}</td>
                    <td className="right num">{formatBytes(run.uploaded_bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Errors runs={data} />
        </>
      )}
    </Card>
  );
}

function DownloadHistory({ jobs }: { jobs: DownloadJob[] }) {
  const [jobId, setJobId] = useState<number | null>(null);
  const selected = jobId ?? jobs[0]?.id ?? null;

  const { data, isLoading } = useQuery({
    queryKey: ["download-runs", selected],
    queryFn: () => api.get<DownloadRun[]>(`/api/downloads/${selected}/runs?limit=50`),
    enabled: selected !== null,
    refetchInterval: 10000,
  });

  return (
    <Card>
      <CardHead title="Download jobs">
        <select
          style={{ width: 220 }}
          value={selected ?? ""}
          onChange={(event) => setJobId(Number(event.target.value))}
        >
          {jobs.map((job) => (
            <option key={job.id} value={job.id}>
              {job.name}
            </option>
          ))}
        </select>
      </CardHead>

      {isLoading ? (
        <div className="card-body row" style={{ color: "var(--muted)" }}>
          <Spinner /> Loading
        </div>
      ) : !data || data.length === 0 ? (
        <Empty
          icon={<CloudDownload size={26} color="var(--muted)" />}
          title="This job has never run"
        />
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Duration</th>
                  <th>Outcome</th>
                  <th className="right">In the channel</th>
                  <th className="right">Already there</th>
                  <th className="right">Files downloaded</th>
                  <th className="right">Data downloaded</th>
                  <th className="right">Failed</th>
                </tr>
              </thead>
              <tbody>
                {data.map((run) => (
                  <tr key={run.id}>
                    <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(run.started_at)}</td>
                    <td className="num">{duration(run)}</td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        <Outcome status={run.status} />
                        {/* A run held back by Telegram succeeded and took four times as
                            long, which nothing else in this table would ever say. */}
                        {run.limited_events ? (
                          <Pill tone="warn">Limited {run.limited_events}x</Pill>
                        ) : null}
                      </div>
                    </td>
                    <td className="right num">{run.indexed_files.toLocaleString("en-US")}</td>
                    <td className="right num">{run.present_files.toLocaleString("en-US")}</td>
                    <td className="right num">{run.downloaded_files.toLocaleString("en-US")}</td>
                    <td className="right num">{formatBytes(run.downloaded_bytes)}</td>
                    <td className="right num">{run.failed_files.toLocaleString("en-US")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Errors runs={data} />
        </>
      )}
    </Card>
  );
}

export default function Runs() {
  const { data: jobs } = useQuery({ queryKey: ["jobs"], queryFn: () => api.get<Job[]>("/api/jobs") });
  const { data: downloads } = useQuery({
    queryKey: ["downloads"],
    queryFn: () => api.get<DownloadJob[]>("/api/downloads"),
  });

  const hasJobs = Boolean(jobs && jobs.length > 0);
  const hasDownloads = Boolean(downloads && downloads.length > 0);

  if (!hasJobs && !hasDownloads) {
    return (
      <Card>
        <Empty
          icon={<History size={26} color="var(--muted)" />}
          title="No history yet"
          hint="History fills up after the first run of a job."
        />
      </Card>
    );
  }

  return (
    <>
      {hasJobs ? <SyncHistory jobs={jobs ?? []} /> : null}
      {hasDownloads ? <DownloadHistory jobs={downloads ?? []} /> : null}
    </>
  );
}
