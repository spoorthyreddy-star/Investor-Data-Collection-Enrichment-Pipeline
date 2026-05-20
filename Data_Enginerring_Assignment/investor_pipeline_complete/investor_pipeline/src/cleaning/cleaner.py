"""
src/cleaning/cleaner.py
───────────────────────
Data cleaning pipeline.

Transforms raw scraped records into standardised, consistent records by:
  1. Stripping / normalising text fields
  2. Standardising investor_type to the canonical taxonomy
  3. Standardising sector_focus to the canonical taxonomy
  4. Standardising geo_focus to canonical regions
  5. Standardising investment_stage labels
  6. Normalising URLs
  7. Validating / reformatting emails
  8. Computing a completeness score
  9. Dropping records below minimum quality threshold

All transformations are deterministic and logged for auditability.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import (
    INVESTOR_TYPES, SECTORS, GEO_REGIONS, STAGES,
    MIN_COMPLETENESS_SCORE, PROCESSED_DIR,
)
from src.utils.helpers import get_logger, normalise_url, clean_text
from src.utils.schema import InvestorRecord, records_to_df, df_to_records

logger = get_logger("cleaning.cleaner")

# ── Regex helpers ─────────────────────────────────────────────────────────────
_EMAIL_RE  = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
_URL_RE    = re.compile(r"^https?://")


# ─────────────────────────────────────────────────────────────────────────────
# Taxonomy mappings (keyword → canonical label)
# ─────────────────────────────────────────────────────────────────────────────

INVESTOR_TYPE_MAP: dict[str, str] = {
    "vc":                 "Venture Capital",
    "venture":            "Venture Capital",
    "venture capital":    "Venture Capital",
    "angel":              "Angel Investor",
    "family office":      "Family Office",
    "private equity":     "Private Equity",
    "pe ":                "Private Equity",
    "corporate":          "Corporate Venture",
    "cvc":                "Corporate Venture",
    "accelerator":        "Accelerator / Incubator",
    "incubator":          "Accelerator / Incubator",
    "limited partner":    "Limited Partner",
    "lp ":                "Limited Partner",
    "startup fund":       "Startup Fund",
    "strategic":          "Strategic Investor",
    "hedge fund":         "Hedge Fund",
    "sovereign":          "Sovereign Wealth Fund",
    "swf":                "Sovereign Wealth Fund",
    "government":         "Government / Public Fund",
    "public fund":        "Government / Public Fund",
}

SECTOR_MAP: dict[str, str] = {
    "fintech":            "FinTech",
    "finance":            "FinTech",
    "payment":            "FinTech",
    "insurtech":          "InsurTech",
    "insurance":          "InsurTech",
    "health":             "HealthTech",
    "medtech":            "HealthTech",
    "medical":            "HealthTech",
    "edtech":             "EdTech",
    "education":          "EdTech",
    "cleantech":          "CleanTech / GreenTech",
    "climate":            "CleanTech / GreenTech",
    "green":              "CleanTech / GreenTech",
    "energy":             "CleanTech / GreenTech",
    "ai":                 "DeepTech / AI / ML",
    "ml":                 "DeepTech / AI / ML",
    "deep tech":          "DeepTech / AI / ML",
    "deeptech":           "DeepTech / AI / ML",
    "saas":               "SaaS / Enterprise Software",
    "enterprise":         "SaaS / Enterprise Software",
    "b2b software":       "SaaS / Enterprise Software",
    "consumer":           "Consumer",
    "marketplace":        "Marketplace",
    "crypto":             "Crypto / Web3",
    "web3":               "Crypto / Web3",
    "blockchain":         "Crypto / Web3",
    "defi":               "Crypto / Web3",
    "biotech":            "BioTech / Life Sciences",
    "life science":       "BioTech / Life Sciences",
    "pharma":             "BioTech / Life Sciences",
    "agritech":           "AgriTech",
    "agriculture":        "AgriTech",
    "proptech":           "PropTech",
    "real estate":        "PropTech",
    "legaltech":          "LegalTech",
    "legal":              "LegalTech",
    "spacetech":          "SpaceTech",
    "space":              "SpaceTech",
    "mobility":           "Mobility / AutoTech",
    "automotive":         "Mobility / AutoTech",
    "cyber":              "CyberSecurity",
    "security":           "CyberSecurity",
    "hrtech":             "HRTech",
    "hr ":                "HRTech",
    "foodtech":           "FoodTech",
    "food":               "FoodTech",
    "hardware":           "Hardware / IoT",
    "iot":                "Hardware / IoT",
    "media":              "Media / Content",
    "content":            "Media / Content",
    "ecommerce":          "E-commerce",
    "e-commerce":         "E-commerce",
    "gaming":             "Gaming",
    "game":               "Gaming",
    "industrial":         "Industrial Tech",
    "logistics":          "Logistics / Supply Chain",
    "supply chain":       "Logistics / Supply Chain",
    "generalist":         "Generalist",
    "general":            "Generalist",
}

GEO_MAP: dict[str, str] = {
    "usa":                "North America",
    "united states":      "North America",
    "us":                 "North America",
    "canada":             "North America",
    "north america":      "North America",
    "uk":                 "Europe",
    "united kingdom":     "Europe",
    "europe":             "Europe",
    "eu":                 "Europe",
    "germany":            "Europe",
    "france":             "Europe",
    "sweden":             "Europe",
    "netherlands":        "Europe",
    "spain":              "Europe",
    "italy":              "Europe",
    "austria":            "Europe",
    "switzerland":        "Europe",
    "dach":               "DACH",
    "nordics":            "Nordics",
    "nordic":             "Nordics",
    "cee":                "CEE",
    "eastern europe":     "CEE",
    "india":              "South Asia",
    "pakistan":           "South Asia",
    "bangladesh":         "South Asia",
    "south asia":         "South Asia",
    "southeast asia":     "Southeast Asia",
    "sea":                "Southeast Asia",
    "singapore":          "Southeast Asia",
    "indonesia":          "Southeast Asia",
    "vietnam":            "Southeast Asia",
    "asia":               "Asia Pacific",
    "asia pacific":       "Asia Pacific",
    "apac":               "Asia Pacific",
    "china":              "Asia Pacific",
    "japan":              "Asia Pacific",
    "korea":              "Asia Pacific",
    "australia":          "Asia Pacific",
    "latam":              "Latin America",
    "latin america":      "Latin America",
    "brazil":             "Latin America",
    "mexico":             "Latin America",
    "mena":               "MENA",
    "middle east":        "MENA",
    "uae":                "MENA",
    "africa":             "Middle East & Africa",
    "nigeria":            "Middle East & Africa",
    "kenya":              "Middle East & Africa",
    "global":             "Global",
    "worldwide":          "Global",
}

STAGE_MAP: dict[str, str] = {
    "pre-seed":           "Pre-Seed",
    "pre seed":           "Pre-Seed",
    "preseed":            "Pre-Seed",
    "seed":               "Seed",
    "series a":           "Series A",
    "seriesa":            "Series A",
    "series b":           "Series B",
    "seriesb":            "Series B",
    "series c":           "Series C+",
    "series d":           "Series C+",
    "late stage":         "Late Stage",
    "growth":             "Growth",
    "buyout":             "Buyout",
    "debt":               "Debt",
}


# ─────────────────────────────────────────────────────────────────────────────
# Main cleaner
# ─────────────────────────────────────────────────────────────────────────────

class DataCleaner:
    """
    Applies all cleaning transformations to a list of InvestorRecord objects.
    Returns a cleaned list sorted by descending completeness score.
    """

    def clean(self, records: list[InvestorRecord]) -> list[InvestorRecord]:
        logger.info(f"Cleaning {len(records)} raw records …")
        cleaned = []
        dropped = 0

        for rec in records:
            rec = self._clean_record(rec)
            score = rec.completeness()
            if score < MIN_COMPLETENESS_SCORE:
                dropped += 1
                continue
            rec.confidence_score = rec.confidence_score or round(score, 3)
            cleaned.append(rec)

        cleaned.sort(key=lambda r: r.completeness(), reverse=True)
        logger.info(f"Cleaning done: {len(cleaned)} kept, {dropped} dropped")
        return cleaned

    # ── Per-record cleaning ───────────────────────────────────────────────────

    def _clean_record(self, rec: InvestorRecord) -> InvestorRecord:
        rec.investor_name  = self._clean_name(rec.investor_name)
        rec.firm_name      = self._clean_name(rec.firm_name) or rec.investor_name
        rec.investor_type  = self._standardise(rec.investor_type, INVESTOR_TYPE_MAP, "Venture Capital")
        rec.sector_focus   = self._standardise_multi(rec.sector_focus, SECTOR_MAP, "Generalist")
        rec.geo_focus      = self._standardise(rec.geo_focus, GEO_MAP, "Global")
        rec.country        = clean_text(rec.country).title() if rec.country else ""
        rec.website_url    = self._clean_url(rec.website_url)
        rec.source_url     = self._clean_url(rec.source_url)
        rec.linkedin_url   = self._clean_url(rec.linkedin_url or "")
        rec.contact_email  = self._clean_email(rec.contact_email or "")
        rec.investment_stage = self._standardise_multi(
            rec.investment_stage or "", STAGE_MAP, None
        )
        rec.date_collected = rec.date_collected or date.today().isoformat()
        rec.investment_thesis = clean_text(rec.investment_thesis or "")[:600] or None
        rec.notes          = clean_text(rec.notes or "") or None
        return rec

    # ── Field-level cleaners ──────────────────────────────────────────────────

    def _clean_name(self, name: Optional[str]) -> str:
        if not name:
            return ""
        name = clean_text(name)
        # Remove common noise suffixes
        for suffix in [" | LinkedIn", " - Crunchbase", " – Crunchbase"]:
            name = name.replace(suffix, "")
        return name.strip()

    def _clean_url(self, url: Optional[str]) -> str:
        if not url:
            return ""
        url = str(url).strip()
        # Only prepend scheme if there is no scheme at all
        if not re.match(r"^[a-zA-Z][a-zA-Z\d+\-.]*://", url):
            url = "https://" + url
        return normalise_url(url)

    def _clean_email(self, email: Optional[str]) -> Optional[str]:
        if not email:
            return None
        email = email.strip().lower()
        return email if _EMAIL_RE.match(email) else None

    def _standardise(self, value: str, mapping: dict[str, str], default: str) -> str:
        """Map a raw value to a canonical label using keyword matching."""
        if not value:
            return default
        v = value.lower().strip()
        # Exact match first
        if v in mapping:
            return mapping[v]
        # Substring match
        for keyword, canonical in mapping.items():
            if keyword in v:
                return canonical
        return default

    def _standardise_multi(self, value: str, mapping: dict[str, str],
                            default: Optional[str]) -> Optional[str]:
        """
        Handle comma-separated multi-value fields (e.g. sectors, stages).
        Returns the first matched canonical label, or default.
        """
        if not value:
            return default
        parts = [p.strip() for p in value.split(",")]
        matched = []
        for part in parts:
            canonical = self._standardise(part, mapping, "")
            if canonical and canonical not in matched:
                matched.append(canonical)
        if matched:
            return ", ".join(matched[:3])   # cap at 3 for readability
        return default


# ─────────────────────────────────────────────────────────────────────────────
# Convenience function
# ─────────────────────────────────────────────────────────────────────────────

def clean_records(records: list[InvestorRecord]) -> list[InvestorRecord]:
    """Clean and return a list of InvestorRecord objects."""
    return DataCleaner().clean(records)
