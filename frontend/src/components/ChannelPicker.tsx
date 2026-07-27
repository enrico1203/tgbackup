import { Hash } from "lucide-react";

import type { ChannelGroup } from "../lib/channels";
import { formatBytes, percent } from "../lib/format";
import { Pill, ProgressBar } from "./ui";

/** The destination channels, as cards to choose from. Shared by the pages that work one
 *  channel at a time: files and restore, and the explorer. */
export default function ChannelPicker({
  groups,
  selected,
  onSelect,
}: {
  groups: ChannelGroup[];
  selected: number | null;
  onSelect: (channelId: number) => void;
}) {
  return (
    <div className="channel-grid">
      {groups.map((group) => {
        const done = percent(group.filesUploaded, group.filesTotal);
        return (
          <button
            key={group.channelId}
            type="button"
            className={group.channelId === selected ? "channel-card active" : "channel-card"}
            onClick={() => onSelect(group.channelId)}
          >
            <div className="row" style={{ gap: 9 }}>
              <Hash size={15} style={{ flexShrink: 0, opacity: 0.65 }} />
              <span className="channel-title">{group.title}</span>
              {group.filesError > 0 ? (
                <span style={{ marginLeft: "auto" }}>
                  <Pill tone="bad">{group.filesError}</Pill>
                </span>
              ) : null}
            </div>

            <div className="channel-meta num">
              {group.filesUploaded.toLocaleString("en-US")} of{" "}
              {group.filesTotal.toLocaleString("en-US")} files, {formatBytes(group.bytesUploaded)}
            </div>

            <ProgressBar done={group.filesUploaded} total={group.filesTotal} />

            <div className="channel-meta">
              {group.jobNames.join(", ")} on {group.accounts.join(", ")}
              <span className="num"> — {done.toFixed(done >= 99.5 ? 1 : 0)} per cent</span>
            </div>
          </button>
        );
      })}
    </div>
  );
}
