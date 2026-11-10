"""Single GPU worker (CLAUDE.md 4.2): one job at a time, in submission order; the reader loads once."""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session, sessionmaker

from naijawatchtower.api import audit
from naijawatchtower.api.db import ResultSheet, SheetJob, utcnow
from naijawatchtower.api.pipeline import apply_reading
from naijawatchtower.ocr.types import SheetReader


class JobQueue:
    """FIFO queue served by one background thread."""

    def __init__(self, sessions: sessionmaker[Session], reader_factory: Callable[[], SheetReader],
                 reader_kind: str = "vlm") -> None:
        self.sessions = sessions
        self.reader_factory = reader_factory
        self.reader: SheetReader | None = None
        self.reader_kind = reader_kind
        self.q: queue.Queue[int] = queue.Queue()
        self.thread = threading.Thread(target=self._loop, name="gpu-worker", daemon=True)
        self.thread.start()

    def submit(self, db: Session, sheet_id: int) -> SheetJob:
        """Record a queued job (caller commits) and hand it to the worker after commit."""
        job = SheetJob(sheet_id=sheet_id, state="queued")
        db.add(job)
        db.flush()
        return job

    def enqueue(self, job_id: int) -> None:
        self.q.put(job_id)

    def drain(self, timeout: float = 60.0) -> None:
        """Block until every queued job has been processed (used by tests and the seeder)."""
        deadline = time.time() + timeout
        while self.q.unfinished_tasks and time.time() < deadline:
            time.sleep(0.01)

    def _loop(self) -> None:
        while True:
            job_id = self.q.get()
            try:
                self.process(job_id)
            finally:
                self.q.task_done()

    def process(self, job_id: int) -> None:
        """Read one sheet and store the result; failures are recorded, never raised."""
        with self.sessions() as db:
            job = db.get(SheetJob, job_id)
            if job is None:
                return
            sheet = db.get(ResultSheet, job.sheet_id)
            job.state, job.started_at = "running", utcnow()
            db.commit()
            start = time.perf_counter()
            try:
                if self.reader is None:
                    self.reader = self.reader_factory()        # model loads once, on the worker thread
                reading = self.reader.read(Path(sheet.image_path))  # type: ignore[arg-type,union-attr]
                apply_reading(db, sheet, reading, self.reader_kind)  # type: ignore[arg-type]
                job.state = "done"
                job.vram_peak_mb = reading.vram_peak_mb or None
            except Exception as err:  # noqa: BLE001 - a failed read must not stop the queue
                db.rollback()
                job = db.get(SheetJob, job_id)
                sheet = db.get(ResultSheet, job.sheet_id)  # type: ignore[union-attr]
                job.state, job.error = "failed", f"{type(err).__name__}: {err}"[:500]  # type: ignore[union-attr]
                sheet.status = "UNREADABLE"  # type: ignore[union-attr]
                sheet.pipeline_outcome = "READ_FAILED"  # type: ignore[union-attr]
                audit.append(db, "system", "worker", "job_failed", {"sheet": sheet.id}, sheet.id)  # type: ignore[union-attr]
            job.finished_at = utcnow()  # type: ignore[union-attr]
            job.seconds = round(time.perf_counter() - start, 3)  # type: ignore[union-attr]
            db.commit()
