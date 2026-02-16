import logging

from hr_breaker.config import get_settings

from .base import BaseScraper, CloudflareBlockedError, ScrapingError

logger = logging.getLogger(__name__)

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    sync_playwright = None
    PlaywrightTimeout = None


class PlaywrightScraper(BaseScraper):
    """Browser-based scraper using Playwright."""

    name = "playwright"

    def __init__(self, timeout: float = 60000):  # ms for playwright
        self.timeout = timeout
        settings = get_settings()
        self.proxy_url = settings.proxy_url
        self.proxy_bypass = settings.no_proxy

    def scrape(self, url: str) -> str:
        """Scrape job posting using headless browser."""
        if not PLAYWRIGHT_AVAILABLE:
            raise ScrapingError(
                "Playwright not installed. Install with: "
                "uv pip install 'hr-breaker[browser]' && playwright install chromium"
            )

        try:
            with sync_playwright() as p:
                proxy_config = None
                if self.proxy_url:
                    proxy_config = {"server": self.proxy_url}
                    if self.proxy_bypass:
                        proxy_config["bypass"] = self.proxy_bypass

                browser = p.chromium.launch(headless=True, proxy=proxy_config)
                try:
                    context = browser.new_context(
                        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                    page = context.new_page()

                    page.goto(url, wait_until="networkidle", timeout=self.timeout)
                    html = page.content()

                    if self.is_cloudflare_blocked(html):
                        raise CloudflareBlockedError(
                            f"Cloudflare blocked even with browser: {url}"
                        )

                    return self.extract_job_text(html)
                finally:
                    browser.close()
        except PlaywrightTimeout:
            raise ScrapingError(f"Playwright timeout loading {url}")
        except Exception as e:
            if isinstance(e, (ScrapingError, CloudflareBlockedError)):
                raise
            raise ScrapingError(f"Playwright error: {e}")
