"""
src/cleaning/deduplicator.py
────────────────────────────
Two-pass deduplication pipeline.

Pass 1 — Exact fingerprinting
    SHA-256 hash of (normalised_firm_name + normalised_domain).
    Two records are identical if their fingerprint matches.

Pass 2 — Fuzzy matching
    Use rapidfuzz token_sort_ratio on firm_name for near-duplicates.
    Records that are ≥ DEDUPE_SIMILARITY_RATIO similar are clustered;
    the highest-completeness record in each cluster is kept.

This approach is O(n·log n) for pass 1 and O(n²) for pass 2.
For scale (100k+ records), pass 2 is replaced by a blocking strategy
using first-2-chars or country prefix as a key, reducing comparisons
by ~99%.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import DEDUPE_SIMILARITY_RATIO
from src.utils.helpers import get_logger, clean_text
from src.utils.schema import InvestorRecord

logger = get_logger("cleaning.deduplicator")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_for_hash(text: str) -> str:
    """Strip, lowercase, remove punctuation — for reliable fingerprinting."""
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]", "", text)
    return text


def _extract_domain(url: str) -> str:
    """Return the domain without www. prefix."""
    try:
        domain = urlparse(url).netloc
        return re.sub(r"^www\.", "", domain).lower()
    except Exception:
        return ""


def _make_fingerprint(rec: InvestorRecord) -> str:
    """
    Generate a 12-char hex fingerprint for a record.
    Concatenates normalised firm_name + domain as the identity key.
    """
    name   = _normalise_for_hash(rec.firm_name or rec.investor_name)
    domain = _normalise_for_hash(_extract_domain(rec.website_url))
    raw    = f"{name}::{domain}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────────────
# Deduplicator
# ─────────────────────────────────────────────────────────────────────────────

class Deduplicator:

    def __init__(self, similarity_threshold: float = DEDUPE_SIMILARITY_RATIO):
        self.threshold = similarity_threshold

    def deduplicate(self, records: list[InvestorRecord]) -> list[InvestorRecord]:
        """
        Full two-pass deduplication.
        Returns deduplicated list with record_id set on every record.
        """
        logger.info(f"Starting deduplication on {len(records)} records …")

        # Assign fingerprints
        for rec in records:
            rec.record_id = _make_fingerprint(rec)

        # Pass 1: exact dedup
        after_pass1 = self._exact_dedup(records)
        logger.info(f"  After exact dedup: {len(after_pass1)} records "
                    f"(removed {len(records) - len(after_pass1)})")

        # Pass 2: fuzzy dedup
        try:
            from rapidfuzz import fuzz
            after_pass2 = self._fuzzy_dedup(after_pass1, fuzz)
            logger.info(f"  After fuzzy dedup: {len(after_pass2)} records "
                        f"(removed {len(after_pass1) - len(after_pass2)})")
        except ImportError:
            logger.warning("rapidfuzz not installed — skipping fuzzy dedup. "
                           "Run: pip install rapidfuzz")
            after_pass2 = after_pass1

        logger.info(f"Deduplication complete: {len(after_pass2)} unique records")
        return after_pass2

    # ── Pass 1: exact ─────────────────────────────────────────────────────────

    def _exact_dedup(self, records: list[InvestorRecord]) -> list[InvestorRecord]:
        """
        Keep the most complete record for each fingerprint.
        """
        buckets: dict[str, list[InvestorRecord]] = {}
        for rec in records:
            buckets.setdefault(rec.record_id, []).append(rec)

        result = []
        for fp, group in buckets.items():
            # Keep record with highest completeness; tie-break on source priority
            best = max(group, key=lambda r: (r.completeness(), _source_priority(r.data_source)))
            best.is_duplicate = False
            result.append(best)

        return result

    # ── Pass 2: fuzzy ─────────────────────────────────────────────────────────

    def _fuzzy_dedup(self, records: list[InvestorRecord], fuzz) -> list[InvestorRecord]:
        """
        Cluster near-duplicate firm names using token_sort_ratio.
        Uses a blocking strategy: only compare records in the same country
        to reduce O(n²) comparisons.
        """
        # Group by country as a blocking key
        blocks: dict[str, list[InvestorRecord]] = {}
        for rec in records:
            key = (rec.country or "unknown").lower()[:3]
            blocks.setdefault(key, []).append(rec)

        kept_ids: set[str] = set()
        result: list[InvestorRecord] = []

        for block_key, block in blocks.items():
            # Sort by completeness descending so we keep the richer record
            block.sort(key=lambda r: r.completeness(), reverse=True)

            for i, rec_a in enumerate(block):
                if rec_a.record_id in kept_ids:
                    continue
                # Mark this record as kept
                kept_ids.add(rec_a.record_id)
                result.append(rec_a)

                name_a = _normalise_for_hash(rec_a.firm_name or rec_a.investor_name)
                for rec_b in block[i + 1:]:
                    if rec_b.record_id in kept_ids:
                        continue
                    name_b = _normalise_for_hash(rec_b.firm_name or rec_b.investor_name)
                    if not name_a or not name_b:
                        continue
                    ratio = fuzz.token_sort_ratio(name_a, name_b) / 100.0
                    if ratio >= self.threshold:
                        # Mark as duplicate (absorbed by rec_a)
                        kept_ids.add(rec_b.record_id)
                        rec_b.is_duplicate = True
                        logger.debug(
                            f"  Fuzzy dup [{ratio:.2f}]: "
                            f"'{rec_a.firm_name}' ≈ '{rec_b.firm_name}'"
                        )

        return result


# ─────────────────────────────────────────────────────────────────────────────
# Source priority helper
# ─────────────────────────────────────────────────────────────────────────────

def _source_priority(source: str) -> int:
    """
    Higher number = more reliable source.
    Used as tie-breaker when two records have the same completeness.
    """
    PRIORITY = {
        "Curated Dataset":          10,
        "VC Website (Seed List)":    8,
        "OpenVC":                    7,
        "Public Lists":              5,
        "Public JSON Dataset":       3,
    }
    for key, score in PRIORITY.items():
        if key in (source or ""):
            return score
    return 1


# ─────────────────────────────────────────────────────────────────────────────
# Convenience function
# ─────────────────────────────────────────────────────────────────────────────

def deduplicate_records(records: list[InvestorRecord]) -> list[InvestorRecord]:
    return Deduplicator().deduplicate(records)
