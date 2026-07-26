import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { Activity, CloudUpload, HardDrive, Users } from "lucide-react";

import { api } from "../lib/api";
import { formatBytes, formatDateTime, formatSpeed, percent } from "../lib/format";
import { useProgress } from "../lib/progress";
import type { Dashboard as DashboardData } from "../lib/types";
import JobActivity, { phaseLabel } from "../components/JobActivity";
import { Card, CardHead, Empty, Pill, Sparkline, Stat } from "../components/ui";

export default function Dashboard() {
  const { jobs, history } = useProgress();
  const { data } = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api.get<DashboardData>("/api/dashboard"),
    refetchInterval: 10000,
  });

  const active = Array.from(jobs.values());
  const totalSpeed = active.reduce((sum, job) => sum + job.speed_bps, 0);

  return (
    <>
      <div className="stat-grid">
        <Stat
          label="Velocita complessiva"
          value={formatSpeed(totalSpeed)}
          hint={active.length ? `${active.length} job in esecuzione` : "nessun job attivo"}
        />
        <Stat
          label="File salvati"
          value={(data?.files_uploaded ?? 0).toLocaleString("it-IT")}
          hint={`su ${(data?.files_total ?? 0).toLocaleString("it-IT")} tracciati`}
        />
        <Stat
          label="Dati su Telegram"
          value={formatBytes(data?.bytes_uploaded ?? 0)}
          hint={`${percent(data?.bytes_uploaded ?? 0, data?.bytes_total ?? 0).toFixed(1)} per cento del totale`}
        />
        <Stat
          label="Account collegati"
          value={`${data?.accounts_connected ?? 0} / ${data?.accounts ?? 0}`}
          hint={data?.files_error ? `${data.files_error} file in errore` : "nessun errore"}
        />
      </div>

      <Card>
        <CardHead title="Job in esecuzione">
          {active.length > 0 ? (
            <Pill tone="ok" live>
              {active.length} attivi
            </Pill>
          ) : null}
        </CardHead>

        {active.length === 0 ? (
          <Empty
            icon={<Activity size={26} color="var(--muted)" />}
            title="Nessun job in esecuzione"
            hint="Quando un sync job parte, qui compaiono velocita di upload, file rimanenti e tempo stimato in tempo reale."
          />
        ) : (
          <div className="card-body">
            {active.map((job) => (
              <div key={job.job_id} style={{ display: "grid", gap: 10 }}>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <div style={{ minWidth: 0 }}>
                    <span style={{ fontWeight: 600 }}>{job.name}</span>
                  </div>
                  <Pill
                    tone={
                      job.phase === "upload" ? "ok" : job.phase === "waiting" ? "mute" : "warn"
                    }
                    live={job.phase !== "waiting"}
                  >
                    {phaseLabel(job.phase)}
                  </Pill>
                </div>

                <JobActivity progress={job} />

                {job.phase === "upload" ? (
                  <Sparkline values={history.get(job.job_id) ?? []} />
                ) : null}
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card>
        <CardHead title="Ultime esecuzioni" />
        {!data || data.recent_runs.length === 0 ? (
          <Empty
            icon={<CloudUpload size={26} color="var(--muted)" />}
            title="Ancora nessuna esecuzione"
            hint="Crea un sync job e avvialo per vedere qui il registro delle corse."
          />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Avvio</th>
                  <th>Esito</th>
                  <th className="right">Esaminati</th>
                  <th className="right">Nuovi</th>
                  <th className="right">Modificati</th>
                  <th className="right">Rimossi</th>
                  <th className="right">Caricati</th>
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
                          ? "Completato"
                          : run.status === "error"
                            ? "Errore"
                            : run.status === "running"
                              ? "In corso"
                              : "Interrotto"}
                      </Pill>
                    </td>
                    <td className="right num">{run.scanned.toLocaleString("it-IT")}</td>
                    <td className="right num">{run.added.toLocaleString("it-IT")}</td>
                    <td className="right num">{run.modified.toLocaleString("it-IT")}</td>
                    <td className="right num">{run.removed.toLocaleString("it-IT")}</td>
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
          label="Sync job configurati"
          value={data?.jobs ?? 0}
          hint={<Link to="/jobs">Gestisci i job</Link>}
        />
        <Stat
          label="File in attesa"
          value={(data?.files_pending ?? 0).toLocaleString("it-IT")}
          hint="da caricare alla prossima corsa"
        />
        <Stat
          label="Spazio totale tracciato"
          value={formatBytes(data?.bytes_total ?? 0)}
          hint={
            <span className="row" style={{ gap: 6 }}>
              <HardDrive size={12} /> cartelle locali sorvegliate
            </span>
          }
        />
        <Stat
          label="Account Telegram"
          value={data?.accounts ?? 0}
          hint={
            <span className="row" style={{ gap: 6 }}>
              <Users size={12} /> <Link to="/accounts">Collega un account</Link>
            </span>
          }
        />
      </div>
    </>
  );
}
