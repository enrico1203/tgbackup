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
}

export interface JobStats {
  files_total: number;
  files_uploaded: number;
  files_pending: number;
  files_error: number;
  bytes_total: number;
  bytes_uploaded: number;
}

export interface Job {
  id: number;
  name: string;
  account_id: number;
  channel_id: number;
  local_path: string;
  interval_hours: number;
  scan_files_per_sec: number;
  part_size_bytes: number;
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
  uploaded_files: number;
  uploaded_bytes: number;
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

export interface RestoreOut {
  restore_id: string;
  target_path: string;
}

export interface Dashboard {
  accounts: number;
  accounts_connected: number;
  jobs: number;
  jobs_running: number;
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
}

export interface RestoreProgress {
  restore_id: string;
  file_name: string;
  target_path: string;
  phase: string;
  error: string | null;
  bytes_total: number;
  bytes_done: number;
  speed_bps: number;
  eta_seconds: number | null;
}

export interface ProgressFrame {
  type: "progress";
  jobs: JobProgress[];
  restores: RestoreProgress[];
}
