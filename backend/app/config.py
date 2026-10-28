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

    # SMTP для сброса пароля (если пусто — ссылка пишется в лог backend).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_ssl: bool = False
    smtp_starttls: bool = True

    # Telegram-бот для алертов по правилам (необязательно).
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    # Секрет для setWebhook (заголовок X-Telegram-Bot-Api-Secret-Token).
    telegram_webhook_secret: str = ""

    # Вордстат (Search API Yandex Cloud): API-ключ сервисного аккаунта с ролью search-api.webSearch.user
    # и id каталога. Ключ сервиса для тех, кто не подключил свой в настройках; пусто — только свои ключи.
    wordstat_api_key: str = ""
    wordstat_folder_id: str = ""

    # Фоновые циклы внутри процесса API (для локального запуска).
    # В Docker API ставит false, отдельный сервис worker запускает циклы сам.
    run_embedded_workers: bool = True
    # Сколько задач очереди процесс выполняет параллельно.
    worker_concurrency: int = 3

    @property
    def dsn(self) -> str:
        return (
            f"host={self.db_host} port={self.db_port} dbname={self.db_name} "
            f"user={self.db_user} password={self.db_password}"
        )


settings = Settings()
