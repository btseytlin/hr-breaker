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
        self._cancelled_doc_ids: set[str] = set()
        self._lock = threading.Lock()
        self.log_queue: queue.Queue[dict] = queue.Queue()

    def submit(self, profile_id: str, doc_ids: list[str], overrides: dict | None = None) -> None:
        """Queue documents for background extraction. Already-active jobs are skipped."""
        job_overrides = dict(overrides or {})
        with self._lock:
            self._submit_locked(profile_id, doc_ids, job_overrides, reset_finished=False)

    def resubmit(self, profile_id: str, doc_ids: list[str], overrides: dict | None = None) -> None:
        """Re-queue finished documents without exposing worker internals to callers."""
        job_overrides = dict(overrides or {})
        with self._lock:
            self._submit_locked(profile_id, doc_ids, job_overrides, reset_finished=True)

    def cancel(self, doc_ids: list[str]) -> None:
        """Cancel queued status tracking for documents that were deleted."""
        with self._lock:
            for doc_id in doc_ids:
                self._cancelled_doc_ids.add(doc_id)
                self._status.pop(doc_id, None)

    def _submit_locked(
        self,
        profile_id: str,
        doc_ids: list[str],
        overrides: dict,
        *,
        reset_finished: bool,
    ) -> None:
        for doc_id in doc_ids:
            status = self._status.get(doc_id)
            if reset_finished and status in ("done", "error"):
                self._status.pop(doc_id, None)
                status = None
            self._cancelled_doc_ids.discard(doc_id)
            if status in ("pending", "running"):
                continue
            self._status[doc_id] = "pending"
            self._executor.submit(self._run, profile_id, doc_id, overrides)

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
            if doc_id in self._cancelled_doc_ids:
                self._status.pop(doc_id, None)
                return
            self._status[doc_id] = "running"
        self.log_queue.put({"level": "INFO", "message": f"Extracting: {label}"})

        try:
            loop = asyncio.new_event_loop()
            try:
                with settings_override(overrides):
                    updated_doc = loop.run_until_complete(store.extract_document_content(profile_id, doc_id))
            finally:
                loop.close()

            if updated_doc is None:
                with self._lock:
                    self._status.pop(doc_id, None)
                self.log_queue.put({"level": "INFO", "message": f"Skipped deleted document: {label}"})
                return

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
