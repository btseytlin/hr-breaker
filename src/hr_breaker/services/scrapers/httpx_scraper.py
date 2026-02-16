import random
import time

import httpx

from hr_breaker.config import get_settings

from .base import BaseScraper, CloudflareBlockedError, ScrapingError

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


class HttpxScraper(BaseScraper):
    """Direct HTTP scraper using httpx."""

    name = "httpx"

    def __init__(self, max_retries: int | None = None, timeout: float | None = None):
        settings = get_settings()
        self.max_retries = max_retries if max_retries is not None else settings.scraper_httpx_max_retries
        self.timeout = timeout if timeout is not None else settings.scraper_httpx_timeout

    def scrape(self, url: str) -> str:
        """Scrape job posting with retry and backoff."""
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                return self._fetch_and_parse(url)
            except CloudflareBlockedError:
                raise
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code == 403:
                    self._backoff(attempt)
                    continue
                raise ScrapingError(f"HTTP {e.response.status_code}: {e}")
            except httpx.RequestError as e:
                last_error = e
                self._backoff(attempt)
                continue

        raise ScrapingError(
            f"Failed to scrape {url} after {self.max_retries} attempts: {last_error}"
        )

    def _fetch_and_parse(self, url: str) -> str:
        """Fetch URL and extract job posting text."""
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

        with httpx.Client(
            follow_redirects=True, timeout=self.timeout, trust_env=True
        ) as client:
            response = client.get(url, headers=headers)
            html = response.text

        if self.is_cloudflare_blocked(html):
            raise CloudflareBlockedError(f"Site {url} is protected by Cloudflare")

        response.raise_for_status()

        return self.extract_job_text(html)

    def _backoff(self, attempt: int):
        """Exponential backoff between retries."""
        delay = (2**attempt) + random.uniform(0, 1)
        time.sleep(delay)
