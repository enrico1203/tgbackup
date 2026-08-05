import { useMutation } from "@tanstack/react-query";
import { Square } from "lucide-react";

import { api } from "../lib/api";
import { formatBytes, formatDuration, formatSpeed } from "../lib/format";
import { useProgress } from "../lib/progress";
import { Alert, Card, CardHead, Pill, ProgressBar } from "./ui";

const PHASES: Record<string, { label: string; tone: "ok" | "bad" | "mute" | "accent" }> = {
  done: { label: "Completed", tone: "ok" },
  error: { label: "Error", tone: "bad" },
  cancelled: { label: "Stopped", tone: "mute" },
};

/** Files being rebuilt from their parts, wherever they are going: the restore folder of
 *  the application, a folder mounted in the container or an rclone remote. One row is one
 *  restore, which is one file or a whole folder: the counters are the same either way.
 *  Shown by the two pages that can start one. */
export default function RestorePanel() {
  const { restores } = useProgress();
  const items = Array.from(restores.values());

  const stop = useMutation({
    mutationFn: (restoreId: string) => api.post(`/api/explorer/restore/${restoreId}/cancel`),
  });

  if (items.length === 0) return null;

  return (
    <Card>
      <CardHead title="Files being rebuilt" />
      <div className="card-body">
        {items.map((restore) => {
          const phase = PHASES[restore.phase] ?? { label: "Running", tone: "accent" as const };
          const many = restore.files_total > 1;
          return (
            <div key={restore.restore_id} style={{ display: "grid", gap: 8 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span style={{ fontWeight: 600 }}>{restore.file_name}</span>
                <div className="row" style={{ gap: 8 }}>
                  {restore.phase === "running" ? (
                    <button
                      type="button"
                      className="btn ghost small"
                      onClick={() => stop.mutate(restore.restore_id)}
                      title="Stop after the part being transferred. What is already written stays."
                    >
                      <Square size={13} />
                      Stop
                    </button>
                  ) : null}
                  <Pill tone={phase.tone} live={restore.phase === "running"}>
                    {phase.label}
                  </Pill>
                </div>
              </div>

              <div className="mono truncate" style={{ color: "var(--muted)" }}>
                {restore.target_path}
              </div>

              <ProgressBar done={restore.bytes_done} total={restore.bytes_total} />

              <div
                className="row wrap num"
                style={{ gap: 20, fontSize: 12.5, color: "var(--muted)" }}
              >
                <span>
                  {formatBytes(restore.bytes_done)} of {formatBytes(restore.bytes_total)}
                </span>
                {many ? (
                  <span>
                    {restore.files_done.toLocaleString("en-US")} of{" "}
                    {restore.files_total.toLocaleString("en-US")} files
                  </span>
                ) : null}
                <span>{formatSpeed(restore.speed_bps)}</span>
                <span>{formatDuration(restore.eta_seconds)} left</span>
                {restore.failed > 0 ? (
                  <span style={{ color: "var(--danger)" }}>
                    {restore.failed.toLocaleString("en-US")} failed
                  </span>
                ) : null}
              </div>

              {restore.current_file && restore.phase === "running" ? (
                <div className="mono truncate" style={{ fontSize: 12.5 }}>
                  {restore.current_file}
                </div>
              ) : null}

              {restore.error ? <Alert>{restore.error}</Alert> : null}
              {/* The first few failures, when the rest of the folder went through: the
                  restore did not fail, but those files are not there. */}
              {!restore.error && restore.errors.length > 0 ? (
                <Alert tone="info">
                  {restore.errors.slice(0, 5).map((line) => (
                    <div key={line} className="mono truncate">
                      {line}
                    </div>
                  ))}
                  {restore.failed > restore.errors.length
                    ? `and ${restore.failed - restore.errors.length} more`
                    : null}
                </Alert>
              ) : null}
            </div>
          );
        })}
      </div>
    </Card>
  );
}
