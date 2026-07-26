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

    # pending, uploading, uploaded, stale, to_delete, error
    state: Mapped[str] = mapped_column(String(16), default="pending")
    parts_total: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    removed: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_files: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    error: Mapped[str | None] = mapped_column(Text)
