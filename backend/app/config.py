from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_secret: str = "cambiami-subito-in-produzione"
    data_dir: Path = Path("/data")

    token_ttl_hours: int = 24 * 30

    # Tetto di protocollo: 8000 parti da 512KB.
    part_size_kb: int = 512
    max_parts_per_file: int = 8000
    max_connections: int = 20

    # Soglie di split di default, scelte in base allo stato Premium dell'account.
    split_premium: int = 3_900_000_000
    split_standard: int = 1_900_000_000

    scheduler_tick_seconds: int = 10
    progress_interval_seconds: float = 1.0

    # Timeout rclone. Sono reti di sicurezza contro un remote che non risponde, non
    # limiti di lavoro: elenco e anteprima si fermano da soli alle voci che servono,
    # mentre la scansione di un job puo durare a lungo su remote enormi.
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
