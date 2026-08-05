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
  FolderSearch,
  FolderTree,
  Hash,
  HardDrive,
  History,
  Laptop,
  Search,
  Trash2,
  X,
} from "lucide-react";

import { api } from "../lib/api";
import { groupByChannel } from "../lib/channels";
import { formatBytes, formatDateTime } from "../lib/format";
import type {
  DownloadTicket,
  ExplorerFile,
  ExplorerFolder,
  ExplorerListing,
  Job,
  RcloneStatus,
  RestoreFolderOut,
  RestoreOut,
} from "../lib/types";
import ChannelPicker from "../components/ChannelPicker";
import RemoteBrowser from "../components/RemoteBrowser";
import RestorePanel from "../components/RestorePanel";
import { Alert, Card, CardHead, Empty, Field, Modal, Spinner } from "../components/ui";

const PAGE_SIZE = 500;

// Where the last download went. Typing a path again for every file is the kind of small
// friction that makes a feature go unused.
const LAST_LOCAL = "tgbackup.explorer.local";
const LAST_REMOTE = "tgbackup.explorer.remote";

type Destination = "browser" | "local" | "rclone";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

// The end of the chosen day, in the timezone of whoever is looking: "as it was on the
// 20th" means after everything that happened on the 20th, not at midnight before it.
function endOfDay(day: string): string {
  const date = new Date(`${day}T23:59:59`);
  return Number.isNaN(date.getTime()) ? new Date().toISOString() : date.toISOString();
}

