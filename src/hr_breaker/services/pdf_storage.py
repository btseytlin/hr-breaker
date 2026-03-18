import re
from datetime import datetime
from pathlib import Path

from hr_breaker.config import get_settings
from hr_breaker.models import GeneratedPDF

# Pattern: 4 digits, underscore, 4 digits, underscore (e.g. "0312_1423_")
RUN_ID_PREFIX_RE = re.compile(r"^\d{4}_\d{4}_")


def generate_run_id() -> str:
    """Generate a run ID from current timestamp: MMDD_HHMM."""
    return datetime.now().strftime("%m%d_%H%M")


def sanitize_filename(name: str) -> str:
    """Convert name to safe filename component."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


class PDFStorage:
    """Storage for generated PDFs - folder-based, no index file."""

    def __init__(self):
        self.output_dir = get_settings().output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_path(
        self,
        first_name: str | None,
        last_name: str | None,
        company: str,
        role: str | None = None,
        lang_code: str | None = None,
        run_id: str | None = None,
    ) -> Path:
        """Generate PDF path: {run_id}_{first}_{last}_{company}_{role}_{lang}.pdf"""
        parts = []
        if run_id:
            parts.append(run_id)
        if first_name:
            parts.append(sanitize_filename(first_name))
        if last_name:
            parts.append(sanitize_filename(last_name))
        parts.append(sanitize_filename(company))
        if role:
            parts.append(sanitize_filename(role))
        # Always append language code
        parts.append(lang_code if lang_code else "en")

        filename = "_".join(parts) + ".pdf"
        return self.output_dir / filename

    def generate_debug_dir(
        self, company: str, role: str | None = None, run_id: str | None = None
    ) -> Path:
        """Generate debug directory: output/{run_id}_debug_{company}_{role}/"""
        parts = []
        if run_id:
            parts.append(run_id)
        parts.extend(["debug", sanitize_filename(company)])
        if role:
            parts.append(sanitize_filename(role))
        debug_dir = self.output_dir / "_".join(parts)
        debug_dir.mkdir(parents=True, exist_ok=True)
        return debug_dir

    def list_all(self) -> list[GeneratedPDF]:
        """Scan output folder for PDFs."""
        records = []
        for pdf_path in self.output_dir.glob("*.pdf"):
            # Parse filename: first_last_company_role.pdf or company_role.pdf
            stem = pdf_path.stem
            run_id = None

            # Strip leading run_id prefix (MMDD_HHMM_)
            m = RUN_ID_PREFIX_RE.match(stem)
            if m:
                run_id = stem[: m.end() - 1]  # without trailing _
                stem = stem[m.end():]

            parts = stem.split("_")

            # Strip known language suffix (2-letter code at end)
            if len(parts) >= 2 and len(parts[-1]) == 2 and parts[-1].isalpha():
                parts = parts[:-1]

            # Heuristic: if 4+ parts, assume first_last_company_role
            if len(parts) >= 4:
                first_name = parts[0].title()
                last_name = parts[1].title()
                company = " ".join(parts[2:-1]).title()
                job_title = parts[-1].title()
            elif len(parts) >= 2:
                first_name, last_name = None, None
                company = " ".join(parts[:-1]).title()
                job_title = parts[-1].title()
            else:
                first_name, last_name = None, None
                company = parts[0].title() if parts else "Unknown"
                job_title = "Unknown"

            records.append(GeneratedPDF(
                path=pdf_path,
                source_checksum="",
                company=company,
                job_title=job_title,
                timestamp=datetime.fromtimestamp(pdf_path.stat().st_mtime),
                first_name=first_name,
                last_name=last_name,
                run_id=run_id,
            ))

        # Sort by timestamp (newest first for display)
        records.sort(key=lambda r: r.timestamp, reverse=True)
        return records

    def save_record(self, pdf: GeneratedPDF) -> None:
        """No-op - metadata derived from filename/mtime."""
        pass
