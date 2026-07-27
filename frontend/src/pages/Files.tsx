import { Fragment, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { Download, ExternalLink, FileStack, Search } from "lucide-react";

import { api } from "../lib/api";
import { groupByChannel } from "../lib/channels";
import { formatBytes, formatDateTime } from "../lib/format";
import type { FileEntry, FilePage, Job, RestoreOut } from "../lib/types";
import ChannelPicker from "../components/ChannelPicker";
import RestorePanel from "../components/RestorePanel";
import {
  Alert,
  Card,
  CardHead,
  Empty,
  Pill,
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
