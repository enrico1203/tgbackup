from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# Autenticazione


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


# Account Telegram


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
    status: str
    last_error: str | None
    created_at: datetime
    connected: bool = False
    channels_count: int = 0


class AccountStepOut(BaseModel):
    account_id: int
    status: str
    needs: str | None = None
    account: AccountOut | None = None


# Canali


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
    local_path: str = Field(min_length=1)
    interval_hours: float = Field(gt=0, le=24 * 365)
    scan_files_per_sec: int = Field(default=0, ge=0)
    part_size_bytes: int | None = None
    enabled: bool = True


class JobUpdate(BaseModel):
    name: str | None = None
    channel_id: int | None = None
    local_path: str | None = None
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
    local_path: str
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


# File


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


# Dashboard


class DashboardOut(BaseModel):
    accounts: int
    accounts_connected: int
    jobs: int
    jobs_running: int
    files_total: int
    files_uploaded: int
    files_pending: int
    files_error: int
    bytes_total: int
    bytes_uploaded: int
    recent_runs: list[JobRunOut]
