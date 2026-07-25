import { formatBytes, formatDuration, formatSpeed } from "../lib/format";
import type { JobProgress } from "../lib/types";
import { ProgressBar } from "./ui";

export function phaseLabel(phase: string): string {
  switch (phase) {
    case "scan":
      return "Scansione";
    case "diff":
      return "Confronto";
    case "delete":
      return "Pulizia";
    case "upload":
      return "Upload";
    default:
      return "In corso";
  }
}

/** Riga di dettaglio di un job attivo, diversa per ogni fase.
 *
 * Durante la scansione non esiste ancora un totale da cui ricavare una percentuale,
 * quindi si mostrano i contatori che crescono invece di una barra ferma a zero. */
export default function JobActivity({ progress }: { progress: JobProgress }) {
  // I contatori di scansione arrivano solo dai backend che li inviano: con un backend
  // piu vecchio restano a zero invece di far esplodere il rendering.
  const files = progress.scanned_files ?? 0;
  const dirs = progress.scanned_dirs ?? 0;
  const bytes = progress.scanned_bytes ?? 0;

  if (progress.phase === "scan") {
    return (
      <>
        <div className="mono truncate" style={{ color: "var(--muted)" }}>
          {progress.scanned_where ? `in ${progress.scanned_where}` : "lettura della cartella"}
        </div>
        <div className="row wrap num" style={{ gap: 20, fontSize: 12.5 }}>
          <strong>{files.toLocaleString("it-IT")} file trovati</strong>
          <span style={{ color: "var(--muted)" }}>
            {dirs.toLocaleString("it-IT")} cartelle visitate
          </span>
          <span style={{ color: "var(--muted)" }}>{formatBytes(bytes)}</span>
          <span style={{ color: "var(--muted)" }}>
            da {formatDuration(progress.elapsed_seconds)}
          </span>
        </div>
      </>
    );
  }

  if (progress.phase === "diff" || progress.phase === "delete") {
    return (
      <div className="row wrap num" style={{ gap: 20, fontSize: 12.5 }}>
        <strong>
          {progress.phase === "diff"
            ? "Confronto con il canale"
            : "Rimozione dei file spariti in locale"}
        </strong>
        <span style={{ color: "var(--muted)" }}>
          {files.toLocaleString("it-IT")} file esaminati
        </span>
        <span style={{ color: "var(--muted)" }}>{formatBytes(bytes)}</span>
      </div>
    );
  }

  return (
    <>
      <div className="mono truncate" style={{ color: "var(--muted)" }}>
        {progress.current_file ?? "preparazione"}
        {progress.current_parts > 1
          ? ` (parte ${progress.current_part} di ${progress.current_parts})`
          : ""}
      </div>
      <ProgressBar done={progress.bytes_done} total={progress.bytes_total} />
      <div className="row wrap num" style={{ gap: 20, fontSize: 12.5 }}>
        <strong>{formatSpeed(progress.speed_bps)}</strong>
        <span style={{ color: "var(--muted)" }}>
          {formatBytes(progress.bytes_done)} di {formatBytes(progress.bytes_total)}
        </span>
        <span style={{ color: "var(--muted)" }}>
          {progress.files_remaining.toLocaleString("it-IT")} file mancanti
        </span>
        <span style={{ color: "var(--muted)" }}>
          stimato {formatDuration(progress.eta_seconds)}
        </span>
      </div>
    </>
  );
}
