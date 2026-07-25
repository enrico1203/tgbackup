import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { CloudUpload, Pencil, Play, Plus, Square, Trash2 } from "lucide-react";

import { api } from "../lib/api";
import { formatBytes, formatDateTime, formatInterval } from "../lib/format";
import { useProgress } from "../lib/progress";
import JobActivity, { phaseLabel } from "../components/JobActivity";
import type { Account, Channel, Job } from "../lib/types";
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
  const [localPath, setLocalPath] = useState(job?.local_path ?? "");
  const [intervalHours, setIntervalHours] = useState(String(job?.interval_hours ?? 24));
  const [scanRate, setScanRate] = useState(String(job?.scan_files_per_sec ?? 0));
  const [partSize, setPartSize] = useState(
    job ? String(job.part_size_bytes / GIGA) : "",
  );
  const [enabled, setEnabled] = useState(job?.enabled ?? true);

  const { data: channels } = useQuery({
    queryKey: ["channels", accountId],
    queryFn: () => api.get<Channel[]>(`/api/accounts/${accountId}/channels`),
    enabled: accountId !== null,
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
        local_path: localPath.trim(),
        interval_hours: Number(intervalHours),
        scan_files_per_sec: Number(scanRate),
        part_size_bytes: Math.round(Number(partSize) * GIGA),
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
    onError: (exc) => setError(exc instanceof Error ? exc.message : "Salvataggio non riuscito"),
  });

  const privateChannels = (channels ?? []).filter((channel) => channel.is_private);
  const valid =
    name.trim() && accountId !== null && channelId !== null && localPath.trim() &&
    Number(intervalHours) > 0 && Number(partSize) > 0;

  return (
    <Modal
      title={job ? `Modifica ${job.name}` : "Nuovo sync job"}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn ghost" onClick={onClose}>
            Annulla
          </button>
          <button
            type="button"
            className="btn"
            disabled={!valid || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? <Spinner /> : null}
            {job ? "Salva" : "Crea il job"}
          </button>
        </>
      }
    >
      {error ? <Alert>{error}</Alert> : null}

      <Field label="Nome del job">
        <input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      </Field>

      <div className="grid-2">
        <Field label="Account Telegram">
          <select
            value={accountId ?? ""}
            disabled={Boolean(job)}
            onChange={(e) => {
              setAccountId(Number(e.target.value));
              setChannelId(null);
            }}
          >
            <option value="" disabled>
              Scegli un account
            </option>
            {(accounts ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Canale privato di destinazione">
          <select
            value={channelId ?? ""}
            disabled={accountId === null}
            onChange={(e) => setChannelId(Number(e.target.value))}
          >
            <option value="" disabled>
              {accountId === null ? "Scegli prima un account" : "Scegli un canale"}
            </option>
            {privateChannels.map((channel) => (
              <option key={channel.id} value={channel.id}>
                {channel.title}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <Field
        label="Cartella locale"
        hint="Percorso interno al container, cosi come e montato nel docker-compose.yml."
      >
        <input
          value={localPath}
          onChange={(e) => setLocalPath(e.target.value)}
          placeholder="/mnt/documenti"
          className="mono"
        />
      </Field>

      <div className="grid-2">
        <Field label="Ogni quante ore" hint="L'attesa parte dalla fine dell'esecuzione precedente.">
          <input
            value={intervalHours}
            onChange={(e) => setIntervalHours(e.target.value.replace(/[^\d.]/g, ""))}
            inputMode="decimal"
          />
        </Field>

        <Field
          label="Velocita di scansione"
          hint="File al secondo. Zero significa nessun limite."
        >
          <input
            value={scanRate}
            onChange={(e) => setScanRate(e.target.value.replace(/\D/g, ""))}
            inputMode="numeric"
          />
        </Field>
      </div>

      <Field
        label="Dimensione massima di una parte (GB)"
        hint={
          account?.is_premium
            ? "Account Premium: il limite di Telegram e circa 3.9 GB per file."
            : "Account senza Premium: il limite di Telegram e 2 GB per file."
        }
      >
        <input
          value={partSize}
          onChange={(e) => setPartSize(e.target.value.replace(/[^\d.]/g, ""))}
          inputMode="decimal"
        />
      </Field>

      <label className="switch">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        <span>Esegui automaticamente secondo l'intervallo</span>
      </label>
    </Modal>
  );
}

function JobCard({ job, onEdit }: { job: Job; onEdit: (job: Job) => void }) {
  const queryClient = useQueryClient();
  const progress = useProgress().jobs.get(job.id);
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["jobs"] });

  const start = useMutation({ mutationFn: () => api.post(`/api/jobs/${job.id}/run`), onSuccess: invalidate });
  const stop = useMutation({ mutationFn: () => api.post(`/api/jobs/${job.id}/stop`), onSuccess: invalidate });
  const remove = useMutation({ mutationFn: () => api.del(`/api/jobs/${job.id}`), onSuccess: invalidate });

  const running = job.status === "running";
  const failed = job.status === "error";

  return (
    <Card>
      <CardHead title={job.name}>
        <div className="row" style={{ gap: 8 }}>
          {running ? (
            <Pill tone="ok" live>
              {phaseLabel(progress?.phase ?? job.phase ?? "")}
            </Pill>
          ) : failed ? (
            <Pill tone="bad">Errore</Pill>
          ) : job.enabled ? (
            <Pill tone="mute">In attesa</Pill>
          ) : (
            <Pill tone="warn">Disattivato</Pill>
          )}

          {running ? (
            <button type="button" className="btn ghost small" onClick={() => stop.mutate()}>
              <Square size={13} />
              Ferma
            </button>
          ) : (
            <button type="button" className="btn small" onClick={() => start.mutate()}>
              <Play size={13} />
              Avvia ora
            </button>
          )}

          <button type="button" className="btn ghost small" onClick={() => onEdit(job)} disabled={running}>
            <Pencil size={13} />
            Modifica
          </button>

          <button
            type="button"
            className="btn danger small"
            disabled={running}
            onClick={() => {
              if (window.confirm(`Eliminare il job ${job.name}? I file su Telegram restano.`)) {
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
          <span className="mono">{job.local_path}</span>
          <span>verso {job.channel_title}</span>
          <span>account {job.account_label}</span>
          <span>ogni {formatInterval(job.interval_hours)}</span>
          <span>parti da {formatBytes(job.part_size_bytes)}</span>
        </div>

        {running && progress ? (
          <JobActivity progress={progress} />
        ) : (
          <>
            <ProgressBar done={job.stats.bytes_uploaded} total={job.stats.bytes_total} />
            <div className="row wrap num" style={{ gap: 20, fontSize: 12.5, color: "var(--muted)" }}>
              <span>
                {job.stats.files_uploaded.toLocaleString("it-IT")} di{" "}
                {job.stats.files_total.toLocaleString("it-IT")} file su Telegram
              </span>
              <span>{formatBytes(job.stats.bytes_uploaded)}</span>
              {job.stats.files_pending > 0 ? (
                <span>{job.stats.files_pending.toLocaleString("it-IT")} in attesa</span>
              ) : null}
              {job.stats.files_error > 0 ? (
                <span style={{ color: "var(--danger)" }}>
                  {job.stats.files_error.toLocaleString("it-IT")} in errore
                </span>
              ) : null}
            </div>
          </>
        )}

        <div className="row wrap" style={{ gap: 24, fontSize: 12, color: "var(--muted)" }}>
          <span>ultima corsa {formatDateTime(job.last_finished_at)}</span>
          <span>prossima {job.enabled ? formatDateTime(job.next_run_at) : "disattivata"}</span>
          <Link to={`/files?job=${job.id}`}>Vedi i file</Link>
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
          Nuovo sync job
        </button>
      </div>

      {isLoading ? (
        <Card>
          <div className="card-body row" style={{ color: "var(--muted)" }}>
            <Spinner /> Caricamento
          </div>
        </Card>
      ) : !data || data.length === 0 ? (
        <Card>
          <Empty
            icon={<CloudUpload size={26} color="var(--muted)" />}
            title="Nessun sync job"
            hint="Un sync job tiene una cartella locale specchiata dentro un canale privato: carica i file nuovi, cancella quelli spariti e ricarica quelli cambiati."
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
