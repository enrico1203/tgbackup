from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_secret: str = "change-me-before-production"
    data_dir: Path = Path("/data")

    # Written into the image by the release build, from the git tag being published.
    # An image built from a working copy keeps "dev", which is what tells the update
    # check to stay quiet: there is nothing to compare a working copy against.
    app_version: str = "dev"

    token_ttl_hours: int = 24 * 30

    # Protocol ceiling: 8000 parts of 512KB.
    part_size_kb: int = 512
    max_parts_per_file: int = 8000
    max_connections: int = 20

    # Default split thresholds, chosen from the account's Premium status.
    split_premium: int = 3_900_000_000
    split_standard: int = 1_900_000_000

    scheduler_tick_seconds: int = 10
    # The slow loop: the silence alarm and the scheduled channel checks. Both are
    # answered in days and hours, so asking more often would only cost queries.
    watcher_tick_seconds: int = 3600
    progress_interval_seconds: float = 1.0

    # A single Telegram call that never comes back. Telethon returns a bare future for
    # every request and never times it out, so a part whose answer the server drops
    # would be awaited for ever, with the job holding its account slot and moving no
    # bytes. The value has to clear what Telethon may legitimately spend inside one
    # call: it retries `request_retries` times, 5 by default, sleeping up to
    # `flood_sleep_threshold`, 60s by default, on each flood wait, so about 300s of
    # honest waiting. Ten minutes leaves room above that and is still far below any
    # speed a 512KB part could arrive at.
    telegram_request_timeout: float = 600.0

    # rclone timeouts. These are safety nets against a remote that never answers, not
    # work limits: listing and preview stop on their own once they have what they need,
    # while a job scan can legitimately take a long time on huge remotes.
    rclone_check_timeout: float = 600.0
    rclone_preview_timeout: float = 600.0
    rclone_list_timeout: float = 6 * 3600.0

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tgbackup.db"

    @property
    def restore_dir(self) -> Path:
        return self.data_dir / "restore"

    @property
    def rclone_config_path(self) -> Path:
        return self.data_dir / "rclone.conf"


settings = Settings()
