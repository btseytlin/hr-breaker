"""Extraction merging and synthesis — merge multiple DocumentExtractions and format for optimizer."""

import re

from hr_breaker.models.profile import (
    DocumentExtraction,
    EducationEntry,
    ExperienceEntry,
    PersonalInfo,
    Profile,
    ProfileDocument,
    ProjectEntry,
    RankedProfileDocument,
    SkillsEntry,
    get_document_extraction,
    extraction_has_signal,
    document_needs_extraction,
    )
from hr_breaker.models.resume import ResumeSource

_MAX_MERGED_SUMMARIES = 2
_WHOLE_DOC_FALLBACK_OVERHEAD_CHARS = 50

# Kind priority for whole-doc fallback ordering (lower = higher priority).
# Keep this aligned with DocumentKind.
KIND_PRIORITY: dict[str, int] = {
    "resume": 0,
    "note": 1,
    "paper": 2,
    "pdf": 3,
    "other": 4,
}


def get_extraction(doc: ProfileDocument) -> DocumentExtraction | None:
    return get_document_extraction(doc)


def _exp_sort_key(e: ExperienceEntry) -> str:
    end = e.end.lower().strip()
    if any(w in end for w in ("present", "current", "now")):
        return "9999"
    years = re.findall(r"\b(20\d{2}|19\d{2})\b", end)
    return years[-1] if years else end


def _merge_bullets(existing: list[str], incoming: list[str]) -> list[str]:
    """Union of bullets, preserving order, deduplicating by normalized text."""
    seen = {" ".join(b.split()).lower() for b in existing}
    merged = list(existing)
    for b in incoming:
        norm = " ".join(b.split()).lower()
        if norm not in seen:
            seen.add(norm)
            merged.append(b)
    return merged


def _merge_exp(existing: ExperienceEntry, incoming: ExperienceEntry) -> ExperienceEntry:
    """Merge two entries for the same role: union bullets, prefer more specific end date."""
    merged_bullets = _merge_bullets(existing.bullets, incoming.bullets)
    # Prefer the end date that sorts later (more recent / more specific)
    better_end = existing.end if _exp_sort_key(existing) >= _exp_sort_key(incoming) else incoming.end
    return existing.model_copy(update={"bullets": merged_bullets, "end": better_end})


def _merge_edu(existing: EducationEntry, incoming: EducationEntry) -> EducationEntry:
    """Merge two entries for the same degree: union notes, fill in missing dates/field."""
    merged_notes = _merge_bullets(existing.notes, incoming.notes)
    field = existing.field or incoming.field
    start = existing.start or incoming.start
    end = existing.end or incoming.end
    return existing.model_copy(update={"notes": merged_notes, "field": field, "start": start, "end": end})


def _merge_proj(existing: ProjectEntry, incoming: ProjectEntry) -> ProjectEntry:
    """Merge two entries for the same project: union bullets, fill in missing URL."""
    merged_bullets = _merge_bullets(existing.bullets, incoming.bullets)
    url = existing.url or incoming.url
    # Prefer the longer description
    description = existing.description if len(existing.description) >= len(incoming.description) else incoming.description
    return existing.model_copy(update={"bullets": merged_bullets, "url": url, "description": description})


