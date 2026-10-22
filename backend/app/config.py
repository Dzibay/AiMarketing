from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _dotenv() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file():
            env = parent / ".env"
            return env if env.is_file() else None
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_dotenv(), extra="ignore")

    app_name: str = "AiMarketing"

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "aimarketing"
    db_user: str = "aimarketing"
    db_password: str = "aimarketing"

    public_base_url: str = ""

    admin_password: str = ""
    admin_token_secret: str = ""
    admin_token_ttl_hours: int = 12

    web_session_secret: str = ""
    web_session_ttl_days: int = 30

    yandex_client_id: str = ""
    yandex_client_secret: str = ""
    # Скоупы одним запросом: вход + Метрика + Директ.
    yandex_oauth_scopes: str = "login:info login:email metrika:read direct:api"

    # Fernet-ключ или любая длинная строка — из неё выводится ключ.
    token_encrypt_secret: str = ""

    @property
    def dsn(self) -> str:
        return (
            f"host={self.db_host} port={self.db_port} dbname={self.db_name} "
            f"user={self.db_user} password={self.db_password}"
        )


settings = Settings()
