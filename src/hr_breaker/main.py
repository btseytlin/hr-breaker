import asyncio
import subprocess
import sys
from pathlib import Path

import nest_asyncio
import streamlit as st

nest_asyncio.apply()

# Event loop setup
if "event_loop" not in st.session_state:
    st.session_state.event_loop = asyncio.new_event_loop()
asyncio.set_event_loop(st.session_state.event_loop)

from hr_breaker.agents import extract_name, parse_job_posting
from hr_breaker.config import get_settings
from hr_breaker.models import GeneratedPDF, ResumeSource, ValidationResult, SUPPORTED_LANGUAGES, get_language
from hr_breaker.orchestration import optimize_for_job, translate_and_rerender
from hr_breaker.services import PDFStorage, ResumeCache, scrape_job_posting, CloudflareBlockedError
from hr_breaker.services.pdf_parser import extract_text_from_pdf

# Initialize services
cache = ResumeCache()
pdf_storage = PDFStorage()
settings = get_settings()

st.set_page_config(page_title="HR-Breaker", page_icon="*", layout="wide")


def run_async(coro):
    """Run async coroutine in sync context."""
    loop = st.session_state.event_loop
    return loop.run_until_complete(coro)


@st.cache_data(show_spinner=False)
def cached_scrape_job(url: str) -> str:
    """Cached job scraping by URL."""
    return scrape_job_posting(url)


@st.cache_data(show_spinner=False)
def cached_extract_name(content: str) -> tuple[str | None, str | None]:
    """Cached name extraction by resume content hash."""
    return run_async(extract_name(content))


@st.cache_resource(show_spinner=False)
def cached_parse_job(text: str):
    """Cached job parsing by job text hash."""
    return run_async(parse_job_posting(text))


def display_filter_results(validation: ValidationResult):
    """Display filter results in UI."""
    for result in validation.results:
        icon = "[OK]" if result.passed else "[X]"
        with st.expander(f"{icon} {result.filter_name} - Score: {result.score:.2f}/{result.threshold:.2f}"):
            if result.issues:
                st.write("**Issues:**")
                for issue in result.issues:
                    st.write(f"- {issue}")
            if result.suggestions:
                st.write("**Suggestions:**")
                for suggestion in result.suggestions:
                    st.write(f"- {suggestion}")


# Sidebar - Options & History
with st.sidebar:
    # Options section
    st.markdown("**Options**")
    sequential_mode = st.checkbox("Sequential", value=False, help="Run filters sequentially with early exit")
    debug_mode = st.checkbox("Debug", value=False, help="Save each iteration PDF")
    no_shame_mode = st.checkbox("No Shame", value=False, help="Lenient mode: allow aggressive content stretching")

    # Language selector
    _lang_options = [lang.code for lang in SUPPORTED_LANGUAGES]
    _lang_labels = {lang.code: lang.native_name for lang in SUPPORTED_LANGUAGES}
    _default_lang_idx = _lang_options.index(settings.default_language) if settings.default_language in _lang_options else 0
    selected_lang_code = st.selectbox(
        "Resume language",
        options=_lang_options,
        index=_default_lang_idx,
        format_func=lambda code: _lang_labels[code],
        help="Output language for the final resume. Optimization runs in English, then translates.",
    )
    selected_language = get_language(selected_lang_code)

    max_iterations = st.number_input("Max iterations", min_value=1, max_value=10, value=settings.max_iterations)

    st.divider()

    # History section
    existing_pdfs = pdf_storage.list_all()  # Always scans folder
    st.markdown(f"**History ({len(existing_pdfs)})**")
    col_open, col_refresh = st.columns(2)
    with col_open:
        if st.button("📂 Open", use_container_width=True, help="Open output folder"):
            folder = str(settings.output_dir.resolve())
            if sys.platform == "darwin":
                subprocess.run(["open", folder])
            elif sys.platform == "win32":
                subprocess.run(["explorer", folder])
            else:
                subprocess.run(["xdg-open", folder])
    with col_refresh:
        if st.button("🔄 Refresh", use_container_width=True, help="Rescan folder"):
            st.rerun()

    if existing_pdfs:
        for pdf in existing_pdfs[:10]:  # Already sorted newest-first
            label = f"{pdf.company} • {pdf.job_title}"
            if len(label) > 30:
                label = label[:27] + "..."
            with open(pdf.path, "rb") as f:
                st.download_button(
                    label,
                    f.read(),
                    file_name=pdf.path.name,
                    mime="application/pdf",
                    key=str(pdf.timestamp),
                    help=f"{pdf.company} • {pdf.job_title}\n{pdf.timestamp.strftime('%m/%d %H:%M')}",
                    use_container_width=True,
                )
    else:
        st.caption("No PDFs yet")

    st.divider()
    if not settings.google_api_key:
        st.warning("Set GOOGLE_API_KEY in .env")