def merge_extractions(extractions: list[DocumentExtraction]) -> DocumentExtraction:
    """Merge multiple document extractions, deduplicating by natural key.

    Unlike a simple first-wins approach, overlapping entries (same role, same
    degree, same project) are merged field-by-field: bullets are unioned,
    missing dates/fields are filled from the richer source, and end dates are
    reconciled by preferring the more specific/recent value.
    """
    merged_pi = PersonalInfo()
    seen_other_links: set[str] = set()
    for ext in extractions:
        pi = ext.personal_info
        if not merged_pi.name and pi.name:
            merged_pi = merged_pi.model_copy(update={"name": pi.name})
        if not merged_pi.email and pi.email:
            merged_pi = merged_pi.model_copy(update={"email": pi.email})
        if not merged_pi.phone and pi.phone:
            merged_pi = merged_pi.model_copy(update={"phone": pi.phone})
        if not merged_pi.linkedin and pi.linkedin:
            merged_pi = merged_pi.model_copy(update={"linkedin": pi.linkedin})
        if not merged_pi.github and pi.github:
            merged_pi = merged_pi.model_copy(update={"github": pi.github})
        if not merged_pi.website and pi.website:
            merged_pi = merged_pi.model_copy(update={"website": pi.website})
        for link in pi.other_links:
            norm = link.strip()
            if norm and norm not in seen_other_links:
                seen_other_links.add(norm)
                merged_pi = merged_pi.model_copy(
                    update={"other_links": merged_pi.other_links + [norm]}
                )

    summaries: list[str] = []
    # Keyed dicts for content-aware merging
    experience_by_key: dict[tuple, ExperienceEntry] = {}
    education_by_key: dict[tuple, EducationEntry] = {}
    projects_by_key: dict[str, ProjectEntry] = {}
    publications: list[str] = []
    technical: set[str] = set()
    languages: set[str] = set()
    certifications: set[str] = set()
    awards: set[str] = set()

    seen_summaries: set[str] = set()
    seen_publications: set[str] = set()

    for ext in extractions:
        for s in ext.summary:
            key = " ".join(s.split())
            if key not in seen_summaries:
                seen_summaries.add(key)
                summaries.append(s)

        # TODO: deduplication uses exact-match normalized keys. "Apple Computer Inc." and
        # "Apple" or "BS" vs "B.S." will not merge, creating duplicate entries. A fuzzy
        # employer/institution name match (e.g. difflib.SequenceMatcher) would improve
        # cross-document dedup quality but is deferred to avoid introducing false merges.
        for exp in ext.experience:
            key = (exp.employer.lower().strip(), exp.title.lower().strip(), exp.start)
            if key not in experience_by_key:
                experience_by_key[key] = exp
            else:
                experience_by_key[key] = _merge_exp(experience_by_key[key], exp)

        for edu in ext.education:
            key = (edu.institution.lower().strip(), edu.degree.lower().strip())
            if key not in education_by_key:
                education_by_key[key] = edu
            else:
                education_by_key[key] = _merge_edu(education_by_key[key], edu)

        for proj in ext.projects:
            key = proj.name.lower().strip()
            if key not in projects_by_key:
                projects_by_key[key] = proj
            else:
                projects_by_key[key] = _merge_proj(projects_by_key[key], proj)

        for pub in ext.publications:
            key = " ".join(pub.split())
            if key not in seen_publications:
                seen_publications.add(key)
                publications.append(pub)

        technical.update(ext.skills.technical)
        languages.update(ext.skills.languages)
        certifications.update(ext.skills.certifications)
        awards.update(ext.skills.awards)

    experience = sorted(experience_by_key.values(), key=_exp_sort_key, reverse=True)

    return DocumentExtraction(
        personal_info=merged_pi,
        summary=summaries[:_MAX_MERGED_SUMMARIES],
        experience=experience,
        education=list(education_by_key.values()),
        skills=SkillsEntry(
            technical=sorted(technical),
            languages=sorted(languages),
            certifications=sorted(certifications),
            awards=sorted(awards),
        ),
        projects=list(projects_by_key.values()),
        publications=publications,
    )



def format_extraction(
    extraction: DocumentExtraction,
    ranked: list[RankedProfileDocument] | None = None,
) -> str:
    """Render merged extraction as structured plain text for the optimizer."""
    lines: list[str] = []

    pi = extraction.personal_info
    if pi.name:
        lines.append(f"Candidate name: {pi.name}")
        lines.append("")
    contact_parts = [p for p in [pi.email, pi.phone, pi.linkedin, pi.github, pi.website] if p]
    if contact_parts:
        lines.append("## Contact")
        lines.extend(contact_parts)
        lines.append("")
    if pi.other_links:
        lines.append("## Additional links (for use inline with projects/publications, not in header)")
        lines.extend(pi.other_links)
        lines.append("")

    if ranked:
        lines.append("## Source documents ranked by relevance to job (prioritize accordingly)")
        for i, r in enumerate(ranked[:6], 1):
            lines.append(f"{i}. {r.document.title} (score: {r.score:.2f})")
        lines.append("")

    if extraction.summary:
        lines.append("## Summary")
        for s in extraction.summary:
            lines.append(s.strip())
        lines.append("")

    if extraction.experience:
        lines.append("## Experience")
        for exp in extraction.experience:
            lines.append(f"{exp.employer} — {exp.title} | {exp.start} – {exp.end}")
            for b in exp.bullets:
                lines.append(f"  • {b}")
        lines.append("")

    if extraction.education:
        lines.append("## Education")
        for edu in extraction.education:
            parts = [edu.institution, edu.degree]
            if edu.field:
                parts.append(edu.field)
            dates = " – ".join(filter(None, [edu.start, edu.end]))
            if dates:
                parts.append(f"| {dates}")
            lines.append(" ".join(parts))
            for n in edu.notes:
                lines.append(f"  {n}")
        lines.append("")

    skills = extraction.skills
    if any([skills.technical, skills.languages, skills.certifications, skills.awards]):
        lines.append("## Skills")
        if skills.technical:
            lines.append(f"Technical: {', '.join(skills.technical)}")
        if skills.languages:
            lines.append(f"Languages: {', '.join(skills.languages)}")
        if skills.certifications:
            lines.append(f"Certifications: {', '.join(skills.certifications)}")
        if skills.awards:
            lines.append(f"Awards: {', '.join(skills.awards)}")
        lines.append("")

    if extraction.projects:
        lines.append("## Projects")
        for proj in extraction.projects:
            header = proj.name
            if proj.url:
                header += f" ({proj.url})"
            lines.append(f"{header} — {proj.description}")
            for b in proj.bullets:
                lines.append(f"  • {b}")
        lines.append("")

    if extraction.publications:
        lines.append("## Publications")
        for pub in extraction.publications:
            lines.append(f"• {pub}")
        lines.append("")

    return "\n".join(lines).strip()


