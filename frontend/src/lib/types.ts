export interface Me {
  id: number;
  username: string;
  must_change_password: boolean;
}

export interface LoginResponse {
  token: string;
  must_change_password: boolean;
  username: string;
}

export interface Account {
  id: number;
  label: string;
  phone: string;
  api_id: number;
  tg_user_id: number | null;
  first_name: string | null;
  username: string | null;
  is_premium: boolean;
  default_part_size: number;
  max_concurrent_jobs: number;
  status: string;
  last_error: string | null;
  created_at: string;
  connected: boolean;
  channels_count: number;
}

export interface AccountStep {
  account_id: number;
  status: string;
  needs: string | null;
  account: Account | null;
}

export interface Channel {
  id: number;
  account_id: number;
  tg_id: number;
  title: string;
  username: string | null;
  is_private: boolean;
  kind: string;
  participants: number | null;
  last_seen_at: string;
  check_interval_days: number;
  check_hour: number;
  check_repair: boolean;
  last_check_at: string | null;
  last_check_result: string | null;
}

export interface JobStats {
  files_total: number;
  files_uploaded: number;
  files_pending: number;
  files_error: number;
  files_trashed: number;
  bytes_total: number;
  bytes_uploaded: number;
  bytes_trashed: number;
}

export interface Job {
  id: number;
  name: string;
  account_id: number;
  channel_id: number;
  source_type: "local" | "rclone";
  local_path: string;
  remote: string | null;
  interval_hours: number;
  scan_files_per_sec: number;
  part_size_bytes: number;
  include_globs: string;
  exclude_globs: string;
  max_file_size: number;
  schedule_hours: string;
  stop_outside_window: boolean;
  throttle_bps: number;
  delete_guard_percent: number;
  delete_guard_files: number;
  delete_guard_bypass: boolean;
  trash_days: number;
  silence_alerts: boolean;
  silence_alerted_at: string | null;
  enabled: boolean;
  status: string;
  phase: string | null;
  last_error: string | null;
  last_run_at: string | null;
  last_finished_at: string | null;
  next_run_at: string | null;
  created_at: string;
  account_label: string;
  channel_title: string;
  channel_tg_id: number;
  stats: JobStats;
  window_open: boolean;
  next_window_at: string | null;
}

export interface JobRun {
  id: number;
  job_id: number;
  started_at: string;
  finished_at: string | null;
  status: string;
  scanned: number;
  added: number;
  modified: number;
  removed: number;
  trashed: number;
  revived: number;
  uploaded_files: number;
  uploaded_bytes: number;
  error: string | null;
}

export interface DownloadStats {
  files_indexed: number;
  bytes_indexed: number;
  files_at_destination: number;
  bytes_at_destination: number;
  files_failed: number;
  last_run_at: string | null;
}

export interface DownloadJob {
  id: number;
  name: string;
  account_id: number;
  channel_id: number;
  dest_type: "local" | "rclone";
  local_path: string;
  remote: string | null;
  interval_hours: number;
  schedule_hours: string;
  stop_outside_window: boolean;
  throttle_bps: number;
  silence_alerts: boolean;
  silence_alerted_at: string | null;
  enabled: boolean;
  status: string;
  phase: string | null;
  last_error: string | null;
  last_run_at: string | null;
  last_finished_at: string | null;
  next_run_at: string | null;
  created_at: string;
  account_label: string;
  channel_title: string;
  channel_tg_id: number;
  stats: DownloadStats;
  window_open: boolean;
  next_window_at: string | null;
}

export interface DownloadRun {
  id: number;
  job_id: number;
  started_at: string;
  finished_at: string | null;
  status: string;
  indexed_files: number;
  indexed_bytes: number;
  present_files: number;
  present_bytes: number;
  downloaded_files: number;
  downloaded_bytes: number;
  failed_files: number;
  error: string | null;
}

export interface FilePart {
  part_index: number;
  offset: number;
  size: number;
  message_id: number;
}

export interface FileEntry {
  id: number;
  job_id: number;
  rel_path: string;
  name: string;
  size: number;
  state: string;
  parts_total: number;
  error: string | null;
  uploaded_at: string | null;
  parts: FilePart[];
}

export interface FilePage {
  items: FileEntry[];
  total: number;
}

export interface ExplorerFolder {
  name: string;
  path: string;
  files: number;
  bytes: number;
}

export interface ExplorerFile {
  id: number;
  name: string;
  path: string;
  size: number;
  parts: number;
  uploaded_at: string | null;
  trashed_at: string | null;
  purge_at: string | null;
  job_id: number;
}

export interface ExplorerListing {
  channel_id: number;
  channel_title: string;
  path: string;
  query: string;
  folders: ExplorerFolder[];
  files: ExplorerFile[];
  files_total: number;
  bytes_total: number;
  entries_total: number;
  offset: number;
  limit: number;
}

export interface DownloadTicket {
  url: string;
  name: string;
  size: number;
  expires_in: number;
}