# Main content
st.markdown("### HR-Breaker")

# Use cached resume if available (but not if user explicitly cleared it)
if "source_resume" not in st.session_state and not st.session_state.get("resume_cleared") and cache.list_all():
    cached_resumes = cache.list_all()
    if cached_resumes:
        st.session_state["source_resume"] = cached_resumes[-1]

# Two main columns: Resume | Job
col_resume, col_job = st.columns(2)

has_resume = "source_resume" in st.session_state

with col_resume:
    resume_header = "**Resume ✓**" if has_resume else "**Resume**"
    st.markdown(resume_header)

    # If resume loaded, show compact info; else show input
    if has_resume:
        src = st.session_state["source_resume"]
        name = f"{src.first_name or ''} {src.last_name or ''}".strip() or "Unknown"
        c1, c2 = st.columns([4, 1])
        with c1:
            st.success(f"✓ {name}")
        with c2:
            if st.button("Change", key="clear_resume"):
                st.session_state.pop("source_resume", None)
                st.session_state.pop("last_result", None)
                st.session_state["resume_uploader_key"] = st.session_state.get("resume_uploader_key", 0) + 1
                st.session_state["resume_cleared"] = True
                st.rerun()
        with st.expander("Preview", expanded=False):
            st.text(src.content)
    else:
        resume_method = st.radio("Resume input method", ["Upload", "Paste"], horizontal=True, key="resume_method", label_visibility="collapsed")

        resume_content = None
        if resume_method == "Upload":
            uploader_key = f"resume_uploader_{st.session_state.get('resume_uploader_key', 0)}"
            uploaded_file = st.file_uploader(
                "Upload (.tex, .md, .txt, .pdf)",
                type=["tex", "md", "txt", "pdf"],
                label_visibility="collapsed",
                key=uploader_key,
            )
            if uploaded_file:
                if uploaded_file.name.lower().endswith(".pdf"):
                    temp_path = Path(f"/tmp/{uploaded_file.name}")
                    temp_path.write_bytes(uploaded_file.read())
                    resume_content = extract_text_from_pdf(temp_path)
                    temp_path.unlink()
                else:
                    resume_content = uploaded_file.read().decode("utf-8")
        else:
            pasted_resume = st.text_area("Paste resume", height=100, label_visibility="collapsed", placeholder="Paste resume text...")
            if pasted_resume:
                resume_content = pasted_resume

        if resume_content:
            with st.spinner("Extracting name..."):
                first_name, last_name = cached_extract_name(resume_content)
            source = ResumeSource(content=resume_content, first_name=first_name, last_name=last_name)
            cache.put(source)
            st.session_state["source_resume"] = source
            st.session_state.pop("resume_cleared", None)
            st.rerun()

