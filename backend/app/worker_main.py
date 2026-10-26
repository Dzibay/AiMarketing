"""Отдельный процесс фоновых воркеров (Docker service `worker`).

  python -m app.worker_main
"""

from __future__ import annotations

import logging
import signal
import threading

from app.db import ensure_schema, pool
from app.workers import start_workers, stop_workers

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app.worker_main")


def main() -> None:
    ensure_schema()
    start_workers()
    log.info("Workers started (embedded loops + advisory locks)")
    done = threading.Event()

    def _shutdown(*_args) -> None:
        log.info("Stopping workers…")
        stop_workers()
        done.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    done.wait()
    pool.close()
    log.info("Workers stopped")


if __name__ == "__main__":
    main()
