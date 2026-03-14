"""Background extraction worker — module-level singleton."""
import asyncio
import logging
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

logger = logging.getLogger(__name__)

DocStatus = Literal["pending", "running", "done", "error"]


class ExtractionWorker:
    def __init__(self, max_workers: int = 2):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="extractor")
        self._status: dict[str, DocStatus] = {}
        self._lock = threading.Lock()
        self.log_queue: queue.Queue[dict] = queue.Queue()

    def submit(self, profile_id: str, doc_ids: list[str], overrides: dict | None = None) -> None:
        """Queue documents for background extraction. Already-active jobs are skipped."""
        job_overrides = dict(overrides or {})
        with self._lock:
            for doc_id in doc_ids:
                if self._status.get(doc_id) not in ("pending", "running"):
                    self._status[doc_id] = "pending"
                    self._executor.submit(self._run, profile_id, doc_id, job_overrides)

    def get_status(self, doc_id: str) -> DocStatus | None:
        with self._lock:
            return self._status.get(doc_id)

    def any_active(self) -> bool:
        with self._lock:
            return any(s in ("pending", "running") for s in self._status.values())

    def drain_logs(self) -> list[dict]:
        """Return and clear all queued log messages."""
        events = []
        while True:
            try:
                events.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def _run(self, profile_id: str, doc_id: str, overrides: dict | None = None) -> None:
        from hr_breaker.config import settings_override
        from hr_breaker.services.profile_store import ProfileStore

        store = ProfileStore()
        doc = store.get_document(profile_id, doc_id)
        label = doc.title if doc else doc_id

        with self._lock:
            self._status[doc_id] = "running"
        self.log_queue.put({"level": "INFO", "message": f"Extracting: {label}"})

        try:
            loop = asyncio.new_event_loop()
            try:
                with settings_override(overrides):
                    loop.run_until_complete(store.extract_document_content(profile_id, doc_id))
            finally:
                loop.close()
            # Warn when the LLM returned an empty extraction so the user knows
            # this doc will fall back to raw-text inclusion in synthesis.
            updated_doc = store.get_document(profile_id, doc_id)
            if updated_doc is not None:
                status = str(updated_doc.metadata.get("extraction_status") or "").lower()
                if status == "empty":
                    logger.warning("Extraction for '%s' produced no usable content", label)
                    self.log_queue.put({"level": "WARNING", "message": f"Extraction empty (no content found): {label}"})
            with self._lock:
                self._status[doc_id] = "done"
            self.log_queue.put({"level": "INFO", "message": f"Extracted: {label}"})
        except Exception as exc:
            with self._lock:
                self._status[doc_id] = "error"
            self.log_queue.put({"level": "ERROR", "message": f"Extraction failed ({label}): {exc}"})
            logger.error("Extraction failed for '%s': %s", label, exc)


# Module-level singleton
extraction_worker = ExtractionWorker()
