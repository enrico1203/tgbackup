import { Fragment, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { Download, FileStack, Hash, Search } from "lucide-react";

import { api } from "../lib/api";
import { formatBytes, formatDateTime, formatDuration, formatSpeed, percent } from "../lib/format";
import { useProgress } from "../lib/progress";
import type { FileEntry, FilePage, Job, RestoreOut } from "../lib/types";
import {
  Alert,
  Card,
  CardHead,
  Empty,
  Pill,
  ProgressBar,
  Spinner,
} from "../components/ui";

const PAGE_SIZE = 100;

const STATE_LABELS: Record<string, { text: string; tone: "ok" | "warn" | "bad" | "mute" }> = {
  uploaded: { text: "Su Telegram", tone: "ok" },
  pending: { text: "In attesa", tone: "warn" },
  uploading: { text: "In corso", tone: "warn" },
  stale: { text: "Da ricaricare", tone: "warn" },
  to_delete: { text: "Da cancellare", tone: "mute" },
  error: { text: "Errore", tone: "bad" },
};

interface ChannelGroup {
  channelId: number;
  title: string;
  accounts: string[];
  jobNames: string[];
  filesTotal: number;
  filesUploaded: number;
  filesError: number;
  bytesTotal: number;
  bytesUploaded: number;
}

/** I file appartengono a un job, e ogni job scrive su un canale. Per mostrarli per
 *  canale si raggruppano i job che condividono la stessa destinazione. */
function groupByChannel(jobs: Job[]): ChannelGroup[] {
  const groups = new Map<number, ChannelGroup>();
  for (const job of jobs) {
    let group = groups.get(job.channel_id);
    if (!group) {
      group = {
        channelId: job.channel_id,
        title: job.channel_title,
        accounts: [],
        jobNames: [],
        filesTotal: 0,
        filesUploaded: 0,
        filesError: 0,
        bytesTotal: 0,
        bytesUploaded: 0,
      };
      groups.set(job.channel_id, group);
    }
    if (!group.accounts.includes(job.account_label)) group.accounts.push(job.account_label);
    group.jobNames.push(job.name);
    group.filesTotal += job.stats.files_total;
    group.filesUploaded += job.stats.files_uploaded;
    group.filesError += job.stats.files_error;
    group.bytesTotal += job.stats.bytes_total;
    group.bytesUploaded += job.stats.bytes_uploaded;
  }
  return Array.from(groups.values()).sort((a, b) => a.title.localeCompare(b.title));
}

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

function ChannelPicker({
  groups,
  selected,
  onSelect,
}: {
  groups: ChannelGroup[];
  selected: number | null;
  onSelect: (channelId: number) => void;
}) {
  return (
    <div className="channel-grid">
      {groups.map((group) => {
        const done = percent(group.filesUploaded, group.filesTotal);
        return (
          <button
            key={group.channelId}
            type="button"
            className={group.channelId === selected ? "channel-card active" : "channel-card"}
            onClick={() => onSelect(group.channelId)}
          >
            <div className="row" style={{ gap: 9 }}>
              <Hash size={15} style={{ flexShrink: 0, opacity: 0.65 }} />
              <span className="channel-title">{group.title}</span>
              {group.filesError > 0 ? (
                <span style={{ marginLeft: "auto" }}>
                  <Pill tone="bad">{group.filesError}</Pill>
                </span>
              ) : null}
            </div>

            <div className="channel-meta num">
              {group.filesUploaded.toLocaleString("it-IT")} di{" "}
              {group.filesTotal.toLocaleString("it-IT")} file, {formatBytes(group.bytesUploaded)}
            </div>

            <ProgressBar done={group.filesUploaded} total={group.filesTotal} />

            <div className="channel-meta">
              {group.jobNames.join(", ")} su {group.accounts.join(", ")}
              <span className="num"> — {done.toFixed(done >= 99.5 ? 1 : 0)} per cento</span>
            </div>
          </button>
        );
      })}
    </div>
  );
}

export default function Files() {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [state, setState] = useState("");
  const [page, setPage] = useState(0);
  const [expanded, setExpanded] = useState<number | null>(null);

  const { data: jobs } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.get<Job[]>("/api/jobs"),
    refetchInterval: 15000,
  });

  const groups = useMemo(() => groupByChannel(jobs ?? []), [jobs]);

  // Il canale scelto vive nella query string, cosi il link resta condivisibile.
  const fromUrl = params.get("channel");
  const selected =
    fromUrl !== null && groups.some((g) => g.channelId === Number(fromUrl))
      ? Number(fromUrl)
      : (groups[0]?.channelId ?? null);

  const selectChannel = (channelId: number) => {
    const next = new URLSearchParams(params);
    next.set("channel", String(channelId));
    setParams(next);
    setPage(0);
    setExpanded(null);
  };

  const filters = new URLSearchParams();
  if (selected !== null) filters.set("channel_id", String(selected));
  if (state) filters.set("state", state);
  if (query) filters.set("search", query);
  filters.set("offset", String(page * PAGE_SIZE));
  filters.set("limit", String(PAGE_SIZE));

  const { data, isLoading } = useQuery({
    queryKey: ["files", filters.toString()],
    queryFn: () => api.get<FilePage>(`/api/files?${filters.toString()}`),
    enabled: selected !== null,
    refetchInterval: 15000,
  });

  const restore = useMutation({
    mutationFn: (fileId: number) => api.post<RestoreOut>("/api/files/restore", { file_id: fileId }),
  });

  const current = groups.find((group) => group.channelId === selected);
  const total = data?.total ?? 0;
  const pages = Math.ceil(total / PAGE_SIZE);

  if (groups.length === 0) {
    return (
      <Card>
        <Empty
          icon={<FileStack size={26} color="var(--muted)" />}
          title="Nessun canale con file"
          hint="I canali compaiono qui quando un sync job li usa come destinazione."
        />
      </Card>
    );
  }

  return (
    <>
      <RestorePanel />

      <div>
        <span className="section-label">Canali di destinazione</span>
        <div style={{ marginTop: 10 }}>
          <ChannelPicker groups={groups} selected={selected} onSelect={selectChannel} />
        </div>
      </div>

      <Card>
        <CardHead title={current ? current.title : "File"}>
          <span className="num" style={{ color: "var(--muted)", fontSize: 12.5 }}>
            {total.toLocaleString("it-IT")} file
          </span>
        </CardHead>

        <div className="card-body">
          <div className="row wrap">
            <div className="row" style={{ flex: 1, minWidth: 220, gap: 8 }}>
              <Search size={16} color="var(--muted)" />
              <input
                value={search}
                placeholder="Cerca per percorso o nome in questo canale"
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
            title="Nessun file in questo canale"
            hint="I file compaiono dopo la prima scansione del sync job che scrive qui."
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
                  const label = STATE_LABELS[entry.state] ?? {
                    text: entry.state,
                    tone: "mute" as const,
                  };
                  return (
                    <Fragment key={entry.id}>
                      <tr
                        className="clickable"
                        onClick={() => setExpanded(expanded === entry.id ? null : entry.id)}
                      >
                        <td>
                          <div className="truncate" style={{ fontWeight: 500 }}>
                            {entry.name}
                          </div>
                          <div className="mono truncate" style={{ color: "var(--muted)" }}>
                            {entry.rel_path}
                          </div>
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
