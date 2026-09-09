from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "小说写作工作台"
    database_url: str = "sqlite:///./data/noveltools.db"
    data_dir: Path = Path("./data")
    default_llm_base_url: str = "https://api.openai.com/v1"
    default_llm_model: str = "gpt-4o-mini"
    runtime_environment: str = "PRODUCTION"
    canvas_api_url: str = "http://127.0.0.1:3010/api"
    prose_workflow_id: str = "8a1d6972-2288-47f6-883d-dbfc589c1a9f"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


settings = Settings()
