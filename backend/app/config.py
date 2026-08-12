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
    # bytes. The value has to clear what may legitimately be spent inside one call. On a
    # part that is now the round trip and nothing else, since `send_transfer_request`
    # owns the retries and answers a flood wait outside the awaited future; on the calls
    # that still go through Telethon it is `request_retries` attempts, 5 by default,
    # sleeping up to `flood_sleep_threshold` on each flood wait, so around 300s. Ten
    # minutes leaves room above that and is still far below any speed a 512KB part could
    # arrive at.
    telegram_request_timeout: float = 600.0

    # The deadline of one part, which is a different question from the one above. Ten
    # minutes is the right length for "this call is never coming back" and far too long
    # for "this part is not coming back": measured on this installation, Telegram takes
    # two or three hundred megabytes and then stops answering the part requests without
    # ever sending a flood wait, and the transfer sat at zero for the whole ten minutes
    # before throwing the slice away and starting it again from the first byte, which on
    # a file of several gigabytes never finishes. A 512KB part that has not been answered
    # in two minutes is not answered because of the line: twenty connections that slow
    # would be moving under a hundred kilobytes a second between them. So the part is
    # sent again on a connection built for it, which costs one round trip instead of the
    # whole slice. See `send_transfer_request`.
    telegram_part_timeout: float = 120.0

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
