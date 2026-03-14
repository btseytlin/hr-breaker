import hashlib
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field

DocumentKind = Literal["resume", "pdf", "note", "paper", "other"]


class Profile(BaseModel):
    """Persisted archive profile for a single candidate."""

    id: str
    display_name: str
    first_name: str | None = None
    last_name: str | None = None
    instructions: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @computed_field
    @property
    def full_name(self) -> str | None:
        parts = [part for part in (self.first_name, self.last_name) if part]
        if not parts:
            return None
        return " ".join(parts)


class ProfileDocument(BaseModel):
    """Normalized document stored within a profile archive."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    profile_id: str
    kind: DocumentKind = "other"
    title: str
    source_name: str
    source_url: str | None = None
    mime_type: str | None = None
    content_text: str
    included_by_default: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)

    @computed_field
    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.content_text.encode("utf-8")).hexdigest()

    @computed_field
    @property
    def preview_text(self) -> str:
        collapsed = " ".join(self.content_text.split())
        if len(collapsed) <= 180:
            return collapsed
        return f"{collapsed[:177]}..."


class RankedProfileDocument(BaseModel):
    """Profile document scored against a job posting."""

    document: ProfileDocument
    lexical_score: float
    keyword_score: float
    vector_score: float | None = None
    score: float
    snippet: str


# --- Extraction models ---

class ExperienceEntry(BaseModel):
    employer: str
    title: str
    start: str
    end: str
    bullets: list[str] = Field(default_factory=list)


class EducationEntry(BaseModel):
    institution: str
    degree: str
    field: str | None = None
    start: str | None = None
    end: str | None = None
    notes: list[str] = Field(default_factory=list)


class ProjectEntry(BaseModel):
    name: str
    description: str
    url: str | None = None
    bullets: list[str] = Field(default_factory=list)


class SkillsEntry(BaseModel):
    technical: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)


class PersonalInfo(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin: str | None = None
    github: str | None = None
    website: str | None = None
    other_links: list[str] = Field(default_factory=list)


class DocumentExtraction(BaseModel):
    """Structured facts extracted from a profile document."""

    personal_info: PersonalInfo = Field(default_factory=PersonalInfo)
    summary: list[str] = Field(default_factory=list)
    experience: list[ExperienceEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    skills: SkillsEntry = Field(default_factory=SkillsEntry)
    projects: list[ProjectEntry] = Field(default_factory=list)
    publications: list[str] = Field(default_factory=list)


def _has_text(value: str | None) -> bool:
    return bool(value and value.strip())


def extraction_has_signal(extraction: DocumentExtraction) -> bool:
    """Return True when extraction contains any usable structured evidence."""
    personal_info = extraction.personal_info
    if any(
        _has_text(value)
        for value in [
            personal_info.name,
            personal_info.email,
            personal_info.phone,
            personal_info.linkedin,
            personal_info.github,
            personal_info.website,
        ]
    ):
        return True
    if any(_has_text(link) for link in personal_info.other_links):
        return True
    if any(_has_text(item) for item in extraction.summary):
        return True
    if extraction.experience or extraction.education or extraction.projects:
        return True
    if any(_has_text(item) for item in extraction.publications):
        return True
    skills = extraction.skills
    return any([skills.technical, skills.languages, skills.certifications, skills.awards])


def get_document_extraction(doc: ProfileDocument) -> DocumentExtraction | None:
    """Return only authoritative extraction data for a document."""
    status = str(doc.metadata.get("extraction_status") or "").lower()
    if status in {"failed", "empty"}:
        return None
    raw = doc.metadata.get("extraction")
    if not raw:
        return None
    try:
        extraction = DocumentExtraction.model_validate(raw)
    except Exception:
        return None
    return extraction if extraction_has_signal(extraction) else None


def document_needs_extraction(doc: ProfileDocument) -> bool:
    status = str(doc.metadata.get("extraction_status") or "").lower()
    if status in {"failed", "empty"}:
        return True
    return get_document_extraction(doc) is None