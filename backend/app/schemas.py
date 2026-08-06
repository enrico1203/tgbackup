from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import SCHEDULE_ALWAYS

# The weekly window, 168 hours: "0" closed, "1" open, "2" open but limited to the
# bandwidth set on the job. Validated here rather than in the job forms so a wrong length
# is a 422 with the field named, not a schedule silently read as always open.
SCHEDULE_PATTERN = r"^[012]{168}$"


class Model(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# Authentication


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    token: str
    must_change_password: bool
    username: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class MeOut(Model):
    id: int
    username: str
    must_change_password: bool


# Telegram accounts


class AccountStartIn(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    api_id: int
    api_hash: str = Field(min_length=8)
    phone: str = Field(min_length=5)


class AccountCodeIn(BaseModel):
    code: str = Field(min_length=3)


class AccountPasswordIn(BaseModel):
    password: str


class AccountOut(Model):
    id: int
    label: str
    phone: str
    api_id: int
    tg_user_id: int | None
    first_name: str | None
    username: str | None
    is_premium: bool
    default_part_size: int
    max_concurrent_jobs: int
    status: str
    last_error: str | None
    created_at: datetime
    connected: bool = False
    channels_count: int = 0


class AccountUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=128)
    # The ceiling is 20 because that is the number of connections per data center: with
    # more jobs than that each would be left with less than one connection.
    max_concurrent_jobs: int | None = Field(default=None, ge=1, le=20)
    default_part_size: int | None = Field(default=None, gt=0)


class AccountStepOut(BaseModel):
    account_id: int
    status: str
    needs: str | None = None
    account: AccountOut | None = None


# Channels


class ChannelOut(Model):
    id: int
    account_id: int
    tg_id: int
    title: str
    username: str | None
    is_private: bool
    kind: str
    participants: int | None
    last_seen_at: datetime
    # Automatic integrity check. 0 days is off, and the hour is local to the timezone of
    # the installation.
    check_interval_days: int = 0
    check_hour: int = 4
    check_repair: bool = False
    last_check_at: datetime | None = None
    last_check_result: str | None = None


class CheckScheduleIn(BaseModel):
    check_interval_days: int = Field(default=0, ge=0, le=365)
    check_hour: int = Field(default=4, ge=0, le=23)
    check_repair: bool = False


# Sync job


class JobIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    account_id: int
    channel_id: int
    source_type: Literal["local", "rclone"] = "local"
    local_path: str = ""
    remote: str | None = None
    interval_hours: float = Field(gt=0, le=24 * 365)
    scan_files_per_sec: int = Field(default=0, ge=0)
    part_size_bytes: int | None = None
    include_globs: str = ""
    exclude_globs: str = ""
    max_file_size: int = Field(default=0, ge=0)
    schedule_hours: str = Field(default=SCHEDULE_ALWAYS, pattern=SCHEDULE_PATTERN)
    stop_outside_window: bool = False
    throttle_bps: int = Field(default=0, ge=0)
    delete_guard_percent: int = Field(default=20, ge=0, le=100)
    delete_guard_files: int = Field(default=0, ge=0)
    trash_days: int = Field(default=0, ge=0, le=3650)
    silence_alerts: bool = True
    enabled: bool = True


class JobUpdate(BaseModel):
    name: str | None = None
    channel_id: int | None = None
    source_type: Literal["local", "rclone"] | None = None
    local_path: str | None = None
    remote: str | None = None
    interval_hours: float | None = Field(default=None, gt=0, le=24 * 365)
    scan_files_per_sec: int | None = Field(default=None, ge=0)
    part_size_bytes: int | None = None
    include_globs: str | None = None
    exclude_globs: str | None = None
    max_file_size: int | None = Field(default=None, ge=0)
    schedule_hours: str | None = Field(default=None, pattern=SCHEDULE_PATTERN)
    stop_outside_window: bool | None = None
    throttle_bps: int | None = Field(default=None, ge=0)
    delete_guard_percent: int | None = Field(default=None, ge=0, le=100)
    delete_guard_files: int | None = Field(default=None, ge=0)
    trash_days: int | None = Field(default=None, ge=0, le=3650)
    silence_alerts: bool | None = None
    enabled: bool | None = None


class JobStats(BaseModel):
    files_total: int = 0
    files_uploaded: int = 0
    files_pending: int = 0
    files_error: int = 0
    files_trashed: int = 0
    bytes_total: int = 0
    bytes_uploaded: int = 0
    bytes_trashed: int = 0


class JobOut(Model):
    id: int
    name: str
    account_id: int
    channel_id: int
    source_type: str
    local_path: str
    remote: str | None
    interval_hours: float
    scan_files_per_sec: int
    part_size_bytes: int
    include_globs: str
    exclude_globs: str
    max_file_size: int
    schedule_hours: str
    stop_outside_window: bool
    throttle_bps: int
    delete_guard_percent: int
    delete_guard_files: int
    delete_guard_bypass: bool
    trash_days: int
    silence_alerts: bool
    silence_alerted_at: datetime | None
    enabled: bool
    status: str
    phase: str | None
    last_error: str | None
    last_run_at: datetime | None
    last_finished_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime

    account_label: str = ""
    channel_title: str = ""
    # Used by the frontend to build the t.me/c/<channel>/<message> links.
    channel_tg_id: int = 0
    stats: JobStats = JobStats()
    # Computed from the window and the timezone of the installation: whether the job may
    # start right now and, if not, when it may. Computed here because the browser would
    # have to redo the timezone arithmetic to get the same answer.
    window_open: bool = True
    next_window_at: datetime | None = None


class JobRunOut(Model):
    id: int
    job_id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    scanned: int
    added: int
    modified: int
    removed: int
    trashed: int
    revived: int
    uploaded_files: int
    uploaded_bytes: int
    error: str | None


# Download job


class DownloadJobIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    account_id: int
    channel_id: int
    dest_type: Literal["local", "rclone"] = "local"
    local_path: str = ""
    remote: str | None = None
    interval_hours: float = Field(gt=0, le=24 * 365)
    schedule_hours: str = Field(default=SCHEDULE_ALWAYS, pattern=SCHEDULE_PATTERN)
    stop_outside_window: bool = False
    throttle_bps: int = Field(default=0, ge=0)
    silence_alerts: bool = True
    enabled: bool = True


class DownloadJobUpdate(BaseModel):
    name: str | None = None
    channel_id: int | None = None
    dest_type: Literal["local", "rclone"] | None = None
    local_path: str | None = None
    remote: str | None = None
    interval_hours: float | None = Field(default=None, gt=0, le=24 * 365)
    schedule_hours: str | None = Field(default=None, pattern=SCHEDULE_PATTERN)
    stop_outside_window: bool | None = None
    throttle_bps: int | None = Field(default=None, ge=0)
    silence_alerts: bool | None = None
    enabled: bool | None = None


class DownloadStats(BaseModel):
    # What the channel index holds, read live.
    files_indexed: int = 0
    bytes_indexed: int = 0
    # What was at the destination at the end of the last run, that is what was already
    # there plus what that run wrote. Nothing is recomputed outside a run: only a run
    # looks at the destination.
    files_at_destination: int = 0
    bytes_at_destination: int = 0
    files_failed: int = 0
    last_run_at: datetime | None = None


class DownloadJobOut(Model):
    id: int
    name: str
    account_id: int
    channel_id: int
    dest_type: str
    local_path: str
    remote: str | None
    interval_hours: float
    schedule_hours: str
    stop_outside_window: bool
    throttle_bps: int
    silence_alerts: bool
    silence_alerted_at: datetime | None
    enabled: bool
    status: str
    phase: str | None
    last_error: str | None
    last_run_at: datetime | None
    last_finished_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime

    account_label: str = ""
    channel_title: str = ""
    channel_tg_id: int = 0
    stats: DownloadStats = DownloadStats()
    window_open: bool = True
    next_window_at: datetime | None = None


class DownloadRunOut(Model):
    id: int
    job_id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    indexed_files: int
    indexed_bytes: int
    present_files: int
    present_bytes: int
    downloaded_files: int
    downloaded_bytes: int
    failed_files: int
    error: str | None


# Files


class FilePartOut(Model):
    part_index: int
    offset: int
    size: int
    message_id: int


class FileOut(Model):
    id: int
    job_id: int
    rel_path: str
    name: str
    size: int
    state: str
    parts_total: int
    error: str | None
    uploaded_at: datetime | None
    parts: list[FilePartOut] = []


class FilePage(BaseModel):
    items: list[FileOut]
    total: int


# File explorer


class ExplorerFolder(BaseModel):
    name: str
    # Path to open, already relative to the root of the channel.
    path: str
    # Everything under it, not only what sits directly inside.
    files: int
    bytes: int


class ExplorerFile(BaseModel):
    id: int
    name: str
    path: str
    size: int
    # A file split across several messages is one file here. This is how many messages
    # hold it, kept because it explains why a large download takes a while to start.
    parts: int
    uploaded_at: datetime | None
    # Set only on a file that is no longer at the source: when it went into the trash and
    # the day its messages are deleted for good. Both null in an ordinary listing.
    trashed_at: datetime | None = None
    purge_at: datetime | None = None
    job_id: int


class ExplorerListing(BaseModel):
    channel_id: int
    channel_title: str
    path: str
    # What was searched for, echoed back. Empty means this is a plain folder listing and
    # the entries are the ones directly inside it; set, it means the folders and files
    # are matches from anywhere below.
    query: str
    folders: list[ExplorerFolder]
    files: list[ExplorerFile]
    # Files and bytes of this folder and everything below it.
    files_total: int
    bytes_total: int
    # Folders plus files directly at this level, before the page is cut.
    entries_total: int
    offset: int
    limit: int


class ExplorerFetchIn(BaseModel):
    file_id: int
    # local: a folder mounted in the container, and it has to be writable.
    # rclone: a remote, written through rcat with nothing staged on disk.
    dest_type: Literal["local", "rclone"]
    path: str = Field(min_length=1)


class ExplorerFetchFolderIn(BaseModel):
    channel_id: int
    # The folder inside the channel, empty for the whole channel.
    path: str = ""
    dest_type: Literal["local", "rclone"]
    # Where it lands. Named apart from `path` because both are paths and confusing the
    # two would write the channel into itself.
    path_to: str = Field(min_length=1)


class DownloadTicketIn(BaseModel):
    file_id: int


class DownloadTicketOut(BaseModel):
    url: str
    name: str
    size: int
    expires_in: int


# Restore


class RestoreIn(BaseModel):
    file_id: int


class RestoreFolderOut(BaseModel):
    restore_id: str
    target_path: str
    # What was actually queued, which is what the interface reports back: the folder may
    # hold files the index has no parts for, and those are skipped.
    files: int
    bytes: int


class RestoreOut(BaseModel):
    restore_id: str
    target_path: str


# rclone


class RcloneConfigIn(BaseModel):
    content: str


class RcloneStatusOut(BaseModel):
    configured: bool
    version: str
    remotes: list[str]
    config_lines: int
    updated_at: datetime | None
    error: str | None


class RcloneContentOut(BaseModel):
    content: str


class RemoteCheckOut(BaseModel):
    ok: bool
    error: str | None


class RemoteEntryOut(BaseModel):
    name: str
    path: str
    size: int
    is_dir: bool
    mtime: str


class RemotePreviewOut(BaseModel):
    remote: str
    entries: list[RemoteEntryOut]
    truncated: bool
    error: str | None = None


# Export and import of a channel


class ExportChannelOut(BaseModel):
    channel_id: int
    tg_id: int
    title: str
    account_id: int
    account_label: str
    jobs: int
    files: int
    parts: int
    bytes_total: int


class ImportJobPreview(BaseModel):
    name: str
    source_type: str
    source: str
    files: int
    parts: int
    bytes_total: int


class ImportPreviewOut(BaseModel):
    exported_at: str | None
    account_label: str
    account_tg_user_id: int | None
    channel_tg_id: int
    channel_title: str
    channel_username: str | None
    jobs: list[ImportJobPreview]
    files: int
    parts: int
    bytes_total: int


class ImportJobResult(BaseModel):
    name: str
    # created or merged
    action: str
    files_imported: int
    files_skipped: int
    parts_imported: int


class ImportResultOut(BaseModel):
    channel_id: int
    channel_title: str
    channel_created: bool
    jobs: list[ImportJobResult]
    files_imported: int
    files_skipped: int
    parts_imported: int
    warnings: list[str]


# Notifications


class NotifyPreferencesIn(BaseModel):
    # Days without a successful run after which a job is reported. 0 turns it off.
    silence_days: int = Field(default=3, ge=0, le=365)
    events: Literal["off", "errors", "all"]
    # 0 means the report travels through the account of the job that ran.
    account_id: int = Field(default=0, ge=0)


class NotifyPreferencesOut(BaseModel):
    silence_days: int = 3
    events: str
    account_id: int


class BandwidthPreferencesIn(BaseModel):
    # Bytes per second for the whole installation, 0 for no limit.
    rate_limit_bps: int = Field(default=0, ge=0)


class BandwidthPreferencesOut(BaseModel):
    rate_limit_bps: int


class SchedulePreferencesIn(BaseModel):
    # An IANA name, "Europe/Rome". The schedule windows are read in this zone, so a job
    # set to run at night keeps running at night across a change of season.
    timezone: str = Field(min_length=1, max_length=64)


class SchedulePreferencesOut(BaseModel):
    timezone: str


# Channel maintenance


class CheckIn(BaseModel):
    channel_id: int
    # Marks the damaged files for re-upload. Without it the check only reports.
    repair: bool = True


class RebuildIn(BaseModel):
    channel_id: int
    # create: a new disabled job. merge: adds to an existing job on this channel.
    mode: Literal["create", "merge"] = "create"
    job_id: int | None = None
    job_name: str = ""


class MaintenanceTaskOut(BaseModel):
    id: str
    kind: str
    channel_id: int
    channel_title: str
    phase: str
    step: str
    processed: int
    total: int
    started_at: datetime
    finished_at: datetime | None
    result: dict
    error: str | None


# Dashboard


class DashboardOut(BaseModel):
    accounts: int
    accounts_connected: int
    jobs: int
    jobs_running: int
    downloads: int
    downloads_running: int
    files_total: int
    files_uploaded: int
    files_pending: int
    files_error: int
    bytes_total: int
    bytes_uploaded: int
    recent_runs: list[JobRunOut]


# Version


class VersionOut(BaseModel):
    # What this backend was built as. The interface knows its own version by itself,
    # baked in at build time, since the two images are published separately and one of
    # them can perfectly well be older than the other.
    backend: str
    latest_backend: str | None
    latest_frontend: str | None
    checked_at: datetime | None
    error: str | None
