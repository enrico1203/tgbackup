import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Check, Copy, File, Folder, HardDrive } from "lucide-react";

import { api } from "../lib/api";
import { formatBytes } from "../lib/format";
import type { RemotePreview } from "../lib/types";
import { Alert, Modal, Spinner } from "./ui";

const PAGE_LIMIT = 20;

/** Builds the path in the form that gets pasted into the job field. */
function joinRemote(base: string, segments: string[]): string {
  const root = base.endsWith(":") ? base : `${base}:`;
  return segments.length ? `${root}${segments.join("/")}` : root;
}

export default function RemoteBrowser({
  remote,
  onClose,
  onPick,
}: {
  remote: string;
  onClose: () => void;
  onPick?: (path: string) => void;
}) {
  const [segments, setSegments] = useState<string[]>([]);
  const [copied, setCopied] = useState(false);

  const fullPath = joinRemote(remote, segments);

  const { data, isFetching } = useQuery({
    queryKey: ["remote-preview", fullPath],
    queryFn: () =>
      api.get<RemotePreview>(
        `/api/rclone/preview?remote=${encodeURIComponent(fullPath)}&limit=${PAGE_LIMIT}`,
      ),
  });

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(fullPath);
    } catch {
      // Without clipboard permission, selecting the field by hand still works.
      return;
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <Modal
      title={`Contents of ${remote}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn ghost" onClick={onClose}>
            Close
          </button>
          {onPick ? (
            <button type="button" className="btn" onClick={() => onPick(fullPath)}>
              Use this path
            </button>
          ) : null}
        </>
      }
    >
      <div className="path-bar">
        <HardDrive size={14} style={{ flexShrink: 0, color: "var(--muted)" }} />
        <input className="mono" value={fullPath} readOnly onFocus={(e) => e.target.select()} />
        <button
          type="button"
          className="btn ghost small"
          onClick={copy}
          title="Copy the path"
        >
          {copied ? <Check size={13} /> : <Copy size={13} />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>

      <div className="row wrap" style={{ gap: 6, fontSize: 12 }}>
        <button
          type="button"
          className="crumb"
          onClick={() => setSegments([])}
          disabled={segments.length === 0}
        >
          {remote}
        </button>
        {segments.map((segment, index) => (
          <button
            key={`${segment}-${index}`}
            type="button"
            className="crumb"
            onClick={() => setSegments(segments.slice(0, index + 1))}
            disabled={index === segments.length - 1}
          >
            {segment}
          </button>
        ))}
      </div>

      {data?.error ? <Alert>{data.error}</Alert> : null}

      {isFetching ? (
        <div className="row" style={{ color: "var(--muted)" }}>
          <Spinner /> Reading the remote
        </div>
      ) : !data || data.entries.length === 0 ? (
        <div style={{ color: "var(--muted)", padding: "20px 0", textAlign: "center" }}>
          {data?.error ? "This folder cannot be read" : "Empty folder"}
        </div>
      ) : (
        <div className="browser">
          {segments.length > 0 ? (
            <button
              type="button"
              className="browser-row"
              onClick={() => setSegments(segments.slice(0, -1))}
            >
              <ArrowLeft size={15} style={{ color: "var(--muted)" }} />
              <span style={{ color: "var(--muted)" }}>Parent folder</span>
            </button>
          ) : null}

          {data.entries.map((entry) => {
            const row = (
              <>
                {entry.is_dir ? (
                  <Folder size={15} style={{ color: "var(--accent)", flexShrink: 0 }} />
                ) : (
                  <File size={15} style={{ color: "var(--muted)", flexShrink: 0 }} />
                )}
                <span className="browser-name">{entry.name}</span>
                <span className="browser-size num">
                  {entry.is_dir ? "folder" : formatBytes(entry.size)}
                </span>
              </>
            );

            if (!entry.is_dir) {
              return (
                <div key={entry.path} className="browser-row static">
                  {row}
                </div>
              );
            }
            return (
              <button
                key={entry.path}
                type="button"
                className="browser-row"
                onClick={() => setSegments([...segments, entry.name])}
              >
                {row}
              </button>
            );
          })}
        </div>
      )}

      {data?.truncated ? (
        <span style={{ fontSize: 12, color: "var(--muted)" }}>
          Showing the first {PAGE_LIMIT} entries, the folder holds more. The sync job takes
          them all anyway.
        </span>
      ) : null}
    </Modal>
  );
}
