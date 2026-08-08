import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { Activity, CloudDownload, CloudUpload, HardDrive, Users } from "lucide-react";

import { api } from "../lib/api";
import { formatBytes, formatDateTime, formatSpeed, percent } from "../lib/format";
import { useProgress } from "../lib/progress";
import type { Dashboard as DashboardData } from "../lib/types";
import DownloadActivity, { downloadPhaseLabel } from "../components/DownloadActivity";
import { isHeld } from "../components/FloodNotice";
import JobActivity, { phaseLabel } from "../components/JobActivity";
import UpdateBanner from "../components/UpdateBanner";
import { Card, CardHead, Empty, Pill, Sparkline, Stat } from "../components/ui";

export default function Dashboard() {
  const { jobs, downloads, history, downloadHistory } = useProgress();
  const { data } = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api.get<DashboardData>("/api/dashboard"),
    refetchInterval: 10000,
  });

  const active = Array.from(jobs.values());
  const activeDownloads = Array.from(downloads.values());
  const running = active.length + activeDownloads.length;
  const totalSpeed =
    active.reduce((sum, job) => sum + job.speed_bps, 0) +
    activeDownloads.reduce((sum, job) => sum + job.speed_bps, 0);

  return (
    <>
      <UpdateBanner />

      <div className="stat-grid">
        <Stat
          label="Overall speed"
          value={formatSpeed(totalSpeed)}
          hint={running ? `${running} jobs running` : "no active jobs"}
        />
        <Stat
          label="Files saved"
          value={(data?.files_uploaded ?? 0).toLocaleString("en-US")}
          hint={`of ${(data?.files_total ?? 0).toLocaleString("en-US")} tracked`}
        />
        <Stat
          label="Data on Telegram"
          value={formatBytes(data?.bytes_uploaded ?? 0)}
          hint={`${percent(data?.bytes_uploaded ?? 0, data?.bytes_total ?? 0).toFixed(1)} per cent of the total`}
        />
        <Stat
          label="Linked accounts"
          value={`${data?.accounts_connected ?? 0} / ${data?.accounts ?? 0}`}
          hint={data?.files_error ? `${data.files_error} files in error` : "no errors"}
        />
      </div>

      <Card>
        <CardHead title="Running jobs">
          {running > 0 ? (
            <Pill tone="ok" live>
              {running} active
            </Pill>
          ) : null}
        </CardHead>

        {running === 0 ? (
          <Empty
            icon={<Activity size={26} color="var(--muted)" />}
            title="No jobs running"
            hint="When a job starts, transfer speed, remaining files and estimated time show up here live."
          />
        ) : (
          <div className="card-body">
            {active.map((job) => (
              <div key={`sync-${job.job_id}`} style={{ display: "grid", gap: 10 }}>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <div className="row" style={{ minWidth: 0, gap: 8 }}>
                    <CloudUpload size={14} color="var(--muted)" />
                    <span style={{ fontWeight: 600 }}>{job.name}</span>
                  </div>
                  <Pill
                    tone={
                      isHeld(job)
                        ? "bad"
                        : job.phase === "upload"
                          ? "ok"
                          : job.phase === "waiting"
                            ? "mute"
                            : "warn"
                    }
                    live={job.phase !== "waiting"}
                  >
                    {isHeld(job) ? "Flood wait" : phaseLabel(job.phase)}
                  </Pill>
                </div>

                <JobActivity progress={job} />

                {job.phase === "upload" ? (
                  <Sparkline values={history.get(job.job_id) ?? []} />
                ) : null}
              </div>
            ))}

            {activeDownloads.map((job) => (
              <div key={`download-${job.job_id}`} style={{ display: "grid", gap: 10 }}>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <div className="row" style={{ minWidth: 0, gap: 8 }}>
                    <CloudDownload size={14} color="var(--muted)" />
                    <span style={{ fontWeight: 600 }}>{job.name}</span>
                  </div>
                  <Pill
                    tone={
                      isHeld(job)
                        ? "bad"
                        : job.phase === "download"
                          ? "ok"
                          : job.phase === "waiting"
                            ? "mute"
                            : "warn"
                    }
                    live={job.phase !== "waiting"}
                  >
                    {isHeld(job) ? "Flood wait" : downloadPhaseLabel(job.phase)}
                  </Pill>
                </div>

                <DownloadActivity progress={job} />

                {job.phase === "download" ? (
                  <Sparkline values={downloadHistory.get(job.job_id) ?? []} />
                ) : null}
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card>
        <CardHead title="Latest runs" />
        {!data || data.recent_runs.length === 0 ? (
          <Empty
            icon={<CloudUpload size={26} color="var(--muted)" />}
            title="No runs yet"
            hint="Create a sync job and start it to see the run log here."
          />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Outcome</th>
                  <th className="right">Examined</th>
                  <th className="right">New</th>
                  <th className="right">Modified</th>
                  <th className="right">Removed</th>
                  <th className="right">Uploaded</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_runs.map((run) => (
                  <tr key={run.id}>
                    <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(run.started_at)}</td>
                    <td>
                      <Pill
                        tone={
                          run.status === "ok"
                            ? "ok"
                            : run.status === "error"
                              ? "bad"
                              : run.status === "running"
                                ? "accent"
                                : "mute"
                        }
                        live={run.status === "running"}
                      >
                        {run.status === "ok"
                          ? "Completed"
                          : run.status === "error"
                            ? "Error"
                            : run.status === "running"
                              ? "Running"
                              : "Interrupted"}
                      </Pill>
                    </td>
                    <td className="right num">{run.scanned.toLocaleString("en-US")}</td>
                    <td className="right num">{run.added.toLocaleString("en-US")}</td>
                    <td className="right num">{run.modified.toLocaleString("en-US")}</td>
                    <td className="right num">{run.removed.toLocaleString("en-US")}</td>
                    <td className="right num">{formatBytes(run.uploaded_bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="stat-grid">
        <Stat
          label="Sync jobs configured"
          value={data?.jobs ?? 0}
          hint={<Link to="/jobs">Manage the jobs</Link>}
        />
        <Stat
          label="Download jobs configured"
          value={data?.downloads ?? 0}
          hint={<Link to="/downloads">Bring a channel back</Link>}
        />
        <Stat
          label="Total tracked size"
          value={formatBytes(data?.bytes_total ?? 0)}
          hint={
            <span className="row" style={{ gap: 6 }}>
              <HardDrive size={12} /> sources being watched
            </span>
          }
        />
        <Stat
          label="Telegram accounts"
          value={data?.accounts ?? 0}
          hint={
            <span className="row" style={{ gap: 6 }}>
              <Users size={12} /> <Link to="/accounts">Link an account</Link>
            </span>
          }
        />
      </div>
    </>
  );
}
