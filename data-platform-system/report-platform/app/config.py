from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "团购数据指标系统"
    database_url: str = "postgresql+psycopg://tg_report:tg_report@postgres:5432/tg_report"
    upload_dir: Path = Path("/data/uploads")
    preset_path: Path = Path("/app/config/report_presets.yml")
    platform_profile_path: Path = Path("/app/config/platform_profiles.yml")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
