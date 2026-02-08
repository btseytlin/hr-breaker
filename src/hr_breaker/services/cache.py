import json
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
