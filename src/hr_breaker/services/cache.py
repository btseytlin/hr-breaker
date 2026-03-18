import hashlib
import json
from datetime import datetime
from pathlib import Path

from hr_breaker.config import get_settings
from hr_breaker.models import ResumeSource


class ResumeCache:
    """File-based cache for resume sources."""

    def __init__(self):
        self.cache_dir = get_settings().cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, checksum: str) -> Path:
        return self.cache_dir / f"{checksum}.json"

    def get(self, checksum: str) -> ResumeSource | None:
        path = self._path(checksum)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return ResumeSource(**data)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                return None
        return None

    def put(self, resume: ResumeSource) -> None:
        path = self._path(resume.checksum)
        path.write_text(resume.model_dump_json(), encoding="utf-8")
        path.touch()

    def touch(self, checksum: str) -> None:
        path = self._path(checksum)
        if path.exists():
            path.touch()

    def delete(self, checksum: str) -> None:
        path = self._path(checksum)
        if path.exists():
            path.unlink()

    def exists(self, checksum: str) -> bool:
        return self._path(checksum).exists()

    def list_all(self) -> list[ResumeSource]:
        resumes = []
        paths = sorted(self.cache_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                resumes.append(ResumeSource(**data))
            except Exception:
                continue
        return resumes


class JobCache:
    """File-based cache for job posting texts."""

    def __init__(self):
        self.cache_dir = get_settings().cache_dir / "jobs"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def checksum(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def _path(self, checksum: str) -> Path:
        return self.cache_dir / f"{checksum}.json"

    def get(self, checksum: str) -> dict | None:
        path = self._path(checksum)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, KeyError):
                return None
        return None

    def put(self, text: str, source: str | None = None) -> str:
        cs = self.checksum(text)
        path = self._path(cs)
        data = {"checksum": cs, "text": text, "source": source, "timestamp": datetime.now().isoformat()}
        path.write_text(json.dumps(data), encoding="utf-8")
        path.touch()
        return cs

    def delete(self, checksum: str) -> None:
        path = self._path(checksum)
        if path.exists():
            path.unlink()

    def touch(self, checksum: str) -> None:
        path = self._path(checksum)
        if path.exists():
            path.touch()

    def list_all(self) -> list[dict]:
        jobs = []
        paths = sorted(self.cache_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                jobs.append(data)
            except Exception:
                continue
        return jobs
