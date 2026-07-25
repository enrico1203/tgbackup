import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";

import { api } from "../lib/api";
import { formatBytes, formatDateTime, formatDuration } from "../lib/format";
import type { Job, JobRun } from "../lib/types";
import { Alert, Card, CardHead, Empty, Pill, Spinner } from "../components/ui";

const STATUS: Record<string, { text: string; tone: "ok" | "bad" | "accent" | "mute" }> = {
  ok: { text: "Completato", tone: "ok" },
  error: { text: "Errore", tone: "bad" },
  running: { text: "In corso", tone: "accent" },
  stopped: { text: "Interrotto", tone: "mute" },
};

function duration(run: JobRun): string {
  if (!run.finished_at) return "in corso";
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
          title="Nessuno storico"
          hint="Lo storico si popola dopo la prima esecuzione di un sync job."
        />
      </Card>
    );
  }

  return (
    <Card>
      <CardHead title="Storico esecuzioni">
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
          <Spinner /> Caricamento
        </div>
      ) : !data || data.length === 0 ? (
        <Empty
          icon={<History size={26} color="var(--muted)" />}
          title="Questo job non e mai stato eseguito"
        />
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Avvio</th>
                  <th>Durata</th>
                  <th>Esito</th>
                  <th className="right">Esaminati</th>
                  <th className="right">Nuovi</th>
                  <th className="right">Modificati</th>
                  <th className="right">Rimossi</th>
                  <th className="right">File caricati</th>
                  <th className="right">Dati caricati</th>
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
                      <td className="right num">{run.scanned.toLocaleString("it-IT")}</td>
                      <td className="right num">{run.added.toLocaleString("it-IT")}</td>
                      <td className="right num">{run.modified.toLocaleString("it-IT")}</td>
                      <td className="right num">{run.removed.toLocaleString("it-IT")}</td>
                      <td className="right num">{run.uploaded_files.toLocaleString("it-IT")}</td>
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