with col_job:
    job_text = st.session_state.get("job_text", "")
    has_job = bool(job_text)
    job_header = "**Job Posting ✓**" if has_job else "**Job Posting**"
    st.markdown(job_header)

    # If job loaded, show compact info; else show input
    if has_job:
        preview = job_text[:80].replace('\n', ' ') + "..." if len(job_text) > 80 else job_text.replace('\n', ' ')
        c1, c2 = st.columns([4, 1])
        with c1:
            st.success(f"✓ {preview}")
        with c2:
            if st.button("Change", key="clear_job"):
                st.session_state.pop("job_text", None)
                st.session_state.pop("last_job_url", None)
                st.session_state.pop("last_result", None)
                st.rerun()
        with st.expander("Preview", expanded=False):
            st.text(job_text)
    else:
        job_input_method = st.radio("Job input method", ["URL", "Paste"], horizontal=True, key="job_method", label_visibility="collapsed")

        if job_input_method == "URL":
            job_url = st.text_input("Job URL", label_visibility="collapsed", placeholder="https://...")

            # Auto-fetch when URL changes
            if job_url and job_url != st.session_state.get("last_job_url"):
                st.session_state["last_job_url"] = job_url
                with st.spinner("Fetching..."):
                    try:
                        job_text = cached_scrape_job(job_url)
                        st.session_state["job_text"] = job_text
                        st.session_state.pop("scrape_failed_url", None)
                        st.rerun()
                    except CloudflareBlockedError:
                        st.session_state["scrape_failed_url"] = job_url
                        st.warning("Bot protection. Copy & paste instead.")
                    except Exception as e:
                        st.error(f"Failed: {e}")

            if st.session_state.get("scrape_failed_url"):
                st.markdown(f"[Open in browser]({st.session_state['scrape_failed_url']})")
        else:
            pasted_job = st.text_area("Paste job", height=100, label_visibility="collapsed", placeholder="Paste job posting...")
            if pasted_job:
                st.session_state["job_text"] = pasted_job
                st.session_state.pop("scrape_failed_url", None)
                st.rerun()

# Optimize button
is_running = st.session_state.get("optimization_running", False)
can_optimize = has_resume and has_job and not is_running
btn_help = None
if not has_resume:
    btn_help = "Need resume"
elif not has_job:
    btn_help = "Need job posting"
elif is_running:
    btn_help = "Optimization in progress"
clicked = st.button("🚀 Optimize", disabled=not can_optimize, use_container_width=True, help=btn_help)

if clicked:
    source = st.session_state["source_resume"]
    st.session_state["optimization_running"] = True
    error_occurred = None

    try:
        with st.spinner("Parsing job posting..."):
            job = cached_parse_job(job_text)

        # Setup debug dir if enabled
        debug_dir = None
        if debug_mode:
            debug_dir = pdf_storage.generate_debug_dir(job.company, job.title)

        # Store iteration results for session state
        iteration_results = []

        with st.status("Optimizing resume...", expanded=True) as status_container:
            def on_iteration(i, opt, val):
                iteration_results.append((i, opt, val))
                status_container.update(label=f"Iteration {i + 1}/{max_iterations}")
                status_container.write(f"Iteration {i + 1} complete")

                # Save debug files if enabled
                if debug_mode and debug_dir:
                    if opt.html:
                        (debug_dir / f"iteration_{i + 1}.html").write_text(opt.html)
                    if opt.pdf_bytes:
                        (debug_dir / f"iteration_{i + 1}.pdf").write_bytes(opt.pdf_bytes)

            def on_translation_status(msg):
                status_container.update(label=msg)
                status_container.write(msg)

            # Only pass language if not English (no translation needed)
            target_lang = selected_language if selected_language.code != "en" else None

            optimized, validation, job = run_async(
                optimize_for_job(
                    source,
                    job_text,
                    max_iterations=max_iterations,
                    on_iteration=on_iteration,
                    job=job,
                    parallel=not sequential_mode,
                    no_shame=no_shame_mode,
                    language=target_lang,
                    on_translation_status=on_translation_status,
                )
            )
            status_container.update(label="Optimization complete", state="complete")

        # Save PDF and store results in session state
        pdf_path = None
        if optimized and optimized.pdf_bytes:
            pdf_path = pdf_storage.generate_path(
                source.first_name, source.last_name, job.company, job.title,
                lang_code=selected_lang_code,
            )
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(optimized.pdf_bytes)

            pdf_record = GeneratedPDF(
                path=pdf_path,
                source_checksum=source.checksum,
                company=job.company,
                job_title=job.title,
                first_name=source.first_name,
                last_name=source.last_name,
            )
            pdf_storage.save_record(pdf_record)

        # Store in session state for persistent display
        st.session_state["last_result"] = {
            "optimized": optimized,
            "validation": validation,
            "job": job,
            "iterations": iteration_results,
            "pdf_path": pdf_path,
            "debug_dir": debug_dir,
        }
    except Exception as e:
        error_occurred = e
    finally:
        st.session_state["optimization_running"] = False

    if error_occurred:
        st.error(f"Optimization failed: {error_occurred}")
    else:
        st.rerun()  # Rerun to show results and update history

