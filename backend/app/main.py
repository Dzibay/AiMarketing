import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.admin import admin_public, router as admin_router
from app.api.ai import router as ai_router
from app.api.negative_lists import router as negative_lists_router
from app.api.overview import router as overview_router
from app.api.site import router as site_router
from app.api.web import router as web_router
from app.api.workspaces import router as workspaces_router
from app.config import settings
from app.db import ensure_schema, pool
from app.settings_store import bootstrap_settings
from app.workers import start_workers, stop_workers

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not settings.admin_password:
        log.warning("ADMIN_PASSWORD не задан — вход в админку закрыт")
    if not settings.yandex_client_id or not settings.yandex_client_secret:
        log.warning("YANDEX_CLIENT_ID/SECRET не заданы — OAuth Яндекса закрыт")
    if not settings.token_encrypt_secret:
        log.warning("TOKEN_ENCRYPT_SECRET не задан — хранение токенов недоступно")
    if not settings.public_base_url:
        log.warning("PUBLIC_BASE_URL не задан — callback OAuth и ссылки юрстраниц могут быть пустыми")
    ensure_schema()
    bootstrap_settings()
    if settings.run_embedded_workers:
        start_workers()
        log.info("Embedded workers enabled (RUN_EMBEDDED_WORKERS)")
    else:
        log.info("Embedded workers disabled — используйте сервис worker")
    yield
    if settings.run_embedded_workers:
        stop_workers()
    pool.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


app.include_router(site_router, prefix="/api")
app.include_router(web_router, prefix="/api")
app.include_router(overview_router, prefix="/api")
app.include_router(negative_lists_router, prefix="/api")
app.include_router(workspaces_router, prefix="/api")
app.include_router(ai_router, prefix="/api")
app.include_router(admin_public, prefix="/api/admin")
app.include_router(admin_router, prefix="/api/admin")
