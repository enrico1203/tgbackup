import { Gauge, Hourglass } from "lucide-react";

import { formatDuration } from "../lib/format";

interface Held {
  flood_wait_seconds?: number | null;
  limited?: boolean;
}

/** Whether a transfer is stopped dead right now, waiting out a hold. One place, because
 *  four of them ask, and because a backend too old to send the field must read as "not
 *  held" and not as NaN. */
export function isHeld(progress?: Held | null): boolean {
  return (progress?.flood_wait_seconds ?? 0) > 0;
}

/** Whether Telegram is holding this account back, which is not the same thing.
 *
 * A held transfer is at zero and says so. A limited one keeps moving bytes between the
 * cuts, so it looks like an ordinary transfer having a bad night, and the only way anybody
 * could tell the difference was to read the log. This is what the job is marked with. */
export function isLimited(progress?: Held | null): boolean {
  return Boolean(progress?.limited);
}

/** What Telegram is doing to a transfer, said in the interface rather than in the log.
 *
 * Two states, and they are not the same. **Held** is the transfer stopped, waiting out a
 * wait Telegram asked for, and the countdown is what makes it readable at a glance.
 * **Limited** is the account being held back while the bytes still move: parts taken and
 * never answered, connections cut every few minutes, no error anywhere. That one used to
 * be invisible, and a job going at a quarter of its speed looked like a job going slowly.
 *
 * It renders nothing at all when neither is happening, so every caller can place it
 * unconditionally. */
export default function FloodNotice({
  seconds,
  waits,
  limited,
  events,
  ago,
  connections,
}: {
  seconds?: number | null;
  waits?: number;
  limited?: boolean;
  events?: number;
  ago?: number | null;
  connections?: number | null;
}) {
  if (seconds !== null && seconds !== undefined && seconds > 0) {
    return (
      <div className="alert" style={{ padding: "8px 11px", fontSize: 12.5, alignItems: "center" }}>
        <Hourglass size={14} style={{ flexShrink: 0 }} />
        <span>
          <strong>Held by Telegram</strong> — the account has hit its limit, retrying in{" "}
          {formatDuration(seconds)}
          {waits && waits > 1 ? ` (${waits} waits in this run)` : ""}
        </span>
      </div>
    );
  }

  if (!limited) return null;

  return (
    <div
      className="alert warn"
      style={{ padding: "8px 11px", fontSize: 12.5, alignItems: "center" }}
    >
      <Gauge size={14} style={{ flexShrink: 0 }} />
      <span>
        <strong>Telegram is limiting this account</strong> — it has interrupted this run{" "}
        {events ?? 0} times
        {ago !== null && ago !== undefined ? `, the last one ${formatDuration(ago)} ago` : ""}.
        Each interruption costs one part sent again, nothing is lost.
        {connections
          ? ` The transfer answered by opening ${connections} connections instead of 20,
             and takes one back for every five clean minutes.`
          : ""}
      </span>
    </div>
  );
}
