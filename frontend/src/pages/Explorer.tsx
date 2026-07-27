import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import {
  ArrowLeft,
  ArrowRight,
  ChevronRight,
  CornerLeftUp,
  Download,
  File,
  Folder,
  FolderTree,
  Hash,
} from "lucide-react";

import { api } from "../lib/api";
import { groupByChannel } from "../lib/channels";
import { formatBytes, formatDateTime } from "../lib/format";
import type { DownloadTicket, ExplorerListing, Job } from "../lib/types";
import ChannelPicker from "../components/ChannelPicker";
import { Alert, Card, CardHead, Empty, Spinner } from "../components/ui";

const PAGE_SIZE = 500;

export default function Explorer() {
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState(0);

  // Where the two arrows go. The browser history is not used for this: the address bar
  // holds the folder so a link stays shareable, but going back in the browser has to
  // leave the explorer, not walk back through the folders opened inside it.
  const [history, setHistory] = useState<string[]>([params.get("path") ?? ""]);
  const [cursor, setCursor] = useState(0);
  const crumbs = useRef<HTMLDivElement>(null);

  const { data: jobs } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.get<Job[]>("/api/jobs"),
    refetchInterval: 30000,
  });

  const groups = useMemo(() => groupByChannel(jobs ?? []), [jobs]);

  const fromUrl = params.get("channel");
  const selected =
    fromUrl !== null && groups.some((group) => group.channelId === Number(fromUrl))
      ? Number(fromUrl)
      : (groups[0]?.channelId ?? null);
  const path = params.get("path") ?? "";
  const segments = path ? path.split("/") : [];

  const goTo = (channelId: number | null, next: string) => {
    const updated = new URLSearchParams(params);
    if (channelId !== null) updated.set("channel", String(channelId));
    if (next) updated.set("path", next);
    else updated.delete("path");
    setParams(updated);
    setPage(0);
  };

  // Anything that changes the folder other than the two arrows lands here: a folder
  // opened, a crumb, or an address pasted by hand. The forward branch is dropped, which
  // is what every file manager does.
  useEffect(() => {
    if (history[cursor] === path) return;
    setHistory((previous) => [...previous.slice(0, cursor + 1), path]);
    setCursor((value) => value + 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  // The end of the path is the part that matters, and it is the part that falls off the
  // screen on a phone.
  useEffect(() => {
    const node = crumbs.current;
    if (node) node.scrollLeft = node.scrollWidth;
  }, [path, selected]);

  const step = (delta: number) => {
    const next = cursor + delta;
    if (next < 0 || next >= history.length) return;
    setCursor(next);
    goTo(selected, history[next]);
  };

  const chooseChannel = (channelId: number) => {
    setHistory([""]);
    setCursor(0);
    goTo(channelId, "");
  };

  const query = new URLSearchParams();
  query.set("channel_id", String(selected ?? 0));
  query.set("path", path);
  query.set("offset", String(page * PAGE_SIZE));
  query.set("limit", String(PAGE_SIZE));

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["explorer", query.toString()],
    queryFn: () => api.get<ExplorerListing>(`/api/explorer/list?${query.toString()}`),
    enabled: selected !== null,
    // The previous folder stays on screen while the next one loads: without this every
    // click empties the list for an instant and the page jumps.
    placeholderData: (previous) => previous,
  });

  // The ticket is minted first and the browser then navigates to it: a download is a
  // plain navigation, which carries no Authorization header.
  const download = useMutation({
    mutationFn: (fileId: number) =>
      api.post<DownloadTicket>("/api/explorer/ticket", { file_id: fileId }),
    onSuccess: (ticket) => window.location.assign(ticket.url),
  });
  const downloading = download.isPending ? (download.variables as number) : null;

  const title = data?.channel_title ?? groups.find((g) => g.channelId === selected)?.title;
  const pages = data ? Math.ceil(data.entries_total / PAGE_SIZE) : 0;
  const empty = !data || (data.folders.length === 0 && data.files.length === 0);

  if (groups.length === 0) {
    return (
      <Card>
        <Empty
          icon={<FolderTree size={26} color="var(--muted)" />}
          title="No channel to explore"
          hint="Channels show up here when a sync job uses them as a destination, or when an index arrives through Import."
        />
      </Card>
    );
  }

  return (
    <>
      <div>
        <span className="section-label">Channels</span>
        <div style={{ marginTop: 10 }}>
          <ChannelPicker groups={groups} selected={selected} onSelect={chooseChannel} />
        </div>
      </div>

      <Card>
        <CardHead title={title ?? "Explorer"}>
          <span className="num" style={{ color: "var(--muted)", fontSize: 12.5 }}>
            {isFetching && !isLoading ? (
              <Spinner size={13} />
            ) : data ? (
              <>
                {data.files_total.toLocaleString("en-US")} files here and below,{" "}
                {formatBytes(data.bytes_total)}
              </>
            ) : null}
          </span>
        </CardHead>

        <div className="card-body">
          <div className="explorer-bar">
            <button
              type="button"
              className="icon-btn"
              disabled={cursor === 0}
              onClick={() => step(-1)}
              aria-label="Back"
              title="Back"
            >
              <ArrowLeft size={17} />
            </button>
            <button
              type="button"
              className="icon-btn"
              disabled={cursor >= history.length - 1}
              onClick={() => step(1)}
              aria-label="Forward"
              title="Forward"
            >
              <ArrowRight size={17} />
            </button>
            <button
              type="button"
              className="icon-btn"
              disabled={segments.length === 0}
              onClick={() => goTo(selected, segments.slice(0, -1).join("/"))}
              aria-label="Parent folder"
              title="Parent folder"
            >
              <CornerLeftUp size={17} />
            </button>

            <div className="explorer-crumbs" ref={crumbs}>
              <button
                type="button"
                className="explorer-crumb"
                onClick={() => goTo(selected, "")}
                disabled={segments.length === 0}
              >
                <Hash size={13} />
                {title ?? "Channel"}
              </button>
              {segments.map((segment, index) => (
                <Fragment key={`${segment}-${index}`}>
                  <ChevronRight size={13} className="explorer-sep" />
                  <button
                    type="button"
                    className="explorer-crumb"
                    disabled={index === segments.length - 1}
                    onClick={() => goTo(selected, segments.slice(0, index + 1).join("/"))}
                  >
                    {segment}
                  </button>
                </Fragment>
              ))}
            </div>
          </div>

          <p className="explorer-note">
            Folders and files come from the index in the database, so browsing costs
            nothing. Telegram is contacted only when a file is downloaded, and a file
            split into several parts arrives as one.
          </p>

          {download.isError ? <Alert>{(download.error as Error).message}</Alert> : null}
        </div>

        {isLoading ? (
          <div className="card-body row" style={{ color: "var(--muted)" }}>
            <Spinner /> Reading the index
          </div>
        ) : empty ? (
          <Empty
            icon={<FolderTree size={26} color="var(--muted)" />}
            title={path ? "Empty folder" : "Nothing in this channel yet"}
            hint={
              path
                ? "Everything that was here has been removed from the index, or it is still waiting to be uploaded."
                : "Files show up after the first run of a sync job writing here."
            }
          />
        ) : (
          <div className="explorer">
            {segments.length > 0 ? (
              <button
                type="button"
                className="explorer-row"
                onClick={() => goTo(selected, segments.slice(0, -1).join("/"))}
              >
                <CornerLeftUp size={16} className="explorer-icon" />
                <span className="explorer-name muted">Parent folder</span>
              </button>
            ) : null}

            {data.folders.map((folder) => (
              <button
                key={`folder:${folder.path}`}
                type="button"
                className="explorer-row"
                onClick={() => goTo(selected, folder.path)}
              >
                <Folder size={16} className="explorer-icon folder" />
                <span className="explorer-name">
                  {folder.name}
                  <span className="explorer-sub num">
                    {folder.files.toLocaleString("en-US")}{" "}
                    {folder.files === 1 ? "file" : "files"}
                  </span>
                </span>
                <span className="explorer-size num">{formatBytes(folder.bytes)}</span>
                <span className="explorer-date" />
                <span className="explorer-action" />
              </button>
            ))}

            {data.files.map((file) => (
              <div key={`file:${file.id}`} className="explorer-row static">
                <File size={16} className="explorer-icon" />
                <span className="explorer-name">
                  {file.name}
                  {file.parts > 1 ? (
                    <span className="explorer-sub num">
                      {file.parts} parts, joined while downloading
                    </span>
                  ) : null}
                </span>
                <span className="explorer-size num">{formatBytes(file.size)}</span>
                <span className="explorer-date num">{formatDateTime(file.uploaded_at)}</span>
                <span className="explorer-action">
                  <button
                    type="button"
                    className="btn ghost small"
                    disabled={downloading !== null}
                    onClick={() => download.mutate(file.id)}
                  >
                    {downloading === file.id ? <Spinner size={13} /> : <Download size={13} />}
                    Download
                  </button>
                </span>
              </div>
            ))}
          </div>
        )}

        {pages > 1 ? (
          <div className="card-body row" style={{ justifyContent: "center" }}>
            <button
              type="button"
              className="btn ghost small"
              disabled={page === 0}
              onClick={() => setPage((value) => value - 1)}
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
              onClick={() => setPage((value) => value + 1)}
            >
              Next
            </button>
          </div>
        ) : null}
      </Card>
    </>
  );
}
