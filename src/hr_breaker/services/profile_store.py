import logging
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from hr_breaker.config import get_settings
from hr_breaker.models.profile import DocumentKind, Profile, ProfileDocument
from hr_breaker.services.pdf_parser import load_resume_content_from_upload
from hr_breaker.services.pdf_storage import sanitize_filename

logger = logging.getLogger(__name__)


class ProfileStore:
    """Persist local profile archives under the cache directory."""

    _path_locks_guard = threading.Lock()
    _path_locks: dict[str, threading.Lock] = {}

    def __init__(self, root_dir: Path | None = None):
        self.root_dir = (root_dir or get_settings().profile_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def list_profiles(self) -> list[Profile]:
        profiles: list[Profile] = []
        for path in sorted(self.root_dir.glob("*/profile.json")):
            try:
                profiles.append(Profile.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception as exc:
                logger.warning("Skipping corrupt profile file %s: %s", path, exc)
                continue
        return sorted(profiles, key=lambda profile: profile.updated_at, reverse=True)

    def get_profile(self, profile_id: str) -> Profile | None:
        try:
            path = self._profile_path(profile_id)
        except ValueError:
            return None
        if not path.exists():
            return None
        try:
            return Profile.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def create_profile(
        self,
        display_name: str,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        instructions: str | None = None,
    ) -> Profile:
        profile_id = self._unique_profile_id(display_name)
        profile = Profile(
            id=profile_id,
            display_name=display_name.strip(),
            first_name=first_name,
            last_name=last_name,
            instructions=instructions,
        )
        self.save_profile(profile)
        return profile

    def save_profile(self, profile: Profile) -> Profile:
        updated = profile.model_copy(update={"updated_at": datetime.now()})
        profile_dir = self._profile_dir(updated.id)
        profile_dir.mkdir(parents=True, exist_ok=True)
        self._documents_dir(updated.id).mkdir(parents=True, exist_ok=True)
        self._assets_dir(updated.id).mkdir(parents=True, exist_ok=True)
        self._write_json_atomically(
            self._profile_path(updated.id),
            updated.model_dump_json(indent=2),
        )
        return updated

    def rename_profile(self, profile_id: str, display_name: str) -> Profile | None:
        profile = self.get_profile(profile_id)
        if profile is None:
            return None
        return self.save_profile(profile.model_copy(update={"display_name": display_name.strip()}))

    def update_profile_details(
        self,
        profile_id: str,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        instructions: str | None = None,
    ) -> Profile | None:
        profile = self.get_profile(profile_id)
        if profile is None:
            return None
        return self.save_profile(
            profile.model_copy(
                update={
                    "first_name": first_name,
                    "last_name": last_name,
                    "instructions": instructions,
                }
            )
        )

    def delete_profile(self, profile_id: str) -> None:
        profile_dir = self._profile_dir(profile_id)
        try:
            shutil.rmtree(profile_dir)
        except Exception as exc:
            logger.error("Failed to delete profile directory %s: %s", profile_dir, exc)
            raise

    def list_documents(self, profile_id: str) -> list[ProfileDocument]:
        documents: list[ProfileDocument] = []
        try:
            document_dir = self._documents_dir(profile_id)
        except ValueError:
            return []
        for path in sorted(document_dir.glob("*.json")):
            try:
                documents.append(ProfileDocument.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception as exc:
                logger.warning("Skipping corrupt document file %s: %s", path, exc)
                continue
        return sorted(documents, key=lambda document: document.timestamp, reverse=True)

    def get_document(self, profile_id: str, document_id: str) -> ProfileDocument | None:
        try:
            path = self._document_path(profile_id, document_id)
        except ValueError:
            return None
        if not path.exists():
            return None
        try:
            return ProfileDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def add_upload(
        self,
        profile_id: str,
        *,
        filename: str,
        data: bytes,
        mime_type: str | None = None,
        title: str | None = None,
        source_url: str | None = None,
        included_by_default: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> ProfileDocument:
        content_text = load_resume_content_from_upload(filename, data)
        merged_metadata = {
            "original_filename": filename,
            "byte_size": len(data),
            **(metadata or {}),
        }
        return self.add_document(
            profile_id,
            kind=self._infer_document_kind(filename),
            title=title or self._default_title(filename),
            source_name=filename,
            mime_type=mime_type,
            content_text=content_text,
            source_url=source_url,
            included_by_default=included_by_default,
            metadata=merged_metadata,
            asset_bytes=data,
            asset_filename=filename,
        )

    def add_note(
        self,
        profile_id: str,
        *,
        title: str,
        content_text: str,
        included_by_default: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> ProfileDocument:
        return self.add_document(
            profile_id,
            kind="note",
            title=title.strip(),
            source_name=title.strip(),
            mime_type="text/plain",
            content_text=content_text,
            included_by_default=included_by_default,
            metadata=metadata,
        )

    def add_document(
        self,
        profile_id: str,
        *,
        kind: DocumentKind,
        title: str,
        source_name: str,
        content_text: str,
        source_url: str | None = None,
        mime_type: str | None = None,
        included_by_default: bool = True,
        metadata: dict[str, Any] | None = None,
        asset_bytes: bytes | None = None,
        asset_filename: str | None = None,
    ) -> ProfileDocument:
        profile = self.get_profile(profile_id)
        if profile is None:
            raise ValueError(f"Unknown profile: {profile_id}")

        candidate = ProfileDocument(
            profile_id=profile_id,
            kind=kind,
            title=title.strip(),
            source_name=source_name,
            source_url=source_url,
            mime_type=mime_type,
            content_text=content_text,
            included_by_default=included_by_default,
            metadata=dict(metadata or {}),
        )
        duplicate = self._find_duplicate_document(profile_id, candidate.checksum, candidate.source_name)
        base = duplicate or candidate
        asset_relative_path = self._write_asset(profile_id, base.id, asset_filename, asset_bytes)

        if duplicate is not None:
            new_meta = {**duplicate.metadata, **(metadata or {})}
            if asset_relative_path is not None:
                new_meta["asset_path"] = asset_relative_path
            document = duplicate.model_copy(
                update={
                    "kind": kind,
                    "title": title.strip(),
                    "source_name": source_name,
                    "source_url": source_url,
                    "mime_type": mime_type,
                    "content_text": content_text,
                    "included_by_default": included_by_default,
                    "metadata": new_meta,
                    "timestamp": datetime.now(),
                }
            )
        else:
            new_meta = dict(metadata or {})
            if asset_relative_path is not None:
                new_meta["asset_path"] = asset_relative_path
            document = candidate.model_copy(update={"metadata": new_meta})

        self._write_json_atomically(
            self._document_path(profile_id, document.id),
            document.model_dump_json(indent=2),
        )
        self.save_profile(profile)
        return document

    async def extract_document_content(self, profile_id: str, document_id: str) -> ProfileDocument | None:
        """Run extraction on a document and persist results in its metadata."""
        doc = self.get_document(profile_id, document_id)
        if doc is None:
            return None
        from hr_breaker.agents.extractor import extract_document
        from hr_breaker.models.profile import extraction_has_signal
        try:
            extraction = await extract_document(doc.content_text)
            status = "done" if extraction_has_signal(extraction) else "empty"
            new_meta = {**doc.metadata, "extraction": extraction.model_dump(), "extraction_status": status}
        except Exception:
            new_meta = {**doc.metadata, "extraction_status": "failed"}
            failed = doc.model_copy(update={"metadata": new_meta})
            if not self._write_document_if_present(profile_id, document_id, failed):
                logger.info("Skipping failed extraction persistence for deleted document %s", document_id)
                return None
            raise
        updated = doc.model_copy(update={"metadata": new_meta})
        if not self._write_document_if_present(profile_id, document_id, updated):
            logger.info("Skipping extraction persistence for deleted document %s", document_id)
            return None
        return updated

    def remove_document(self, profile_id: str, document_id: str) -> None:
        path = self._document_path(profile_id, document_id)
        with self._path_lock(path):
            if path.exists():
                path.unlink()
            asset_prefix = f"{document_id}_"
            for asset_path in self._assets_dir(profile_id).glob(f"{asset_prefix}*"):
                asset_path.unlink(missing_ok=True)
        profile = self.get_profile(profile_id)
        if profile is not None:
            self.save_profile(profile)

    def _write_document_if_present(
        self,
        profile_id: str,
        document_id: str,
        document: ProfileDocument,
    ) -> bool:
        return self._write_json_atomically(
            self._document_path(profile_id, document_id),
            document.model_dump_json(indent=2),
            require_existing=True,
        )

    @classmethod
    def _path_lock(cls, path: Path) -> threading.Lock:
        key = str(path)
        with cls._path_locks_guard:
            lock = cls._path_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                cls._path_locks[key] = lock
            return lock

    def _write_json_atomically(
        self,
        path: Path,
        payload: str,
        *,
        require_existing: bool = False,
    ) -> bool:
        tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        lock = self._path_lock(path)
        try:
            with lock:
                if require_existing and not path.exists():
                    return False
                tmp_path.write_text(payload, encoding="utf-8")
                os.replace(tmp_path, path)
        finally:
            tmp_path.unlink(missing_ok=True)
        return True


    def _find_duplicate_document(
        self,
        profile_id: str,
        checksum: str,
        source_name: str,
    ) -> ProfileDocument | None:
        for document in self.list_documents(profile_id):
            if document.checksum == checksum and document.source_name == source_name:
                return document
        return None

    def _write_asset(
        self,
        profile_id: str,
        document_id: str,
        filename: str | None,
        data: bytes | None,
    ) -> str | None:
        if filename is None or data is None:
            return None
        path = self._asset_path(profile_id, document_id, filename)
        path.write_bytes(data)
        return str(path.relative_to(self._profile_dir(profile_id)))

    def _unique_profile_id(self, display_name: str) -> str:
        base = sanitize_filename(display_name) or "profile"
        candidate = base
        index = 2
        while self._profile_dir(candidate).exists():
            candidate = f"{base}_{index}"
            index += 1
        return candidate

    def _default_title(self, filename: str) -> str:
        stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
        return stem or filename

    def _infer_document_kind(self, filename: str) -> DocumentKind:
        lowered = filename.lower()
        if lowered.endswith(".pdf"):
            if any(token in lowered for token in ("paper", "publication", "research")):
                return "paper"
            return "pdf"
        if any(token in lowered for token in ("resume", "cv")):
            return "resume"
        return "other"

    def _profile_dir(self, profile_id: str) -> Path:
        safe_profile_id = self._validate_identifier(profile_id, label="profile_id")
        path = (self.root_dir / safe_profile_id).resolve()
        if not path.is_relative_to(self.root_dir):
            raise ValueError(f"Invalid profile_id: {profile_id}")
        return path

    def _profile_path(self, profile_id: str) -> Path:
        return self._profile_dir(profile_id) / "profile.json"

    def _documents_dir(self, profile_id: str) -> Path:
        return self._profile_dir(profile_id) / "documents"

    def _assets_dir(self, profile_id: str) -> Path:
        return self._profile_dir(profile_id) / "assets"

    def _document_path(self, profile_id: str, document_id: str) -> Path:
        safe_document_id = self._validate_identifier(document_id, label="document_id")
        return self._documents_dir(profile_id) / f"{safe_document_id}.json"

    def _asset_path(self, profile_id: str, document_id: str, filename: str) -> Path:
        safe_document_id = self._validate_identifier(document_id, label="document_id")
        original = Path(filename)
        safe_stem = sanitize_filename(original.stem) or "asset"
        safe_name = f"{safe_document_id}_{safe_stem}{original.suffix.lower()}"
        return self._assets_dir(profile_id) / safe_name

    def _validate_identifier(self, value: str, *, label: str) -> str:
        if not value or value in {".", ".."}:
            raise ValueError(f"Invalid {label}: {value}")
        if Path(value).name != value or "/" in value or "\\" in value:
            raise ValueError(f"Invalid {label}: {value}")
        return value
