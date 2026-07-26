import { Fragment, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { Download, ExternalLink, FileStack, Hash, Search } from "lucide-react";

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
  uploaded: { text: "On Telegram", tone: "ok" },
  pending: { text: "Pending", tone: "warn" },
  uploading: { text: "Uploading", tone: "warn" },
  stale: { text: "To re-upload", tone: "warn" },
  to_delete: { text: "To delete", tone: "mute" },
  error: { text: "Error", tone: "bad" },
};

/** A message in a private channel opens with t.me/c/<channel id>/<message id>, without the
 *  -100 prefix. It is the same id kept in tg_id. */
function messageLink(channelTgId: number, messageId: number): string {
  return `https://t.me/c/${channelTgId}/${messageId}`;
}

interface ChannelGroup {
  channelId: number;
  channelTgId: number;
  title: string;
  accounts: string[];
  jobNames: string[];
  filesTotal: number;
  filesUploaded: number;
  filesError: number;
  bytesTotal: number;
  bytesUploaded: number;
}

/** Files belong to a job, and every job writes to a channel. To show them per channel, the
 *  jobs sharing the same destination are grouped together. */
function groupByChannel(jobs: Job[]): ChannelGroup[] {
  const groups = new Map<number, ChannelGroup>();
  for (const job of jobs) {
    let group = groups.get(job.channel_id);
    if (!group) {
      group = {
        channelId: job.channel_id,
        channelTgId: job.channel_tg_id,
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
      <CardHead title="Restores in progress" />
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
                  ? "Completed"
                  : restore.phase === "error"
                    ? "Error"
                    : "Running"}
              </Pill>
            </div>
            <div className="mono truncate" style={{ color: "var(--muted)" }}>
              {restore.target_path}
            </div>
            <ProgressBar done={restore.bytes_done} total={restore.bytes_total} />
            <div className="row wrap num" style={{ gap: 20, fontSize: 12.5, color: "var(--muted)" }}>
              <span>
                {formatBytes(restore.bytes_done)} of {formatBytes(restore.bytes_total)}
              </span>
              <span>{formatSpeed(restore.speed_bps)}</span>
              <span>{formatDuration(restore.eta_seconds)} left</span>
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
              {group.filesUploaded.toLocaleString("en-US")} of{" "}
              {group.filesTotal.toLocaleString("en-US")} files, {formatBytes(group.bytesUploaded)}
            </div>

            <ProgressBar done={group.filesUploaded} total={group.filesTotal} />

            <div className="channel-meta">
              {group.jobNames.join(", ")} on {group.accounts.join(", ")}
              <span className="num"> — {done.toFixed(done >= 99.5 ? 1 : 0)} per cent</span>
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

  // The chosen channel lives in the query string, so the link stays shareable.
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
          title="No channel with files"
          hint="Channels show up here when a sync job uses them as a destination."
        />
      </Card>
    );
  }

  return (
    <>
      <RestorePanel />

      <div>
        <span className="section-label">Destination channels</span>
        <div style={{ marginTop: 10 }}>
          <ChannelPicker groups={groups} selected={selected} onSelect={selectChannel} />
        </div>
      </div>

      <Card>
        <CardHead title={current ? current.title : "Files"}>
          <span className="num" style={{ color: "var(--muted)", fontSize: 12.5 }}>
            {total.toLocaleString("en-US")} files
          </span>
        </CardHead>

        <div className="card-body">
          <div className="row wrap">
            <div className="row" style={{ flex: 1, minWidth: 220, gap: 8 }}>
              <Search size={16} color="var(--muted)" />
              <input
                value={search}
                placeholder="Search by path or name in this channel"
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
              <option value="">All states</option>
              <option value="uploaded">On Telegram</option>
              <option value="pending">Pending</option>
              <option value="error">In error</option>
            </select>

            <button
              type="button"
              className="btn ghost"
              onClick={() => {
                setQuery(search.trim());
                setPage(0);
              }}
            >
              Search
            </button>
          </div>

          {restore.isError ? <Alert>{(restore.error as Error).message}</Alert> : null}
          {restore.isSuccess ? (
            <Alert tone="info">
              Restore started. The rebuilt file will be at {restore.data.target_path} inside
              the container, that is in the project data folder.
            </Alert>
          ) : null}
        </div>

        {isLoading ? (
          <div className="card-body row" style={{ color: "var(--muted)" }}>
            <Spinner /> Loading
          </div>
        ) : !data || data.items.length === 0 ? (
          <Empty
            icon={<FileStack size={26} color="var(--muted)" />}
            title="No files in this channel"
            hint="Files show up after the first scan of the sync job writing here."
          />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Path</th>
                  <th>State</th>
                  <th className="right">Size</th>
                  <th className="right">Parts</th>
                  <th>Uploaded on</th>
                  <th className="right">Actions</th>
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
                            <span className="section-label">
                              {entry.parts.length === 1
                                ? "Message on Telegram"
                                : `${entry.parts.length} parts on Telegram, in order`}
                            </span>
                            <div className="row wrap mono" style={{ gap: 14, marginTop: 8 }}>
                              {entry.parts.map((part) => {
                                const label = (
                                  <>
                                    {entry.parts.length > 1
                                      ? `part ${part.part_index + 1}: `
                                      : ""}
                                    message {part.message_id}
                                    <span style={{ color: "var(--muted)" }}>
                                      {" "}
                                      {formatBytes(part.size)}
                                    </span>
                                  </>
                                );
                                // Without the channel id the link would be broken: plain
                                // text beats a link that leads nowhere.
                                if (!current?.channelTgId) {
                                  return (
                                    <span
                                      key={part.part_index}
                                      style={{ color: "var(--muted)" }}
                                    >
                                      {label}
                                    </span>
                                  );
                                }
                                return (
                                  <a
                                    key={part.part_index}
                                    className="part-link"
                                    href={messageLink(current.channelTgId, part.message_id)}
                                    target="_blank"
                                    rel="noreferrer"
                                    onClick={(event) => event.stopPropagation()}
                                  >
                                    <ExternalLink size={11} />
                                    {label}
                                  </a>
                                );
                              })}
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
              Previous
            </button>
            <span className="num" style={{ color: "var(--muted)" }}>
              page {page + 1} of {pages}
            </span>
            <button
              type="button"
              className="btn ghost small"
              disabled={page + 1 >= pages}
              onClick={() => setPage((current) => current + 1)}
            >
              Next
            </button>
          </div>
        ) : null}
      </Card>
    </>
  );
}
