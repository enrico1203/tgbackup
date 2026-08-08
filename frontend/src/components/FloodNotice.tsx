import { Hourglass } from "lucide-react";

import { formatDuration } from "../lib/format";

/** Whether a transfer is being held right now. One place, because four of them ask, and
 *  because a backend too old to send the field must read as "not held" and not as NaN. */
export function isHeld(progress?: { flood_wait_seconds?: number | null } | null): boolean {
  return (progress?.flood_wait_seconds ?? 0) > 0;
}

/** A transfer Telegram has told to wait.
 *
 * Held is not slow and not broken, and until this existed the interface had no way to
 * say which of the three was happening: a job whose speed had fallen to zero looked the
 * same whether the account was limited, the source had stalled or the line was busy. The
 * countdown is what makes it readable at a glance, and the count of waits is what says
 * whether this is one bad minute or an account that has been limited all night.
 *
 * It renders nothing at all when there is no wait, so every caller can place it
 * unconditionally. */
export default function FloodNotice({
  seconds,
  waits,
}: {
  seconds?: number | null;
  waits?: number;
}) {
  if (seconds === null || seconds === undefined || seconds <= 0) return null;

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