def split_full_name(name: str | None) -> tuple[str | None, str | None]:
    if not name:
        return None, None
    parts = [part for part in name.strip().split() if part]
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def resolve_profile_name_parts(
    profile: Profile, *, extracted_name: str | None
 ) -> tuple[str | None, str | None, str | None]:
    extracted_first, extracted_last = split_full_name(extracted_name)
    display_first, display_last = split_full_name(profile.display_name)

    first_name = profile.first_name or extracted_first
    last_name = profile.last_name or extracted_last

    if not first_name and not last_name:
        first_name, last_name = display_first, display_last
    elif not first_name and display_last and display_last == last_name:
        first_name = display_first
    elif not last_name and display_first and display_first == first_name:
        last_name = display_last

    best_name = " ".join(part for part in [first_name, last_name] if part) or None
    return first_name, last_name, best_name


def synthesize_from_extractions(
    profile: Profile,
    selected: list[ProfileDocument],
    ranked: list[RankedProfileDocument],
    header: str,
    max_chars: int,
) -> ResumeSource | None:
    """Synthesize from extracted facts, appending raw text for unextracted docs.

    Returns None only if *no* selected document has extraction data.
    When some docs lack extraction, their raw text is appended after the
    structured section (within the char budget) so the optimizer sees all
    selected evidence.
    """
    pairs = [(doc, get_extraction(doc)) for doc in selected]
    extracted_pairs = [(doc, ext) for doc, ext in pairs if ext is not None]
    unextracted_docs = [doc for doc, ext in pairs if ext is None]

    if not extracted_pairs:
        return None

    extracted_exts = [ext for _, ext in extracted_pairs]
    merged = merge_extractions(extracted_exts)

    # Resolve name before formatting so format_extraction is only called once.
    extracted_name = merged.personal_info.name or None
    first_name, last_name, best_name = resolve_profile_name_parts(
        profile, extracted_name=extracted_name
    )
    if best_name and best_name != merged.personal_info.name:
        merged = merged.model_copy(
            update={"personal_info": merged.personal_info.model_copy(update={"name": best_name})}
        )

    body = format_extraction(merged, ranked=ranked or None)

    # Append raw text for unextracted docs within remaining budget
    if unextracted_docs:
        used = len(header) + len(body) + 4  # 4 for "\n\n" separators
        remaining = max_chars - used
        appended: list[str] = []
        skipped: list[str] = []
        score_by_id = {r.document.id: r.score for r in ranked}  # empty dict when ranked=[]
        # Sort unextracted by relevance score descending so the most relevant fit first
        unextracted_sorted = sorted(
            unextracted_docs,
            key=lambda d: -score_by_id.get(d.id, 0.0),
        )
        for doc in unextracted_sorted:
            section = f"\n\n## {doc.title} [{doc.kind}] (raw — extraction unavailable)\n{doc.content_text.strip()}"
            if len(section) <= remaining:
                appended.append(section)
                remaining -= len(section)
            else:
                skipped.append(doc.title)
        if appended:
            body += "".join(appended)
        if skipped:
            body += f"\n\nNote: extraction unavailable, omitted (over budget): {', '.join(skipped)}"

    return ResumeSource(
        content=f"{header}\n\n{body}".strip(),
        first_name=first_name,
        last_name=last_name,
        instructions=profile.instructions,
    )


def synthesize_from_whole_docs(
    profile: Profile,
    selected: list[ProfileDocument],
    ranked: list[RankedProfileDocument],
    header: str,
    max_chars: int,
) -> ResumeSource:
    """Fallback synthesis: load whole documents in priority order up to max_chars budget."""
    score_by_id = {r.document.id: r.score for r in ranked}
    ordered = sorted(
        selected,
        key=lambda d: (KIND_PRIORITY.get(d.kind, 7), -score_by_id.get(d.id, 0.0)),
    )
    budget = max_chars - len(header) - _WHOLE_DOC_FALLBACK_OVERHEAD_CHARS
    included: list[str] = []
    skipped: list[str] = []
    remaining = max(0, budget)
    for doc in ordered:
        section = f"\n\n## {doc.title} [{doc.kind}]\n{doc.content_text.strip()}"
        if len(section) <= remaining:
            included.append(section)
            remaining -= len(section)
        else:
            skipped.append(doc.title)

    body = "Profile documents:" + "".join(included)
    if skipped:
        body += f"\n\nNote: omitted (over budget): {', '.join(skipped)}"

    first_name, last_name, best_name = resolve_profile_name_parts(profile, extracted_name=None)
    return ResumeSource(
        content=f"{header}\n\n{body}".strip(),
        first_name=first_name,
        last_name=last_name,
        instructions=profile.instructions,
    )
