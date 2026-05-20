"""
src/scrapers/base_scraper.py
────────────────────────────
Abstract base class for every scraper in the pipeline.

All concrete scrapers must implement:
    scrape() -> list[InvestorRecord]

The base class provides:
    - logger
    - session (polite HTTP)
    - safe_get() with error handling
    - BeautifulSoup parsing shortcut
    - saving raw HTML to disk for audit/replay
"""

from __future__ import annotations

import abc
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import RAW_DIR, REQUEST_TIMEOUT
from src.utils.helpers import build_session, get_logger, polite_delay
from src.utils.schema import InvestorRecord


class BaseScraper(abc.ABC):
    """
    Abstract base scraper.

    Usage:
        class MyScraper(BaseScraper):
            SOURCE_NAME = "MySource"
            def scrape(self) -> list[InvestorRecord]:
                ...

    The `scrape()` method should return a list of InvestorRecord objects
    with at minimum the mandatory fields populated.
    """

    SOURCE_NAME: str = "unknown"      # override in subclass
    BASE_URL:    str = ""             # override in subclass

    def __init__(self, save_raw: bool = True):
        self.logger     = get_logger(f"scraper.{self.SOURCE_NAME.lower().replace(' ', '_')}")
        self.session    = build_session()
        self.save_raw   = save_raw
        self._raw_dir   = RAW_DIR / self.SOURCE_NAME.lower().replace(" ", "_")
        self._raw_dir.mkdir(parents=True, exist_ok=True)

    # ── Abstract interface ────────────────────────────────────────────────────

    @abc.abstractmethod
    def scrape(self) -> list[InvestorRecord]:
        """
        Main entry point. Return a list of InvestorRecord objects.
        Subclasses should NOT write to disk themselves; the pipeline runner
        accumulates records and writes in bulk.
        """

    # ── Helpers ───────────────────────────────────────────────────────────────

    def safe_get(self, url: str, params: Optional[dict] = None,
                 delay: bool = True) -> Optional[str]:
        """
        Perform a GET request and return the response text.
        Returns None on any error so callers can skip gracefully.
        Respects polite delay and saves raw HTML when save_raw=True.
        """
        if delay:
            polite_delay()
        try:
            resp = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            html = resp.text
            if self.save_raw:
                self._save_html(url, html)
            return html
        except Exception as exc:
            self.logger.warning(f"GET failed [{url}]: {exc}")
            return None

    def parse(self, html: str) -> BeautifulSoup:
        """Parse HTML string and return a BeautifulSoup object."""
        return BeautifulSoup(html, "html.parser")

    def make_record(self, **kwargs) -> InvestorRecord:
        """
        Convenience factory that pre-populates source metadata
        so individual scrapers don't repeat boilerplate.
        """
        kwargs.setdefault("data_source", self.SOURCE_NAME)
        kwargs.setdefault("date_collected", date.today().isoformat())
        return InvestorRecord.from_dict(kwargs)

    # ── Private ───────────────────────────────────────────────────────────────

    def _save_html(self, url: str, html: str) -> None:
        """Save raw HTML to disk using a URL-hash filename (for audit/replay)."""
        try:
            slug = hashlib.md5(url.encode()).hexdigest()[:12]
            path = self._raw_dir / f"{slug}.html"
            if not path.exists():
                path.write_text(html, encoding="utf-8", errors="replace")
        except Exception:
            pass  # silently skip if disk write fails

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} source='{self.SOURCE_NAME}'>"
