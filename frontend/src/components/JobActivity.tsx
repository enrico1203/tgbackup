import { formatBytes, formatDuration, formatSpeed } from "../lib/format";
import type { JobProgress } from "../lib/types";
import FloodNotice, { isHeld } from "./FloodNotice";
import { ProgressBar } from "./ui";

export function phaseLabel(phase: string): string {
  switch (phase) {
    case "scan":
      return "Scanning";
    case "diff":
      return "Comparing";
    case "delete":
      return "Cleanup";
    case "waiting":
      return "Queued";
    case "upload":
      return "Uploading";
    default:
      return "Running";
  }
}

/** Detail line of an active job, different for every phase.
 *
 * During scanning there is no total yet from which to derive a percentage, so growing
 * counters are shown instead of a bar stuck at zero. */
export default function JobActivity({ progress }: { progress: JobProgress }) {
  // The scan counters only come from backends that send them: with an older backend they
  // stay at zero instead of breaking the rendering.
  const files = progress.scanned_files ?? 0;
  const dirs = progress.scanned_dirs ?? 0;
  const bytes = progress.scanned_bytes ?? 0;
  const held = isHeld(progress);

  if (progress.phase === "scan") {
    return (
      <>
        <div className="mono truncate" style={{ color: "var(--muted)" }}>
          {progress.scanned_where ? `in ${progress.scanned_where}` : "reading the folder"}
        </div>
        <div className="row wrap num" style={{ gap: 20, fontSize: 12.5 }}>
          <strong>{files.toLocaleString("en-US")} files found</strong>
          <span style={{ color: "var(--muted)" }}>
            {dirs.toLocaleString("en-US")} folders visited
          </span>
          <span style={{ color: "var(--muted)" }}>{formatBytes(bytes)}</span>
          <span style={{ color: "var(--muted)" }}>
            for {formatDuration(progress.elapsed_seconds)}
          </span>
        </div>
      </>
    );
  }

  if (progress.phase === "waiting") {
    return (
      <div className="row wrap num" style={{ gap: 20, fontSize: 12.5 }}>
        <strong>Waiting for the Telegram account to free up</strong>
        <span style={{ color: "var(--muted)" }}>
          scan already done, {files.toLocaleString("en-US")} files in the source for{" "}
          {formatBytes(bytes)}
        </span>
        <span style={{ color: "var(--muted)" }}>
          another job is uploading on the same account or bot set
        </span>
      </div>
    );
  }

  if (progress.phase === "diff" || progress.phase === "delete") {
    return (
      <div className="row wrap num" style={{ gap: 20, fontSize: 12.5 }}>
        <strong>
          {progress.phase === "diff"
            ? "Comparing with the channel"
            : "Removing files gone from the source"}
        </strong>
        <span style={{ color: "var(--muted)" }}>
          {files.toLocaleString("en-US")} files examined
        </span>
        <span style={{ color: "var(--muted)" }}>{formatBytes(bytes)}</span>
      </div>
    );
  }

  // A job on a bot set carries one file per bot, so there is no single current file: each
  // worker says what it is holding. With one worker, which is every job on an account, the
  // list says exactly what the single line said and the line is shown instead.
  const workers = (progress.workers ?? []).filter((worker) => worker.current_file);

  return (
    <>
      {workers.length > 1 ? (
        <div style={{ display: "grid", gap: 2 }}>
          {workers.map((worker) => (
            <div
              key={worker.slot}
              className="mono truncate"
              style={{ color: "var(--muted)", fontSize: 12 }}
            >
              {worker.label}: {worker.current_file}
              {worker.current_parts > 1
                ? ` (part ${worker.current_part} of ${worker.current_parts})`
                : ""}
            </div>
          ))}
        </div>
      ) : (
        <div className="mono truncate" style={{ color: "var(--muted)" }}>
          {progress.current_file ?? "preparing"}
          {progress.current_parts > 1
            ? ` (part ${progress.current_part} of ${progress.current_parts})`
            : ""}
        </div>
      )}
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
        <span style={{ color: "var(--muted)" }}>
          {formatDuration(progress.eta_seconds)} left
        </span>
      </div>
    </>
  );
}
