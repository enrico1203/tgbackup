import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";

import { api } from "../lib/api";
import { formatBytes, formatDateTime, formatDuration } from "../lib/format";
import type { Job, JobRun } from "../lib/types";
import { Alert, Card, CardHead, Empty, Pill, Spinner } from "../components/ui";

const STATUS: Record<string, { text: string; tone: "ok" | "bad" | "accent" | "mute" }> = {
  ok: { text: "Completed", tone: "ok" },
  error: { text: "Error", tone: "bad" },
  running: { text: "Running", tone: "accent" },
  stopped: { text: "Interrupted", tone: "mute" },
};

function duration(run: JobRun): string {
  if (!run.finished_at) return "running";
  const seconds =
    (new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000;
  return formatDuration(seconds);
}

export default function Runs() {
  const { data: jobs } = useQuery({ queryKey: ["jobs"], queryFn: () => api.get<Job[]>("/api/jobs") });
  const [jobId, setJobId] = useState<number | null>(null);
  const selected = jobId ?? jobs?.[0]?.id ?? null;

  const { data, isLoading } = useQuery({
    queryKey: ["runs", selected],
    queryFn: () => api.get<JobRun[]>(`/api/jobs/${selected}/runs?limit=50`),
    enabled: selected !== null,
    refetchInterval: 10000,
  });

  if (!jobs || jobs.length === 0) {
    return (
      <Card>
        <Empty
          icon={<History size={26} color="var(--muted)" />}
          title="No history yet"
          hint="History fills up after the first run of a sync job."
        />
      </Card>
    );
  }

  return (
    <Card>
      <CardHead title="Run history">
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
          icon={<History size={26} color="var(--muted)" />}
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
                  <th className="right">Examined</th>
                  <th className="right">New</th>
                  <th className="right">Modified</th>
                  <th className="right">Removed</th>
                  <th className="right">Files uploaded</th>
                  <th className="right">Data uploaded</th>
                </tr>
              </thead>
              <tbody>
                {data.map((run) => {
                  const status = STATUS[run.status] ?? { text: run.status, tone: "mute" as const };
                  return (
                    <tr key={run.id}>
                      <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(run.started_at)}</td>
                      <td className="num">{duration(run)}</td>
                      <td>
                        <Pill tone={status.tone} live={run.status === "running"}>
                          {status.text}
                        </Pill>
                      </td>
                      <td className="right num">{run.scanned.toLocaleString("en-US")}</td>
                      <td className="right num">{run.added.toLocaleString("en-US")}</td>
                      <td className="right num">{run.modified.toLocaleString("en-US")}</td>
                      <td className="right num">{run.removed.toLocaleString("en-US")}</td>
                      <td className="right num">{run.uploaded_files.toLocaleString("en-US")}</td>
                      <td className="right num">{formatBytes(run.uploaded_bytes)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {data.some((run) => run.error) ? (
            <div className="card-body">
              {data
                .filter((run) => run.error)
                .slice(0, 5)
                .map((run) => (
                  <Alert key={run.id}>
                    {formatDateTime(run.started_at)}: {run.error}
                  </Alert>
                ))}
            </div>
          ) : null}
        </>
      )}
    </Card>
  );
}
