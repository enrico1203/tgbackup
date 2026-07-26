from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_secret: str = "change-me-before-production"
    data_dir: Path = Path("/data")

    token_ttl_hours: int = 24 * 30

    # Protocol ceiling: 8000 parts of 512KB.
    part_size_kb: int = 512
    max_parts_per_file: int = 8000
    max_connections: int = 20

    # Default split thresholds, chosen from the account's Premium status.
    split_premium: int = 3_900_000_000
    split_standard: int = 1_900_000_000

    scheduler_tick_seconds: int = 10
    progress_interval_seconds: float = 1.0

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
