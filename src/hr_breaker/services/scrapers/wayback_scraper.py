import logging
from datetime import datetime, timedelta, timezone

import httpx

from hr_breaker.config import get_settings

from .base import BaseScraper, ScrapingError

logger = logging.getLogger(__name__)

WAYBACK_CDX_API = "http://web.archive.org/cdx/search/cdx"


class WaybackScraper(BaseScraper):
    """Scraper using Wayback Machine archived snapshots."""

    name = "wayback"

    def __init__(self, max_age_days: int | None = None, timeout: float | None = None):
        settings = get_settings()
        self.max_age_days = max_age_days if max_age_days is not None else settings.scraper_wayback_max_age_days
        self.timeout = timeout if timeout is not None else settings.scraper_wayback_timeout

    def scrape(self, url: str) -> str:
        """Fetch job posting from Wayback Machine."""
        snapshot_url = self._get_latest_snapshot(url)
        if not snapshot_url:
            raise ScrapingError(f"No recent Wayback snapshot for {url}")

        logger.info(f"Using Wayback snapshot: {snapshot_url}")

        with httpx.Client(
            follow_redirects=True, timeout=self.timeout, trust_env=True
        ) as client:
            response = client.get(snapshot_url)
            response.raise_for_status()
            html = response.text

        return self.extract_job_text(html)

    def _get_latest_snapshot(self, url: str) -> str | None:
        """Query CDX API for most recent snapshot."""
        params = {
            "url": url,
            "output": "json",
            "limit": 1,
            "sort": "reverse",
            "filter": "statuscode:200",
        }

        try:
            with httpx.Client(timeout=self.timeout, trust_env=True) as client:
                response = client.get(WAYBACK_CDX_API, params=params)
                response.raise_for_status()
                data = response.json()
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.warning(f"Wayback CDX API error: {e}")
            return None

        # Response: [["urlkey","timestamp","original",...], [...actual data...]]
        if len(data) < 2:
            return None

        # CDX columns: urlkey, timestamp, original, mimetype, statuscode, digest, length
        row = data[1]
        timestamp = row[1]  # Format: YYYYMMDDhhmmss

        # Check freshness
        try:
            snapshot_date = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(
                tzinfo=timezone.utc
            )
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.max_age_days)
            if snapshot_date < cutoff:
                logger.info(
                    f"Wayback snapshot too old: {snapshot_date.date()} "
                    f"(cutoff: {cutoff.date()})"
                )
                return None
        except ValueError:
            pass  # If timestamp parsing fails, proceed anyway

        original_url = row[2]
        return f"http://web.archive.org/web/{timestamp}/{original_url}"
