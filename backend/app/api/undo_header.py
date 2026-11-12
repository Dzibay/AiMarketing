"""Номер записи журнала с откатом — в заголовке ответа.

Запрос кабинета, который поменял что-то в Директе и оставил ровно одну запись с откатом, отдаёт её номер
в `X-Undo-Log`: кабинет показывает «Отменить» прямо в сообщении о результате. Фоновые задачи кладут
номер в результат сами (`job_handlers.run_job`).
"""

from __future__ import annotations

from app.journal import collect_undoable, single_undoable

HEADER = b"x-undo-log"


class UndoLogHeader:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        with collect_undoable() as ids:

            async def send_with_header(message):
                if message["type"] == "http.response.start":
                    log_id = single_undoable(ids)
                    if log_id is not None:
                        message = {**message, "headers": [*message.get("headers", []), (HEADER, str(log_id).encode())]}
                await send(message)

            await self.app(scope, receive, send_with_header)