export default function Explorer() {
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState(0);

  // Where the two arrows go. The browser history is not used for this: the address bar
  // holds the folder so a link stays shareable, but going back in the browser has to
  // leave the explorer, not walk back through the folders opened inside it.
  const [history, setHistory] = useState<string[]>([params.get("path") ?? ""]);
  const [cursor, setCursor] = useState(0);
  const [term, setTerm] = useState(params.get("q") ?? "");
  const crumbs = useRef<HTMLDivElement>(null);

  // The file whose destination is being chosen, and the choice itself.
  const [chosen, setChosen] = useState<ExplorerFile | null>(null);
  // A folder is restored through the same dialog, minus the browser: a folder is not
  // something that can be streamed into a download.
  const [chosenFolder, setChosenFolder] = useState<ExplorerFolder | null>(null);
  const [destination, setDestination] = useState<Destination>("browser");
  const [localPath, setLocalPath] = useState(() => localStorage.getItem(LAST_LOCAL) ?? "");
  const [remote, setRemote] = useState(() => localStorage.getItem(LAST_REMOTE) ?? "");
  const [browsing, setBrowsing] = useState<string | null>(null);

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
  const search = params.get("q") ?? "";
  const segments = path ? path.split("/") : [];

  // What the listing is looking at: the channel as it is, what the trash still holds, or
  // the channel as it stood at the end of a day. All three live in the address, so a view
  // of last Tuesday is a link somebody can keep.
  const view = (params.get("view") ?? "current") as "current" | "trash" | "asof";
  const asOfDay = params.get("as_of") ?? "";

  const setView = (next: "current" | "trash" | "asof", day?: string) => {
    const updated = new URLSearchParams(params);
    if (next === "current") {
      updated.delete("view");
      updated.delete("as_of");
    } else {
      updated.set("view", next);
      if (next === "asof") updated.set("as_of", day || asOfDay || today());
      else updated.delete("as_of");
    }
    setParams(updated);
    setPage(0);
  };

  // Opening a folder ends the search, the way it does in a file manager: the results
  // came from anywhere below, and staying in them while the folder changes underneath
  // would show matches that have nothing to do with where you now are.
  const goTo = (channelId: number | null, next: string) => {
    const updated = new URLSearchParams(params);
    if (channelId !== null) updated.set("channel", String(channelId));
    if (next) updated.set("path", next);
    else updated.delete("path");
    updated.delete("q");
    setParams(updated);
    setPage(0);
  };

  const runSearch = (value: string) => {
    const updated = new URLSearchParams(params);
    const trimmed = value.trim();
    if (trimmed) updated.set("q", trimmed);
    else updated.delete("q");
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

  // The box follows the address: opening a folder or a shared link clears what was
  // typed, rather than leaving a word in the field that is no longer searched for.
  useEffect(() => {
    setTerm(search);
  }, [search]);

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
  if (search) query.set("q", search);
  if (view === "trash") query.set("trash", "true");
  if (view === "asof" && asOfDay) query.set("as_of", endOfDay(asOfDay));
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
    onSuccess: (ticket) => {
      window.location.assign(ticket.url);
      setChosen(null);
    },
  });

  // The other two destinations are written by the backend, and the transfer shows up in
  // the panel at the top of the page like any other rebuild.
  const fetchTo = useMutation({
    mutationFn: (body: { file_id: number; dest_type: Destination; path: string }) =>
      api.post<RestoreOut>("/api/explorer/fetch", body),
    onSuccess: (_result, body) => {
      if (body.dest_type === "local") localStorage.setItem(LAST_LOCAL, body.path);
      else localStorage.setItem(LAST_REMOTE, body.path);
      setChosen(null);
    },
  });

  const fetchFolder = useMutation({
    mutationFn: (body: {
      channel_id: number;
      path: string;
      dest_type: Destination;
      path_to: string;
    }) => api.post<RestoreFolderOut>("/api/explorer/fetch-folder", body),
    onSuccess: (_result, body) => {
      if (body.dest_type === "local") localStorage.setItem(LAST_LOCAL, body.path_to);
      else localStorage.setItem(LAST_REMOTE, body.path_to);
      setChosenFolder(null);
    },
  });

  const { data: rcloneStatus } = useQuery({
    queryKey: ["rclone-status"],
    queryFn: () => api.get<RcloneStatus>("/api/rclone"),
    enabled: chosen !== null || chosenFolder !== null,
  });

  const start = () => {
    const where = destination === "local" ? localPath.trim() : remote.trim();
    if (chosenFolder) {
      fetchFolder.mutate({
        channel_id: selected ?? 0,
        path: chosenFolder.path,
        dest_type: destination,
        path_to: where,
      });
      return;
    }
    if (!chosen) return;
    if (destination === "browser") {
      download.mutate(chosen.id);
      return;
    }
    fetchTo.mutate({ file_id: chosen.id, dest_type: destination, path: where });
  };

  const busy = download.isPending || fetchTo.isPending || fetchFolder.isPending;

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
      <RestorePanel />

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

          <div className="explorer-search">
            <Search size={16} color="var(--muted)" style={{ flexShrink: 0 }} />
            <input
              value={term}
              placeholder={
                path ? `Search in ${segments[segments.length - 1]} and below` : "Search this channel"
              }
              onChange={(event) => setTerm(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") runSearch(term);
                if (event.key === "Escape") runSearch("");
              }}
            />
            {search ? (
              <button
                type="button"
                className="icon-btn"
                onClick={() => runSearch("")}
                aria-label="Clear the search"
                title="Clear the search"
              >
                <X size={16} />
              </button>
            ) : null}
            <button type="button" className="btn ghost small" onClick={() => runSearch(term)}>
              Search
            </button>
          </div>

          {search && data ? (
            <p className="explorer-note">
              {data.entries_total.toLocaleString("en-US")}{" "}
              {data.entries_total === 1 ? "match" : "matches"} for{" "}
              <span style={{ color: "var(--text)" }}>{search}</span>
              {path ? (
                <>
                  {" "}
                  in <span className="mono">{path}</span> and below
                </>
              ) : (
                " in the whole channel"
              )}
              . Opening a folder leaves the results.
            </p>
          ) : null}

          <div className="row wrap" style={{ gap: 8 }}>
            <button
              type="button"
              className={view === "current" ? "btn small" : "btn ghost small"}
              onClick={() => setView("current")}
            >
              <FolderTree size={13} />
              Now
            </button>
            <button
              type="button"
              className={view === "trash" ? "btn small" : "btn ghost small"}
              onClick={() => setView("trash")}
            >
              <Trash2 size={13} />
              Trash
            </button>
            <button
              type="button"
              className={view === "asof" ? "btn small" : "btn ghost small"}
              onClick={() => setView("asof")}
            >
              <History size={13} />
              As it was
            </button>
            {view === "asof" ? (
              <input
                type="date"
                value={asOfDay}
                max={today()}
                onChange={(event) => setView("asof", event.target.value)}
                style={{ width: 170 }}
              />
            ) : null}
          </div>

          {view === "current" ? (
            <p className="explorer-note">
              Folders and files come from the index in the database, so browsing costs
              nothing. Telegram is contacted only when a file is downloaded, and a file
              split into several parts arrives as one.
            </p>
          ) : view === "trash" ? (
            <p className="explorer-note">
              Files that are no longer at the source and whose messages are still in the
              channel. They can be downloaded exactly like the others until the retention
              of their job runs out, and they come back on their own, at no cost, if the
              file returns to the source unchanged. A job with no retention set deletes
              straight away and never fills this.
            </p>
          ) : (
            <p className="explorer-note">
              The channel as it stood at the end of that day: everything uploaded by then
              that had not yet been deleted. A file modified since shows its current
              content, because a modification replaces the messages of the old version.
            </p>
          )}

          {download.isError ? <Alert>{(download.error as Error).message}</Alert> : null}
        </div>

        {isLoading ? (
          <div className="card-body row" style={{ color: "var(--muted)" }}>
            <Spinner /> Reading the index
          </div>
        ) : empty ? (
          <Empty
            icon={<FolderTree size={26} color="var(--muted)" />}
            title={
              view === "trash"
                ? "The trash is empty"
                : view === "asof"
                  ? "Nothing was here on that day"
                  : path
                    ? "Empty folder"
                    : "Nothing in this channel yet"
            }
            hint={
              view === "trash"
                ? "Nothing has disappeared from the source, or the jobs writing here delete straight away instead of keeping a trash. The retention is set on the job."
                : view === "asof"
                  ? "Either nothing had been uploaded by then, or everything that had is a file that arrived later."
                  : path
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
              <div key={`folder:${folder.path}`} className="explorer-row static">
                {/* The name opens the folder, the button restores it. Two actions on one
                    row, so the row itself can no longer be the button. */}
                <Folder size={16} className="explorer-icon folder" />
                <button
                  type="button"
                  className="explorer-name as-link"
                  onClick={() => goTo(selected, folder.path)}
                >
                  {folder.name}
                  <span className={search ? "explorer-sub mono" : "explorer-sub num"}>
                    {search
                      ? folder.path
                      : `${folder.files.toLocaleString("en-US")} ${folder.files === 1 ? "file" : "files"}`}
                  </span>
                </button>
                <span className="explorer-size num">{formatBytes(folder.bytes)}</span>
                <span className="explorer-date" />
                <span className="explorer-action">
                  <button
                    type="button"
                    className="btn ghost small"
                    onClick={() => {
                      setChosenFolder(folder);
                      // The browser cannot take a folder: the choice starts on the
                      // destination that can.
                      setDestination(destination === "browser" ? "local" : destination);
                      fetchFolder.reset();
                    }}
                    title="Write this folder and everything below it to a destination on the server"
                  >
                    <Download size={13} />
                    Restore
                  </button>
                </span>
              </div>
            ))}

            {data.files.map((file) => (
              <div key={`file:${file.id}`} className="explorer-row static">
                <File size={16} className="explorer-icon" />
                <span className="explorer-name">
                  {file.name}
                  {/* In a listing the folder is the one you are in and saying so twice
                      helps nobody. In results it is the only way to tell two files with
                      the same name apart. */}
                  {search ? (
                    <span className="explorer-sub mono">
                      {file.path.includes("/") ? file.path.slice(0, file.path.lastIndexOf("/")) : "."}
                    </span>
                  ) : file.trashed_at ? (
                    <span className="explorer-sub num">
                      gone from the source {formatDateTime(file.trashed_at)}
                      {file.purge_at ? `, deleted for good ${formatDateTime(file.purge_at)}` : ""}
                    </span>
                  ) : file.parts > 1 ? (
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
                    onClick={() => {
                      setChosen(file);
                      download.reset();
                      fetchTo.reset();
                    }}
                  >
                    <Download size={13} />
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

      {chosen || chosenFolder ? (
        <Modal
          title={
            chosenFolder ? `Restore ${chosenFolder.name}` : `Download ${chosen?.name ?? ""}`
          }
          onClose={() => {
            setChosen(null);
            setChosenFolder(null);
          }}
          footer={
            <>
              <button
                type="button"
                className="btn ghost"
                onClick={() => {
                  setChosen(null);
                  setChosenFolder(null);
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn"
                disabled={
                  busy ||
                  (destination === "local" && !localPath.trim()) ||
                  (destination === "rclone" && !remote.includes(":"))
                }
                onClick={start}
              >
                {busy ? <Spinner size={14} /> : <Download size={14} />}
                {destination === "browser" ? "Download" : "Write it there"}
              </button>
            </>
          }
        >
          <div className="row wrap" style={{ gap: 14, color: "var(--muted)", fontSize: 12.5 }}>
            {chosenFolder ? (
              <>
                <span className="num">{formatBytes(chosenFolder.bytes)}</span>
                <span className="num">
                  {chosenFolder.files.toLocaleString("en-US")}{" "}
                  {chosenFolder.files === 1 ? "file" : "files"}, this folder and everything
                  below it
                </span>
                <span className="mono truncate">{chosenFolder.path}</span>
              </>
            ) : chosen ? (
              <>
                <span className="num">{formatBytes(chosen.size)}</span>
                <span className="num">
                  {chosen.parts === 1 ? "one message" : `${chosen.parts} parts, joined on the way`}
                </span>
                <span className="mono truncate">{chosen.path}</span>
              </>
            ) : null}
          </div>

          <div className="picker">
            {chosenFolder ? null : (
            <label className={destination === "browser" ? "picker-option active" : "picker-option"}>
              <input
                type="radio"
                name="destination"
                checked={destination === "browser"}
                onChange={() => setDestination("browser")}
              />
              <Laptop size={16} className="picker-icon" />
              <span>
                <strong>This device</strong>
                <span className="picker-hint">
                  Streamed to the browser as the parts arrive, nothing kept on the server.
                  Phone included. An interrupted download has to start again.
                </span>
              </span>
            </label>
            )}

            <label className={destination === "local" ? "picker-option active" : "picker-option"}>
              <input
                type="radio"
                name="destination"
                checked={destination === "local"}
                onChange={() => setDestination("local")}
              />
              <HardDrive size={16} className="picker-icon" />
              <span>
                <strong>Folder on the server</strong>
                <span className="picker-hint">
                  Written where the backend can reach it, in the background: you can leave
                  the page.
                </span>
              </span>
            </label>

            <label className={destination === "rclone" ? "picker-option active" : "picker-option"}>
              <input
                type="radio"
                name="destination"
                checked={destination === "rclone"}
                onChange={() => setDestination("rclone")}
              />
              <FolderSearch size={16} className="picker-icon" />
              <span>
                <strong>Rclone remote</strong>
                <span className="picker-hint">
                  Straight into the remote through the API, nothing staged on disk.
                </span>
              </span>
            </label>
          </div>

          {destination === "local" ? (
            <Field
              label="Folder"
              hint="Path inside the container. It has to be a writable volume: the folders to back up are mounted read-only and are refused here, which is what keeps a download from landing in what a sync job reads. The file keeps its path inside the channel."
            >
              <input
                value={localPath}
                onChange={(event) => setLocalPath(event.target.value)}
                placeholder="/mnt/restored"
                className="mono"
              />
            </Field>
          ) : null}

          {destination === "rclone" ? (
            <Field
              label="Remote"
              hint="Remote name with the colon, optionally followed by a folder. The file keeps its path inside the channel."
            >
              <div className="row" style={{ gap: 8 }}>
                <select
                  style={{ width: 160 }}
                  value={(rcloneStatus?.remotes ?? []).find((item) => remote.startsWith(item)) ?? ""}
                  onChange={(event) => setRemote(event.target.value)}
                >
                  <option value="" disabled>
                    Pick a remote
                  </option>
                  {(rcloneStatus?.remotes ?? []).map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
                <input
                  value={remote}
                  onChange={(event) => setRemote(event.target.value)}
                  placeholder="mycloud-crypt:Restored"
                  className="mono"
                />
                <button
                  type="button"
                  className="btn ghost small"
                  disabled={!remote.includes(":")}
                  onClick={() => setBrowsing(remote)}
                  title="Browse the remote and pick a folder"
                >
                  <FolderSearch size={13} />
                  Browse
                </button>
              </div>
            </Field>
          ) : null}

          {chosenFolder ? (
            <p className="explorer-note">
              Every file keeps its path relative to this folder. Nothing at the
              destination is ever deleted, and a file that fails does not stop the rest:
              the panel at the top of the page counts them and can stop the whole thing.
            </p>
          ) : null}

          {download.isError ? <Alert>{(download.error as Error).message}</Alert> : null}
          {fetchTo.isError ? <Alert>{(fetchTo.error as Error).message}</Alert> : null}
          {fetchFolder.isError ? <Alert>{(fetchFolder.error as Error).message}</Alert> : null}
        </Modal>
      ) : null}

      {browsing !== null ? (
        <RemoteBrowser
          remote={browsing}
          onClose={() => setBrowsing(null)}
          onPick={(picked) => {
            setRemote(picked);
            setBrowsing(null);
          }}
        />
      ) : null}
    </>
  );
}