# Display last result if exists
if "last_result" in st.session_state:
    result = st.session_state["last_result"]
    optimized = result["optimized"]
    validation = result["validation"]
    job = result["job"]
    iterations = result["iterations"]
    pdf_path = result["pdf_path"]
    debug_dir = result["debug_dir"]

    # Result section
    st.markdown("---")
    st.markdown(f"### Result: {job.title} at {job.company}")

    # Status message
    if validation.passed:
        st.success("All filters passed!")
    else:
        passed = [r.filter_name for r in validation.results if r.passed]
        failed = [r.filter_name for r in validation.results if not r.passed]
        st.warning(f"Max iterations ({len(passed)}/{len(validation.results)} passed). Failed: {', '.join(failed)}")

    if debug_dir:
        st.info(f"Debug output: {debug_dir}")

    # PDF actions
    if pdf_path:
        st.success(f"PDF saved: {pdf_path}")
        if st.button("📂 Open Output Folder", use_container_width=True):
            folder = str(pdf_path.parent.resolve())
            if sys.platform == "darwin":
                subprocess.run(["open", folder])
            elif sys.platform == "win32":
                subprocess.run(["explorer", folder])
            else:
                subprocess.run(["xdg-open", folder])
    elif optimized:
        st.error("Failed to render PDF")

    # Translate existing result to another language (independent of "Resume language" option)
    if optimized and optimized.html:
        translate_targets = [lang for lang in SUPPORTED_LANGUAGES if lang.code != "en"]
        if translate_targets:
            tr_col1, tr_col2 = st.columns([2, 1])
            with tr_col1:
                translate_lang_code = st.selectbox(
                    "Translate to…",
                    options=[lang.code for lang in translate_targets],
                    format_func=lambda c: next(lg.native_name for lg in translate_targets if lg.code == c),
                    key="translate_target_lang",
                    help="Translate this result without re-running optimization",
                )
            with tr_col2:
                translate_clicked = st.button("🌐 Translate", use_container_width=True, key="translate_btn")
            if translate_clicked and translate_lang_code:
                translate_language = get_language(translate_lang_code)
                try:
                    with st.status(f"Translating to {translate_language.native_name}...", expanded=True) as tr_status:
                        def on_tr_status(msg):
                            tr_status.update(label=msg)
                            tr_status.write(msg)

                        translated = run_async(
                            translate_and_rerender(optimized, translate_language, job, on_status=on_tr_status)
                        )
                        tr_status.update(label="Translation complete", state="complete")

                    # Save translated PDF
                    if translated.pdf_bytes:
                        source = st.session_state["source_resume"]
                        tr_pdf_path = pdf_storage.generate_path(
                            source.first_name, source.last_name, job.company, job.title,
                            lang_code=translate_language.code,
                        )
                        tr_pdf_path.parent.mkdir(parents=True, exist_ok=True)
                        tr_pdf_path.write_bytes(translated.pdf_bytes)

                        pdf_record = GeneratedPDF(
                            path=tr_pdf_path,
                            source_checksum=source.checksum,
                            company=job.company,
                            job_title=job.title,
                            first_name=source.first_name,
                            last_name=source.last_name,
                        )
                        pdf_storage.save_record(pdf_record)

                        # Update session state with translated result
                        st.session_state["last_result"] = {
                            **st.session_state["last_result"],
                            "optimized": translated,
                            "pdf_path": tr_pdf_path,
                        }
                        st.rerun()
                except Exception as e:
                    st.error(f"Translation failed: {e}")

    # Resume content preview
    if optimized:
        with st.expander("Resume Content", expanded=False):
            if optimized.html:
                st.code(optimized.html, language="html")
            elif optimized.data:
                st.json(optimized.data.model_dump())

    # Iteration details
    for i, opt, val in iterations:
        with st.expander(f"Iteration {i + 1}", expanded=False):
            if opt.changes:
                st.write("**Changes:**")
                for change in opt.changes:
                    st.write(f"- {change}")
            display_filter_results(val)

    # Clear button
    if st.button("Clear Result", use_container_width=True):
        st.session_state.pop("last_result", None)
        st.rerun()
