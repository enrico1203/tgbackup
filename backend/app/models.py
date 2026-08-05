from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


# A weekly window, one character per hour, Monday 00:00 to Sunday 23:00 in the timezone
# of the installation: "1" the job may run, "0" it may not. All ones is the default and
# means no restriction, which is what every job created before the feature existed gets.
# See sync/window.py.
SCHEDULE_ALWAYS = "1" * 168

# Below this many known files the percentage guard is not applied: on a job holding eight
# files, deleting three is 37% and means nothing, while the same proportion on a job of
# thousands is the signature of a source that is not there. The absolute limit, when the
# user sets one, is checked at every size.
GUARD_MIN_ENTRIES = 10

# The states in which an entry still has its messages in the channel, and can therefore be
# downloaded or restored. A trashed file is one the source no longer has: it is exactly
# what somebody comes looking for, so it stays readable until the retention purges it.
READABLE_STATES = ("uploaded", "trashed")

# An entry rebuilt by reading the channel has no date: the caption of a message carries the
# name, the folder and the part number, never the modification time. This value marks that
# absence, and the first scan of the job adopts the date of the source instead of taking
# the file for modified and uploading it again. See sync/runner.py, _diff.
MTIME_UNKNOWN = -1


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TelegramAccount(Base):
    __tablename__ = "telegram_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(128))
    api_id: Mapped[int] = mapped_column(Integer)
    api_hash_enc: Mapped[str] = mapped_column(Text)
    phone: Mapped[str] = mapped_column(String(32))

    session_enc: Mapped[str | None] = mapped_column(Text)
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger)
    first_name: Mapped[str | None] = mapped_column(String(128))
    username: Mapped[str | None] = mapped_column(String(128))
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    default_part_size: Mapped[int] = mapped_column(BigInteger, default=1_900_000_000)

    # How many jobs may upload at the same time on this account. The ceiling of 20
    # connections per data center is divided among them, so raising this number does
    # not increase total bandwidth: it spreads the same bandwidth across more jobs.
    max_concurrent_jobs: Mapped[int] = mapped_column(Integer, default=2)

    # pending_code, pending_password, connected, disconnected, error
    status: Mapped[str] = mapped_column(String(32), default="pending_code")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    channels: Mapped[list[Channel]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("account_id", "tg_id", name="uq_channel_account_tg"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_accounts.id", ondelete="CASCADE")
    )
    tg_id: Mapped[int] = mapped_column(BigInteger)
    access_hash: Mapped[int | None] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(256))
    username: Mapped[str | None] = mapped_column(String(128))
    is_private: Mapped[bool] = mapped_column(Boolean, default=True)
    kind: Mapped[str] = mapped_column(String(32), default="channel")
    participants: Mapped[int | None] = mapped_column(Integer)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Automatic integrity check. Every this many days, at this local hour, the index of
    # the channel is compared with the messages; 0 days is off. `check_repair` marks the
    # damaged files for re-upload, which is the one setting here that spends bandwidth
    # without being asked. The outcome is kept on the row rather than in the in-memory
    # task registry, which holds twenty tasks and forgets them at every restart: a check
    # that runs once a month has to be readable a month later.
    # See sync/scheduler.py, _check_channels.
    check_interval_days: Mapped[int] = mapped_column(Integer, default=0)
    check_hour: Mapped[int] = mapped_column(Integer, default=4)
    check_repair: Mapped[bool] = mapped_column(Boolean, default=False)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_check_result: Mapped[str | None] = mapped_column(Text)

    account: Mapped[TelegramAccount] = relationship(back_populates="channels")


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[int] = mapped_column(ForeignKey("telegram_accounts.id", ondelete="CASCADE"))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))

    # local: folder mounted in the container. rclone: remote read through the API, no mount.
    source_type: Mapped[str] = mapped_column(String(16), default="local")
    local_path: Mapped[str] = mapped_column(Text, default="")
    # Full rclone path, for example "mycloud-crypt:" or "gdrive:Photos".
    remote: Mapped[str | None] = mapped_column(Text)

    interval_hours: Mapped[float] = mapped_column(Float, default=24.0)
    # 0 means no limit. Above zero it is the ceiling of files examined per second.
    scan_files_per_sec: Mapped[int] = mapped_column(Integer, default=0)
    part_size_bytes: Mapped[int] = mapped_column(BigInteger, default=1_900_000_000)

    # One pattern per line. Empty include means everything, exclude wins over include.
    # See sync/filters.py: the same matcher is used for both kinds of source.
    include_globs: Mapped[str] = mapped_column(Text, default="")
    exclude_globs: Mapped[str] = mapped_column(Text, default="")
    # 0 means no ceiling. Above zero, files larger than this are left out of the backup.
    max_file_size: Mapped[int] = mapped_column(BigInteger, default=0)

    # The hours of the week the job may run in, and whether a run already started has to
    # stop when the window closes instead of going through to the end.
    schedule_hours: Mapped[str] = mapped_column(Text, default=SCHEDULE_ALWAYS)
    stop_outside_window: Mapped[bool] = mapped_column(Boolean, default=False)

    # Bytes per second this job may move, 0 for no limit. It applies in the hours marked
    # "2" in the window, or at every hour when none is. See telegram/throttle.py.
    throttle_bps: Mapped[int] = mapped_column(BigInteger, default=0)

    # How much of the backup a single run is allowed to delete. A source that fails to
    # mount lists nothing, and without these the diff would take every known file for
    # removed and empty the channel message by message. 0 turns a limit off, and the
    # percentage is not applied to a job holding fewer than GUARD_MIN_ENTRIES files.
    # See sync/runner.py, _diff.
    delete_guard_percent: Mapped[int] = mapped_column(Integer, default=20)
    delete_guard_files: Mapped[int] = mapped_column(Integer, default=0)
    # Set by the user after reading what the guard stopped, and consumed by the next run:
    # an acknowledgement is worth exactly one execution.
    delete_guard_bypass: Mapped[bool] = mapped_column(Boolean, default=False)

    # How many days a file that disappeared from the source is kept in the channel before
    # its messages are deleted. 0 is the historical behaviour, deletion within the run
    # that noticed. See sync/runner.py, _purge_trash.
    trash_days: Mapped[int] = mapped_column(Integer, default=0)

    # A job that stops succeeding says nothing on its own: nobody notices a backup that
    # is not happening. `silence_alerted_at` is when the last alarm went out, cleared by
    # a run that succeeds, and the switch is for a job deliberately parked.
    # See sync/scheduler.py, _check_silence.
    silence_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    silence_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # idle, running, error
    status: Mapped[str] = mapped_column(String(32), default="idle")
    phase: Mapped[str | None] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(Text)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    account: Mapped[TelegramAccount] = relationship()
    channel: Mapped[Channel] = relationship()


