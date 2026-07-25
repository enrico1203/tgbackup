import { Fragment, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { Download, FileStack, Search } from "lucide-react";

import { api } from "../lib/api";
import { formatBytes, formatDateTime, formatDuration, formatSpeed } from "../lib/format";
import { useProgress } from "../lib/progress";
import type { FileEntry, FilePage, Job, RestoreOut } from "../lib/types";
import { Alert, Card, CardHead, Empty, Pill, ProgressBar, Spinner } from "../components/ui";

const PAGE_SIZE = 100;

const STATE_LABELS: Record<string, { text: string; tone: "ok" | "warn" | "bad" | "mute" }> = {
  uploaded: { text: "Su Telegram", tone: "ok" },
  pending: { text: "In attesa", tone: "warn" },
  uploading: { text: "In corso", tone: "warn" },
  stale: { text: "Da ricaricare", tone: "warn" },
  to_delete: { text: "Da cancellare", tone: "mute" },
  error: { text: "Errore", tone: "bad" },
};

function RestorePanel() {
  const { restores } = useProgress();
  const items = Array.from(restores.values());
  if (items.length === 0) return null;

  return (
    <Card>
      <CardHead title="Restore in corso" />
      <div className="card-body">
        {items.map((restore) => (
          <div key={restore.restore_id} style={{ display: "grid", gap: 8 }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span style={{ fontWeight: 600 }}>{restore.file_name}</span>
              <Pill
                tone={restore.phase === "done" ? "ok" : restore.phase === "error" ? "bad" : "accent"}
                live={restore.phase === "running"}
              >
                {restore.phase === "done"
                  ? "Completato"
                  : restore.phase === "error"
                    ? "Errore"
                    : "In corso"}
              </Pill>
            </div>
            <div className="mono truncate" style={{ color: "var(--muted)" }}>
              {restore.target_path}
            </div>
            <ProgressBar done={restore.bytes_done} total={restore.bytes_total} />
            <div className="row wrap num" style={{ gap: 20, fontSize: 12.5, color: "var(--muted)" }}>
              <span>
                {formatBytes(restore.bytes_done)} di {formatBytes(restore.bytes_total)}
              </span>
              <span>{formatSpeed(restore.speed_bps)}</span>
              <span>stimato {formatDuration(restore.eta_seconds)}</span>
            </div>
            {restore.error ? <Alert>{restore.error}</Alert> : null}
          </div>
        ))}
      </div>
    </Card>
  );
}

export default function Files() {
  const [params, setParams] = useSearchParams();
  const jobParam = params.get("job");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [state, setState] = useState("");
  const [page, setPage] = useState(0);
  const [expanded, setExpanded] = useState<number | null>(null);

  const { data: jobs } = useQuery({ queryKey: ["jobs"], queryFn: () => api.get<Job[]>("/api/jobs") });

  const filters = new URLSearchParams();
  if (jobParam) filters.set("job_id", jobParam);
  if (state) filters.set("state", state);
  if (query) filters.set("search", query);
  filters.set("offset", String(page * PAGE_SIZE));
  filters.set("limit", String(PAGE_SIZE));

  const { data, isLoading } = useQuery({
    queryKey: ["files", filters.toString()],
    queryFn: () => api.get<FilePage>(`/api/files?${filters.toString()}`),
    refetchInterval: 15000,
  });

  const restore = useMutation({
    mutationFn: (fileId: number) => api.post<RestoreOut>("/api/files/restore", { file_id: fileId }),
  });

  const total = data?.total ?? 0;
  const pages = Math.ceil(total / PAGE_SIZE);

  return (
    <>
      <RestorePanel />

      <Card>
        <CardHead title={`File tracciati (${total.toLocaleString("it-IT")})`} />

        <div className="card-body">
          <div className="row wrap">
            <div className="row" style={{ flex: 1, minWidth: 220, gap: 8 }}>
              <Search size={16} color="var(--muted)" />
              <input
                value={search}
                placeholder="Cerca per percorso o nome"
                onChange={(event) => setSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    setQuery(search.trim());
                    setPage(0);
                  }
                }}
              />
            </div>

            <select
              style={{ width: 200 }}
              value={jobParam ?? ""}
              onChange={(event) => {
                const next = new URLSearchParams(params);
                if (event.target.value) next.set("job", event.target.value);
                else next.delete("job");
                setParams(next);
                setPage(0);
              }}
            >
              <option value="">Tutti i job</option>
              {(jobs ?? []).map((job) => (
                <option key={job.id} value={job.id}>
                  {job.name}
                </option>
              ))}
            </select>

            <select
              style={{ width: 180 }}
              value={state}
              onChange={(event) => {
                setState(event.target.value);
                setPage(0);
              }}
            >
              <option value="">Tutti gli stati</option>
              <option value="uploaded">Su Telegram</option>
              <option value="pending">In attesa</option>
              <option value="error">In errore</option>
            </select>

            <button
              type="button"
              className="btn ghost"
              onClick={() => {
                setQuery(search.trim());
                setPage(0);
              }}
            >
              Cerca
            </button>
          </div>

          {restore.isError ? <Alert>{(restore.error as Error).message}</Alert> : null}
          {restore.isSuccess ? (
            <Alert tone="info">
              Restore avviato. Il file ricomposto sara in {restore.data.target_path} dentro il
              container, cioe nella cartella data del progetto.
            </Alert>
          ) : null}
        </div>

        {isLoading ? (
          <div className="card-body row" style={{ color: "var(--muted)" }}>
            <Spinner /> Caricamento
          </div>
        ) : !data || data.items.length === 0 ? (
          <Empty
            icon={<FileStack size={26} color="var(--muted)" />}
            title="Nessun file"
            hint="I file compaiono qui dopo la prima scansione di un sync job."
          />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Percorso</th>
                  <th>Stato</th>
                  <th className="right">Dimensione</th>
                  <th className="right">Parti</th>
                  <th>Caricato il</th>
                  <th className="right">Azioni</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((entry: FileEntry) => {
                  const label = STATE_LABELS[entry.state] ?? { text: entry.state, tone: "mute" as const };
                  return (
                    <Fragment key={entry.id}>
                      <tr
                        className="clickable"
                        onClick={() => setExpanded(expanded === entry.id ? null : entry.id)}
                      >
                        <td>
                          <div className="mono truncate">{entry.rel_path}</div>
                          {entry.error ? (
                            <div style={{ color: "var(--danger)", fontSize: 12 }}>{entry.error}</div>
                          ) : null}
                        </td>
                        <td>
                          <Pill tone={label.tone}>{label.text}</Pill>
                        </td>
                        <td className="right num">{formatBytes(entry.size)}</td>
                        <td className="right num">{entry.parts_total}</td>
                        <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(entry.uploaded_at)}</td>
                        <td className="right">
                          <button
                            type="button"
                            className="btn ghost small"
                            disabled={entry.state !== "uploaded" || restore.isPending}
                            onClick={(event) => {
                              event.stopPropagation();
                              restore.mutate(entry.id);
                            }}
                          >
                            <Download size={13} />
                            Restore
                          </button>
                        </td>
                      </tr>
                      {expanded === entry.id && entry.parts.length > 0 ? (
                        <tr>
                          <td colSpan={6} style={{ background: "var(--ground)" }}>
                            <span className="section-label">Parti su Telegram</span>
                            <div className="row wrap mono" style={{ gap: 16, marginTop: 8 }}>
                              {entry.parts.map((part) => (
                                <span key={part.part_index} style={{ color: "var(--muted)" }}>
                                  parte {part.part_index + 1}: messaggio {part.message_id},{" "}
                                  {formatBytes(part.size)} da offset {part.offset}
                                </span>
                              ))}
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {pages > 1 ? (
          <div className="card-body row" style={{ justifyContent: "center" }}>
            <button
              type="button"
              className="btn ghost small"
              disabled={page === 0}
              onClick={() => setPage((current) => current - 1)}
            >
              Precedente
            </button>
            <span className="num" style={{ color: "var(--muted)" }}>
              pagina {page + 1} di {pages}
            </span>
            <button
              type="button"
              className="btn ghost small"
              disabled={page + 1 >= pages}
              onClick={() => setPage((current) => current + 1)}
            >
              Successiva
            </button>
          </div>
        ) : null}
      </Card>
    </>
  );
}