export interface RcloneStatus {
  configured: boolean;
  version: string;
  remotes: string[];
  config_lines: number;
  updated_at: string | null;
  error: string | null;
}

export interface RemoteEntry {
  name: string;
  path: string;
  size: number;
  is_dir: boolean;
  mtime: string;
}

export interface RemotePreview {
  remote: string;
  entries: RemoteEntry[];
  truncated: boolean;
  error: string | null;
}

export interface RestoreOut {
  restore_id: string;
  target_path: string;
}

export interface RestoreFolderOut {
  restore_id: string;
  target_path: string;
  files: number;
  bytes: number;
}

export interface ExportChannel {
  channel_id: number;
  tg_id: number;
  title: string;
  account_id: number;
  account_label: string;
  jobs: number;
  files: number;
  parts: number;
  bytes_total: number;
}

export interface ImportJobPreview {
  name: string;
  source_type: string;
  source: string;
  files: number;
  parts: number;
  bytes_total: number;
}

export interface ImportPreview {
  exported_at: string | null;
  account_label: string;
  account_tg_user_id: number | null;
  channel_tg_id: number;
  channel_title: string;
  channel_username: string | null;
  jobs: ImportJobPreview[];
  files: number;
  parts: number;
  bytes_total: number;
}

export interface ImportJobResult {
  name: string;
  action: string;
  files_imported: number;
  files_skipped: number;
  parts_imported: number;
}

export interface ImportResult {
  channel_id: number;
  channel_title: string;
  channel_created: boolean;
  jobs: ImportJobResult[];
  files_imported: number;
  files_skipped: number;
  parts_imported: number;
  warnings: string[];
}

export interface NotifyPreferences {
  events: "off" | "errors" | "all";
  account_id: number;
  silence_days: number;
}

export interface BandwidthPreferences {
  rate_limit_bps: number;
}

export interface SchedulePreferences {
  timezone: string;
}

export interface MaintenanceTask {
  id: string;
  kind: "check" | "rebuild";
  channel_id: number;
  channel_title: string;
  phase: "running" | "done" | "error";
  step: string;
  processed: number;
  total: number;
  started_at: string;
  finished_at: string | null;
  result: Record<string, number | string | string[]>;
  error: string | null;
}

export interface Dashboard {
  accounts: number;
  accounts_connected: number;
  jobs: number;
  jobs_running: number;
  downloads: number;
  downloads_running: number;
  files_total: number;
  files_uploaded: number;
  files_pending: number;
  files_error: number;
  bytes_total: number;
  bytes_uploaded: number;
  recent_runs: JobRun[];
}

export interface JobProgress {
  job_id: number;
  name: string;
  phase: string;
  current_file: string | null;
  current_part: number;
  current_parts: number;
  scanned_files: number;
  scanned_dirs: number;
  scanned_bytes: number;
  scanned_where: string | null;
  files_total: number;
  files_done: number;
  files_remaining: number;
  bytes_total: number;
  bytes_done: number;
  bytes_remaining: number;
  speed_bps: number;
  eta_seconds: number | null;
  elapsed_seconds: number;
  /** How long this transfer is held by a flood wait, null when it is not. Optional:
   * an older backend does not send it and the interface simply shows nothing. */
  flood_wait_seconds?: number | null;
  flood_wait_total?: number | null;
  flood_waits?: number;
}

export interface DownloadProgress {
  job_id: number;
  name: string;
  phase: string;
  current_file: string | null;
  current_part: number;
  current_parts: number;
  indexed_files: number;
  indexed_bytes: number;
  dest_files: number;
  dest_where: string | null;
  present_files: number;
  files_total: number;
  files_done: number;
  files_remaining: number;
  bytes_total: number;
  bytes_done: number;
  bytes_remaining: number;
  speed_bps: number;
  eta_seconds: number | null;
  elapsed_seconds: number;
  /** How long this transfer is held by a flood wait, null when it is not. Optional:
   * an older backend does not send it and the interface simply shows nothing. */
  flood_wait_seconds?: number | null;
  flood_wait_total?: number | null;
  flood_waits?: number;
}

export interface RestoreProgress {
  restore_id: string;
  file_name: string;
  target_path: string;
  phase: string;
  error: string | null;
  bytes_total: number;
  bytes_done: number;
  files_total: number;
  files_done: number;
  current_file: string | null;
  failed: number;
  errors: string[];
  speed_bps: number;
  eta_seconds: number | null;
  /** How long this transfer is held by a flood wait, null when it is not. Optional:
   * an older backend does not send it and the interface simply shows nothing. */
  flood_wait_seconds?: number | null;
  flood_wait_total?: number | null;
  flood_waits?: number;
}

export interface ProgressFrame {
  type: "progress";
  jobs: JobProgress[];
  downloads: DownloadProgress[];
  restores: RestoreProgress[];
}

export interface VersionInfo {
  backend: string;
  latest_backend: string | null;
  latest_frontend: string | null;
  checked_at: string | null;
  error: string | null;
}
