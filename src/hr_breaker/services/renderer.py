"""Abstract renderer interface and implementations."""

import os
import sys
from abc import ABC, abstractmethod
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from hr_breaker.models.resume_data import ResumeData, RenderResult

# Template directory
TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates"


def _setup_macos_library_path():
    """Set up library path for WeasyPrint on macOS with Homebrew."""
    if sys.platform != "darwin":
        return

    # Check if DYLD_FALLBACK_LIBRARY_PATH is already set
    if os.environ.get("DYLD_FALLBACK_LIBRARY_PATH"):
        return

    # Try common Homebrew paths
    homebrew_paths = [
        "/opt/homebrew/lib",  # Apple Silicon
        "/usr/local/lib",  # Intel
    ]

    for path in homebrew_paths:
        gobject_lib = Path(path) / "libgobject-2.0.dylib"
        if gobject_lib.exists():
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = path
            return


class RenderError(Exception):
    """Raised when rendering fails."""

    pass


class BaseRenderer(ABC):
    """Abstract base class for resume renderers."""

    @abstractmethod
    def render(self, data: ResumeData) -> RenderResult:
        """Render resume data to PDF."""
        pass


class HTMLRenderer(BaseRenderer):
    """Render resume using HTML + WeasyPrint."""

    _weasyprint_imported = False

    def __init__(self):
        self._ensure_weasyprint()
        self.env = Environment(
            loader=FileSystemLoader(TEMPLATE_DIR),
            autoescape=True,
        )
        from weasyprint.text.fonts import FontConfiguration
        self.font_config = FontConfiguration()
        self._wrapper_html = (TEMPLATE_DIR / "resume_wrapper.html").read_text(encoding="utf-8")

    @classmethod
    def _ensure_weasyprint(cls):
        """Lazily import WeasyPrint with proper library path setup."""
        if cls._weasyprint_imported:
            return

        # Set up library path before importing (mainly for macOS)
        _setup_macos_library_path()

        try:
            # Import WeasyPrint - this will fail if libs not found
            import weasyprint  # noqa: F401
            cls._weasyprint_imported = True
        except OSError as e:
            if any(libs in str(e) for libs in ["libgobject", "libpango", "libcairo", "libgdk_pixbuf"]):
                if sys.platform == "darwin":
                    msg = (
                        "WeasyPrint libraries not found. On macOS, run:\n"
                        "  brew install pango gdk-pixbuf libffi\n"
                        "Then either:\n"
                        "  1. Add to ~/.zshrc: export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib\n"
                        "  2. Or run with: DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run hr-breaker ..."
                    )
                elif sys.platform == "win32":
                    msg = (
                        "WeasyPrint libraries (GTK3) not found on Windows.\n"
                        "Please download and install the GTK3 runtime from:\n"
                        "  https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases\n"
                        "After installation, ensure the GTK3 bin folder is in your PATH and RESTART your terminal."
                    )
                else:
                    msg = (
                        "WeasyPrint libraries (Pango, Cairo, GdkPixbuf) not found.\n"
                        "Please install them using your system package manager:\n"
                        "  Ubuntu/Debian: sudo apt-get install libpango-1.0-0 libharfbuzz0b libpangoft2-1.0-0\n"
                        "  Fedora: sudo dnf install pango cairo gdk-pixbuf2"
                    )
                raise RenderError(msg) from e
            raise

    def render(self, html_body: str) -> RenderResult:
        """Render LLM-generated HTML body to PDF.

        Args:
            html_body: HTML content for the <body> (no wrapper needed)
        """
        from weasyprint import HTML

        # Wrap LLM's body content with our template
        html_content = self._wrapper_html.replace("{{BODY}}", html_body)

        # Render with WeasyPrint
        html = HTML(string=html_content, base_url=str(TEMPLATE_DIR))
        doc = html.render(font_config=self.font_config)
        pdf_bytes = doc.write_pdf()
        page_count = len(doc.pages)

        warnings = []
        if page_count > 1:
            warnings.append(f"Resume is {page_count} pages, should be 1 page")

        return RenderResult(
            pdf_bytes=pdf_bytes,
            page_count=page_count,
            warnings=warnings,
        )

    def render_data(self, data: ResumeData) -> RenderResult:
        """Legacy: Render ResumeData to PDF via Jinja template."""
        from weasyprint import HTML, CSS

        template = self.env.get_template("resume.html")
        html_content = template.render(resume=data)

        html = HTML(string=html_content, base_url=str(TEMPLATE_DIR))
        css_path = TEMPLATE_DIR / "resume.css"
        stylesheets = []
        if css_path.exists():
            stylesheets.append(CSS(filename=str(css_path), font_config=self.font_config))

        doc = html.render(stylesheets=stylesheets, font_config=self.font_config)
        pdf_bytes = doc.write_pdf()
        page_count = len(doc.pages)

        warnings = []
        if page_count > 1:
            warnings.append(f"Resume is {page_count} pages, should be 1 page")

        return RenderResult(
            pdf_bytes=pdf_bytes,
            page_count=page_count,
            warnings=warnings,
        )


def get_renderer() -> HTMLRenderer:
    """Get the HTML renderer."""
    return HTMLRenderer()
