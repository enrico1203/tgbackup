import { formatBytes, formatDuration, formatSpeed } from "../lib/format";
import { useProgress } from "../lib/progress";
import { Alert, Card, CardHead, Pill, ProgressBar } from "./ui";

/** Files being rebuilt from their parts, wherever they are going: the restore folder of
 *  the application, a folder mounted in the container or an rclone remote. Shown by the
 *  two pages that can start one. */
export default function RestorePanel() {
  const { restores } = useProgress();
  const items = Array.from(restores.values());
  if (items.length === 0) return null;

  return (
    <Card>
      <CardHead title="Files being rebuilt" />
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
