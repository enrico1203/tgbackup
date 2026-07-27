import type { Job } from "./types";

export interface ChannelGroup {
  channelId: number;
  channelTgId: number;
  title: string;
  accounts: string[];
  jobNames: string[];
  filesTotal: number;
  filesUploaded: number;
  filesError: number;
  bytesTotal: number;
  bytesUploaded: number;
}

/** Files belong to a job, and every job writes to a channel. To show them per channel, the
 *  jobs sharing the same destination are grouped together. */
export function groupByChannel(jobs: Job[]): ChannelGroup[] {
  const groups = new Map<number, ChannelGroup>();
  for (const job of jobs) {
    let group = groups.get(job.channel_id);
    if (!group) {
      group = {
        channelId: job.channel_id,
        channelTgId: job.channel_tg_id,
        title: job.channel_title,
        accounts: [],
        jobNames: [],
        filesTotal: 0,
        filesUploaded: 0,
        filesError: 0,
        bytesTotal: 0,
        bytesUploaded: 0,
      };
      groups.set(job.channel_id, group);
    }
    if (!group.accounts.includes(job.account_label)) group.accounts.push(job.account_label);
    group.jobNames.push(job.name);
    group.filesTotal += job.stats.files_total;
    group.filesUploaded += job.stats.files_uploaded;
    group.filesError += job.stats.files_error;
    group.bytesTotal += job.stats.bytes_total;
    group.bytesUploaded += job.stats.bytes_uploaded;
  }
  return Array.from(groups.values()).sort((a, b) => a.title.localeCompare(b.title));
}
