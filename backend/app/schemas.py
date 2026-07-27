from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    enabled: bool | None = None


class JobStats(BaseModel):
    files_total: int = 0
    files_uploaded: int = 0
    files_pending: int = 0
    files_error: int = 0
    bytes_total: int = 0
    bytes_uploaded: int = 0


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
    enabled: bool = True


class DownloadJobUpdate(BaseModel):
    name: str | None = None
    channel_id: int | None = None
    dest_type: Literal["local", "rclone"] | None = None
    local_path: str | None = None
    remote: str | None = None
    interval_hours: float | None = Field(default=None, gt=0, le=24 * 365)
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


# Restore


class RestoreIn(BaseModel):
    file_id: int


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
