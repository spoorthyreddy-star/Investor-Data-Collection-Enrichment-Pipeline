"""
src/utils/helpers.py
────────────────────
Shared utilities used across the pipeline:
  - structured logging setup
  - HTTP session factory (sync + async)
  - polite delay helper
  - URL normalisation
  - text cleaning helpers
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import (
    LOGS_DIR, REQUEST_TIMEOUT, REQUEST_DELAY_MIN, REQUEST_DELAY_MAX,
    MAX_RETRIES, BACKOFF_FACTOR, USER_AGENTS,
)

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Return a named logger that writes to both console and a rotating log file.
    Call once per module: logger = get_logger(__name__)
    """
    logger = logging.getLogger(name)
    if logger.handlers:          # avoid duplicate handlers on re-import
        return logger

    logger.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File handler
    log_file = LOGS_DIR / f"{name.split('.')[-1]}.log"
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ─────────────────────────────────────────────────────────────────────────────
# HTTP Session (sync)
# ─────────────────────────────────────────────────────────────────────────────

def build_session(extra_headers: Optional[dict] = None) -> requests.Session:
    """
    Return a requests.Session pre-configured with:
      - automatic retries (with exponential back-off) on 5xx / connection errors
      - a random User-Agent from the rotation list
      - sensible timeouts
    """
    session = requests.Session()

    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=BACKOFF_FACTOR,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
    })

    if extra_headers:
        session.headers.update(extra_headers)

    return session


# ─────────────────────────────────────────────────────────────────────────────
# Polite delay
# ─────────────────────────────────────────────────────────────────────────────

def polite_delay(min_s: float = REQUEST_DELAY_MIN, max_s: float = REQUEST_DELAY_MAX) -> None:
    """Sleep for a random duration to be a polite scraper."""
    time.sleep(random.uniform(min_s, max_s))


async def async_polite_delay(min_s: float = REQUEST_DELAY_MIN,
                              max_s: float = REQUEST_DELAY_MAX) -> None:
    """Async version of polite_delay."""
    await asyncio.sleep(random.uniform(min_s, max_s))


# ─────────────────────────────────────────────────────────────────────────────
# URL utilities
# ─────────────────────────────────────────────────────────────────────────────

_TRAILING_SLASH_RE = re.compile(r"/+$")

def normalise_url(url: str) -> str:
    """
    Normalise a URL:
      - lowercase scheme + host
      - strip trailing slashes
      - remove default ports
      - strip utm_* / tracking query params
    Returns empty string on unparseable input.
    """
    if not url or not isinstance(url, str):
        return ""
    url = url.strip()
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        scheme = parsed.scheme.lower() or "https"
        netloc = parsed.netloc.lower()
        path   = _TRAILING_SLASH_RE.sub("", parsed.path)
        # Strip common tracking params
        query_parts = [
            p for p in (parsed.query or "").split("&")
            if p and not p.lower().startswith(("utm_", "fbclid", "gclid", "ref="))
        ]
        query = "&".join(query_parts)
        return urlunparse((scheme, netloc, path, "", query, ""))
    except Exception:
        return ""


def extract_domain(url: str) -> str:
    """Return just the domain (e.g. 'sequoiacap.com') from a URL."""
    try:
        return urlparse(normalise_url(url)).netloc.lstrip("www.")
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Text cleaning
# ─────────────────────────────────────────────────────────────────────────────

_WHITESPACE_RE = re.compile(r"\s+")
_EMAIL_RE      = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_RE      = re.compile(
    r"(\+?\d[\d\s\-().]{7,}\d)"
)

def clean_text(text: Optional[str]) -> str:
    """Strip, collapse whitespace, remove zero-width chars."""
    if not text:
        return ""
    text = str(text)
    text = text.replace("\u200b", "").replace("\xa0", " ")
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def extract_emails(text: str) -> list[str]:
    """Extract all email addresses found in a block of text."""
    return list(set(_EMAIL_RE.findall(text or "")))


def extract_phones(text: str) -> list[str]:
    """Extract all phone-like patterns found in a block of text."""
    return list(set(_PHONE_RE.findall(text or "")))


def title_case_name(name: str) -> str:
    """
    Apply title-casing while preserving known acronyms
    (e.g. 'VC', 'AI', 'LP', 'GP').
    """
    ACRONYMS = {"VC", "AI", "LP", "GP", "IPO", "M&A", "PE", "HNI", "SWF", "CVC"}
    return " ".join(
        w.upper() if w.upper() in ACRONYMS else w.capitalize()
        for w in clean_text(name).split()
    )
