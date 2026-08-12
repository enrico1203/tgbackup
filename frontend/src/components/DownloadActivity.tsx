import { formatBytes, formatDuration, formatSpeed } from "../lib/format";
import type { DownloadProgress } from "../lib/types";
import FloodNotice, { isHeld } from "./FloodNotice";
import { ProgressBar } from "./ui";

export function downloadPhaseLabel(phase: string): string {
  switch (phase) {
    case "index":
      return "Reading the index";
    case "scan":
      return "Scanning the destination";
    case "diff":
      return "Comparing";
    case "waiting":
      return "Queued";
    case "download":
      return "Downloading";
    default:
      return "Running";
  }
}

/** Detail line of a running download job, different for every phase.
 *
 * Before the comparison there is no total to derive a percentage from, so growing
 * counters are shown instead of a bar stuck at zero. */
export default function DownloadActivity({ progress }: { progress: DownloadProgress }) {
  const indexed = progress.indexed_files ?? 0;
  const held = isHeld(progress);

  if (progress.phase === "index") {
    return (
      <div className="row wrap num" style={{ gap: 20, fontSize: 12.5 }}>
        <strong>Reading what the channel holds</strong>
        <span style={{ color: "var(--muted)" }}>
          {indexed.toLocaleString("en-US")} files in the index
        </span>
        <span style={{ color: "var(--muted)" }}>{formatBytes(progress.indexed_bytes ?? 0)}</span>
      </div>
    );
  }

  if (progress.phase === "scan") {
    return (
      <>
        <div className="mono truncate" style={{ color: "var(--muted)" }}>
          {progress.dest_where ? `in ${progress.dest_where}` : "reading the destination"}
        </div>
        <div className="row wrap num" style={{ gap: 20, fontSize: 12.5 }}>
          <strong>
            {(progress.dest_files ?? 0).toLocaleString("en-US")} files at the destination
          </strong>
          <span style={{ color: "var(--muted)" }}>
            {indexed.toLocaleString("en-US")} in the channel
          </span>
          <span style={{ color: "var(--muted)" }}>
            for {formatDuration(progress.elapsed_seconds)}
          </span>
        </div>
      </>
    );
  }

  if (progress.phase === "diff") {
    return (
      <div className="row wrap num" style={{ gap: 20, fontSize: 12.5 }}>
        <strong>Working out what is missing</strong>
        <span style={{ color: "var(--muted)" }}>
          {indexed.toLocaleString("en-US")} files in the channel
        </span>
        <span style={{ color: "var(--muted)" }}>
          {(progress.dest_files ?? 0).toLocaleString("en-US")} at the destination
        </span>
      </div>
    );
  }

  if (progress.phase === "waiting") {
    return (
      <div className="row wrap num" style={{ gap: 20, fontSize: 12.5 }}>
        <strong>Waiting for the Telegram account to free up</strong>
        <span style={{ color: "var(--muted)" }}>
          {progress.files_total.toLocaleString("en-US")} files to download for{" "}
          {formatBytes(progress.bytes_total)}
        </span>
        <span style={{ color: "var(--muted)" }}>
          another job is transferring on the same account
        </span>
      </div>
    );
  }

  return (
    <>
      <div className="mono truncate" style={{ color: "var(--muted)" }}>
        {progress.current_file ?? "preparing"}
        {progress.current_parts > 1
          ? ` (part ${progress.current_part} of ${progress.current_parts})`
          : ""}
      </div>
      <FloodNotice
        seconds={progress.flood_wait_seconds}
        waits={progress.flood_waits}
        limited={progress.limited}
        events={progress.limited_events}
        ago={progress.limited_ago}
        connections={progress.connections_allowed}
      />
      <ProgressBar
        done={progress.bytes_done}
        total={progress.bytes_total}
        tone={held ? "danger" : undefined}
      />
      <div className="row wrap num" style={{ gap: 20, fontSize: 12.5 }}>
        <strong style={held ? { color: "var(--danger)" } : undefined}>
          {held ? "held" : formatSpeed(progress.speed_bps)}
        </strong>
        <span style={{ color: "var(--muted)" }}>
          {formatBytes(progress.bytes_done)} of {formatBytes(progress.bytes_total)}
        </span>
        <span style={{ color: "var(--muted)" }}>
          {progress.files_remaining.toLocaleString("en-US")} files left
        </span>
        <span style={{ color: "var(--muted)" }}>{formatDuration(progress.eta_seconds)} left</span>
      </div>
    </>
  );
}
