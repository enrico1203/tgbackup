import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  CheckCircle2,
  Download,
  FileUp,
  Hash,
  PackageOpen,
  Upload,
} from "lucide-react";

import { api, download, saveBlob, upload } from "../lib/api";
import { formatBytes, formatDateTime } from "../lib/format";
import type { Account, ExportChannel, ImportPreview, ImportResult } from "../lib/types";
import { Alert, Card, CardHead, Empty, Field, Pill, Spinner } from "../components/ui";

function ExportCard() {
  const { data, isLoading } = useQuery({
    queryKey: ["export-channels"],
    queryFn: () => api.get<ExportChannel[]>("/api/export/channels"),
  });

  const save = useMutation({
    mutationFn: async (channelId: number) => {
      const file = await download(`/api/export/channels/${channelId}`);
      saveBlob(file.blob, file.filename);
    },
  });

  return (
    <Card>
      <CardHead title="Export a channel" />
      <div className="card-body">
        <p style={{ margin: 0, color: "var(--muted)", maxWidth: "78ch" }}>
          The export holds the index of one channel: its coordinates, the jobs writing to it
          and, for every file, path, size, modification time and the message ids of its parts.
          That is everything a restore needs, so another instance can rebuild the files from
          the channel. No file content travels, and no Telegram credentials either: the
          sessions stay on this machine.
        </p>

        {save.isError ? <Alert>{(save.error as Error).message}</Alert> : null}

        {isLoading ? (
          <div className="row" style={{ color: "var(--muted)" }}>
            <Spinner /> Loading
          </div>
        ) : !data || data.length === 0 ? (
          <Empty
            icon={<PackageOpen size={26} color="var(--muted)" />}
            title="No channel to export"
            hint="A channel shows up here once a sync job uses it as a destination."
          />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Channel</th>
                  <th>Account</th>
                  <th className="right">Jobs</th>
                  <th className="right">Files</th>
                  <th className="right">Messages</th>
                  <th className="right">Size</th>
                  <th className="right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {data.map((channel) => (
                  <tr key={channel.channel_id}>
                    <td>
                      <div className="row" style={{ gap: 8 }}>
                        <Hash size={14} style={{ flexShrink: 0, opacity: 0.65 }} />
                        <span style={{ fontWeight: 500 }}>{channel.title}</span>
                      </div>
                      <div className="mono" style={{ color: "var(--muted)" }}>
                        {channel.tg_id}
                      </div>
                    </td>
                    <td>{channel.account_label}</td>
                    <td className="right num">{channel.jobs}</td>
                    <td className="right num">{channel.files.toLocaleString("en-US")}</td>
                    <td className="right num">{channel.parts.toLocaleString("en-US")}</td>
                    <td className="right num">{formatBytes(channel.bytes_total)}</td>
                    <td className="right">
                      <button
                        type="button"
                        className="btn ghost small"
                        disabled={save.isPending}
                        onClick={() => save.mutate(channel.channel_id)}
                      >
                        {save.isPending && save.variables === channel.channel_id ? (
                          <Spinner size={13} />
                        ) : (
                          <Download size={13} />
                        )}
                        Export
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  );
}

function ImportResultPanel({ result }: { result: ImportResult }) {
  return (
    <>
      <Alert tone="info">
        {result.channel_created
          ? `Channel ${result.channel_title} added to this instance. `
          : `Channel ${result.channel_title} was already here, the index was added to it. `}
        {result.files_imported.toLocaleString("en-US")} files and{" "}
        {result.parts_imported.toLocaleString("en-US")} messages imported
        {result.files_skipped > 0
          ? `, ${result.files_skipped.toLocaleString("en-US")} already known and left alone`
          : ""}
        . The imported jobs are disabled: check that the source of each one points at the
        right folder or remote on this machine before enabling it.
      </Alert>

      {result.warnings.map((warning) => (
        <Alert key={warning}>{warning}</Alert>
      ))}

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Job</th>
              <th>Outcome</th>
              <th className="right">Files</th>
              <th className="right">Skipped</th>
              <th className="right">Messages</th>
            </tr>
          </thead>
          <tbody>
            {result.jobs.map((job) => (
              <tr key={job.name}>
                <td style={{ fontWeight: 500 }}>{job.name}</td>
                <td>
                  <Pill tone={job.action === "created" ? "ok" : "accent"}>
                    {job.action === "created" ? "Created" : "Merged"}
                  </Pill>
                </td>
                <td className="right num">{job.files_imported.toLocaleString("en-US")}</td>
                <td className="right num">{job.files_skipped.toLocaleString("en-US")}</td>
                <td className="right num">{job.parts_imported.toLocaleString("en-US")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function ImportCard() {
  const queryClient = useQueryClient();
  const picker = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [merge, setMerge] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);

  const { data: accounts } = useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.get<Account[]>("/api/accounts"),
  });

  const preview = useMutation({
    mutationFn: (chosen: File) => {
      const form = new FormData();
      form.append("file", chosen);
      return upload<ImportPreview>("/api/export/preview", form);
    },
  });

  const runImport = useMutation({
    mutationFn: (target: number) => {
      const form = new FormData();
      form.append("file", file as File);
      form.append("account_id", String(target));
      form.append("merge", merge ? "true" : "false");
      return upload<ImportResult>("/api/export/import", form);
    },
    onSuccess: (imported) => {
      setResult(imported);
      setFile(null);
      preview.reset();
      if (picker.current) picker.current.value = "";
      // The channel, its jobs and its files are all new to this instance.
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      void queryClient.invalidateQueries({ queryKey: ["accounts"] });
      void queryClient.invalidateQueries({ queryKey: ["export-channels"] });
    },
  });

  const choose = (chosen: File | null) => {
    setResult(null);
    runImport.reset();
    setFile(chosen);
    preview.reset();
    if (chosen) preview.mutate(chosen);
  };

  // Preselected instead of left empty: on most instances there is one account, and the
  // choice only matters when there are several.
  const target = accountId ?? accounts?.[0]?.id ?? null;
  const account = accounts?.find((item) => item.id === target);

  return (
    <Card>
      <CardHead title="Import into this instance" />
      <div className="card-body">
        <p style={{ margin: 0, color: "var(--muted)", maxWidth: "78ch" }}>
          Load the file exported from the other machine. The channel is linked to a Telegram
          account of this instance, which has to be a member of it, and the jobs arrive
          disabled: their source is a folder of the machine they came from.
        </p>

        <div className="row wrap">
          <input
            ref={picker}
            type="file"
            accept=".gz,.json,application/gzip,application/json"
            style={{ display: "none" }}
            onChange={(event) => choose(event.target.files?.[0] ?? null)}
          />
          <button type="button" className="btn ghost" onClick={() => picker.current?.click()}>
            <FileUp size={14} />
            Choose the export file
          </button>
          {file ? (
            <span className="mono truncate" style={{ color: "var(--muted)" }}>
              {file.name} — {formatBytes(file.size)}
            </span>
          ) : null}
        </div>

        {preview.isPending ? (
          <div className="row" style={{ color: "var(--muted)" }}>
            <Spinner /> Reading the file
          </div>
        ) : null}
        {preview.isError ? <Alert>{(preview.error as Error).message}</Alert> : null}
        {runImport.isError ? <Alert>{(runImport.error as Error).message}</Alert> : null}

        {preview.data ? (
          <>
            <div className="stat-grid">
              <div className="stat">
                <span className="stat-label">Channel</span>
                <span className="stat-value" style={{ fontSize: 18 }}>
                  {preview.data.channel_title}
                </span>
                <span className="stat-hint mono">{preview.data.channel_tg_id}</span>
              </div>
              <div className="stat">
                <span className="stat-label">Files</span>
                <span className="stat-value num">
                  {preview.data.files.toLocaleString("en-US")}
                </span>
                <span className="stat-hint num">
                  {preview.data.parts.toLocaleString("en-US")} messages,{" "}
                  {formatBytes(preview.data.bytes_total)}
                </span>
              </div>
              <div className="stat">
                <span className="stat-label">Exported</span>
                <span className="stat-value" style={{ fontSize: 18 }}>
                  {formatDateTime(preview.data.exported_at)}
                </span>
                <span className="stat-hint">from the account {preview.data.account_label}</span>
              </div>
            </div>

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Job in the file</th>
                    <th>Source on the other machine</th>
                    <th className="right">Files</th>
                    <th className="right">Size</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.data.jobs.map((job) => (
                    <tr key={job.name}>
                      <td style={{ fontWeight: 500 }}>{job.name}</td>
                      <td className="mono truncate" style={{ color: "var(--muted)" }}>
                        {job.source_type === "rclone" ? "rclone " : ""}
                        {job.source}
                      </td>
                      <td className="right num">{job.files.toLocaleString("en-US")}</td>
                      <td className="right num">{formatBytes(job.bytes_total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <Field
              label="Telegram account that will hold the channel"
              hint="It has to be a member of the channel: the access_hash is issued per account, and the one in the file only works for the account that produced it."
            >
              <select
                value={target ?? ""}
                onChange={(event) => setAccountId(Number(event.target.value))}
              >
                {(accounts ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                    {item.connected ? "" : " (not connected)"}
                  </option>
                ))}
              </select>
            </Field>

            <label className="switch">
              <input
                type="checkbox"
                checked={merge}
                onChange={(event) => setMerge(event.target.checked)}
              />
              <span>
                Merge into jobs of this channel with the same name, instead of creating new
                ones. Files already known are left untouched.
              </span>
            </label>

            {account && !account.connected ? (
              <Alert tone="info">
                The account {account.label} is not connected: the channel cannot be verified
                against Telegram right now, and the coordinates in the file will be used as
                they are.
              </Alert>
            ) : null}

            <div className="row">
              <button
                type="button"
                className="btn"
                disabled={!file || target === null || runImport.isPending}
                onClick={() => target !== null && runImport.mutate(target)}
              >
                {runImport.isPending ? <Spinner /> : <Upload size={14} />}
                Import into this instance
              </button>
              <button type="button" className="btn ghost" onClick={() => choose(null)}>
                Cancel
              </button>
            </div>
          </>
        ) : null}

        {result ? <ImportResultPanel result={result} /> : null}
      </div>
    </Card>
  );
}

export default function Export() {
  return (
    <>
      <Card>
        <div className="card-body">
          <div className="row wrap" style={{ gap: 12, color: "var(--muted)" }}>
            <Pill tone="mute">
              <CheckCircle2 size={11} />
              Index only
            </Pill>
            <span>
              Export a channel here, carry the file to the other machine, import it there.
              The files stay on Telegram the whole time.
            </span>
            <span className="row" style={{ gap: 8, marginLeft: "auto" }}>
              <Download size={14} /> <ArrowRight size={14} /> <Upload size={14} />
            </span>
          </div>
        </div>
      </Card>

      <ExportCard />
      <ImportCard />
    </>
  );
}