class DownloadJob(Base):
    """The inverse of a sync job: a channel poured back into a folder or a remote.

    It owns no file table of its own. What has to be downloaded is the channel index
    minus what already sits at the destination, recomputed at every run: the destination
    is the state, so there is nothing that can drift away from it.
    """

    __tablename__ = "download_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[int] = mapped_column(ForeignKey("telegram_accounts.id", ondelete="CASCADE"))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))

    # local: folder mounted in the container, and it has to be writable. rclone: remote
    # written through the API, no mount.
    dest_type: Mapped[str] = mapped_column(String(16), default="local")
    local_path: Mapped[str] = mapped_column(Text, default="")
    remote: Mapped[str | None] = mapped_column(Text)

    interval_hours: Mapped[float] = mapped_column(Float, default=24.0)

    # Same weekly window a sync job has: the direction the bytes travel in changes
    # nothing about when the line is free.
    schedule_hours: Mapped[str] = mapped_column(Text, default=SCHEDULE_ALWAYS)
    stop_outside_window: Mapped[bool] = mapped_column(Boolean, default=False)
    throttle_bps: Mapped[int] = mapped_column(BigInteger, default=0)

    # Same alarm a sync job has: a channel that stops being poured back is as quiet as a
    # folder that stops being backed up.
    silence_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    silence_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # idle, running, error
    status: Mapped[str] = mapped_column(String(32), default="idle")
    phase: Mapped[str | None] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(Text)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    account: Mapped[TelegramAccount] = relationship()
    channel: Mapped[Channel] = relationship()


class DownloadRun(Base):
    __tablename__ = "download_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("download_jobs.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # running, ok, error, stopped
    status: Mapped[str] = mapped_column(String(16), default="running")
    # Files the channel index holds for this channel.
    indexed_files: Mapped[int] = mapped_column(Integer, default=0)
    indexed_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    # Already at the destination when the run started, and therefore skipped.
    present_files: Mapped[int] = mapped_column(Integer, default=0)
    present_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    downloaded_files: Mapped[int] = mapped_column(Integer, default=0)
    downloaded_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    failed_files: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class FileEntry(Base):
    __tablename__ = "file_entries"
    __table_args__ = (
        UniqueConstraint("job_id", "rel_path", name="uq_file_job_path"),
        Index("ix_file_job_state", "job_id", "state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("sync_jobs.id", ondelete="CASCADE"))
    rel_path: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    mtime_ns: Mapped[int] = mapped_column(BigInteger)

    # pending, uploading, uploaded, stale, trashed, to_delete, error
    state: Mapped[str] = mapped_column(String(16), default="pending")
    parts_total: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the file stopped being at the source. Set on the way into `trashed` and cleared
    # if the file comes back, and it is what the retention is counted from. A row carrying
    # it still owns its parts: the messages are in the channel until the purge.
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    parts: Mapped[list[FilePart]] = relationship(
        back_populates="file", cascade="all, delete-orphan", order_by="FilePart.part_index"
    )


class FilePart(Base):
    __tablename__ = "file_parts"
    __table_args__ = (UniqueConstraint("file_id", "part_index", name="uq_part_file_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("file_entries.id", ondelete="CASCADE"))
    part_index: Mapped[int] = mapped_column(Integer)
    offset: Mapped[int] = mapped_column(BigInteger)
    size: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    file: Mapped[FileEntry] = relationship(back_populates="parts")


class Setting(Base):
    """Global settings. The value may be encrypted, as indicated by `encrypted`."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    value: Mapped[str] = mapped_column(Text)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("sync_jobs.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # running, ok, error, stopped
    status: Mapped[str] = mapped_column(String(16), default="running")
    scanned: Mapped[int] = mapped_column(Integer, default=0)
    added: Mapped[int] = mapped_column(Integer, default=0)
    modified: Mapped[int] = mapped_column(Integer, default=0)
    # Deleted from the channel during this run, whether they had been in the trash or
    # went straight out because the job keeps none.
    removed: Mapped[int] = mapped_column(Integer, default=0)
    # Moved to the trash by this run, and brought back out of it because they reappeared
    # at the source with the same size and date, which costs no upload at all.
    trashed: Mapped[int] = mapped_column(Integer, default=0)
    revived: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_files: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    error: Mapped[str | None] = mapped_column(Text)
