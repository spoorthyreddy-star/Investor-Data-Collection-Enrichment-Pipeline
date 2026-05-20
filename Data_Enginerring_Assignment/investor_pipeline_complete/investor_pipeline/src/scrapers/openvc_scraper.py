"""
src/scrapers/openvc_scraper.py
──────────────────────────────
Scraper for OpenVC (https://openvc.app) — a publicly accessible directory
of venture capital firms.

OpenVC does NOT require login for browsing investor profiles. We only access
public pages and respect rate limits via polite_delay().

Strategy:
  1. Paginate through the /vcs directory listing.
  2. For each VC card, extract name, description, focus, stage, and URL.
  3. Optionally follow the individual profile page for richer data.

NOTE: If OpenVC's HTML structure changes, update the CSS selectors in the
      _parse_listing_page() method.
"""

from __future__ import annotations

import re
from typing import Optional
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.scrapers.base_scraper import BaseScraper
from src.utils.helpers import normalise_url, clean_text
from src.utils.schema import InvestorRecord
from config.settings import SECTORS, STAGES


class OpenVCScraper(BaseScraper):
    """
    Scrapes the public OpenVC investor directory.

    Pagination: OpenVC uses ?page=N query params on /vcs.
    Each page contains ~20 VC cards with structured metadata.
    """

    SOURCE_NAME = "OpenVC"
    BASE_URL    = "https://openvc.app"
    LIST_URL    = "https://openvc.app/vcs"
    MAX_PAGES   = 50  # cap at 50 pages (~1000 records); increase for scale

    def scrape(self) -> list[InvestorRecord]:
        records: list[InvestorRecord] = []
        self.logger.info(f"Starting OpenVC scrape (max {self.MAX_PAGES} pages)")

        for page_num in range(1, self.MAX_PAGES + 1):
            self.logger.info(f"  Fetching page {page_num}/{self.MAX_PAGES}")
            html = self.safe_get(self.LIST_URL, params={"page": page_num})
            if not html:
                self.logger.warning(f"  Empty response on page {page_num}, stopping.")
                break

            page_records = self._parse_listing_page(html, page_num)
            if not page_records:
                self.logger.info(f"  No records found on page {page_num}, end of data.")
                break

            records.extend(page_records)
            self.logger.info(f"  Collected {len(page_records)} records (total: {len(records)})")

        self.logger.info(f"OpenVC scrape complete: {len(records)} records")
        return records

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _parse_listing_page(self, html: str, page_num: int) -> list[InvestorRecord]:
        """
        Parse a single listing page and return InvestorRecord objects.
        CSS selectors target the publicly visible VC card grid.
        """
        soup = self.parse(html)
        records = []

        # OpenVC renders VC cards as article or div elements with class 'vc-card'
        # Adjust selector if site structure changes
        cards = (
            soup.select("div.vc-card") or
            soup.select("article.investor-card") or
            soup.select("[data-testid='investor-card']") or
            soup.select("div.card")
        )

        if not cards:
            # Fallback: try generic list items that look like VC entries
            cards = soup.select("li.investor, div.investor-item")

        for card in cards:
            try:
                record = self._parse_card(card)
                if record:
                    records.append(record)
            except Exception as exc:
                self.logger.debug(f"Card parse error (page {page_num}): {exc}")

        return records

    def _parse_card(self, card) -> Optional[InvestorRecord]:
        """Extract structured data from a single VC card element."""
        # ── Name ──────────────────────────────────────────────────────────
        name_el = (
            card.select_one("h2, h3, .vc-name, .firm-name, [class*='name']")
        )
        firm_name = clean_text(name_el.get_text()) if name_el else ""
        if not firm_name:
            return None

        # ── Website URL ───────────────────────────────────────────────────
        link_el   = card.select_one("a[href]")
        profile_url = ""
        if link_el:
            href = link_el.get("href", "")
            if href.startswith("http"):
                profile_url = normalise_url(href)
            elif href.startswith("/"):
                profile_url = normalise_url(f"{self.BASE_URL}{href}")

        # ── Description / thesis ─────────────────────────────────────────
        desc_el = card.select_one("p, .description, .thesis, [class*='desc']")
        thesis  = clean_text(desc_el.get_text()) if desc_el else ""

        # ── Sectors ───────────────────────────────────────────────────────
        tag_els  = card.select(".tag, .badge, .sector, [class*='tag'], [class*='sector']")
        raw_tags = [clean_text(t.get_text()) for t in tag_els]
        sector_focus = self._classify_sectors(raw_tags, thesis)

        # ── Stage ─────────────────────────────────────────────────────────
        stage_focus = self._classify_stage(raw_tags, thesis)

        # ── Geography ─────────────────────────────────────────────────────
        geo_el  = card.select_one(".location, .geo, [class*='location'], [class*='country']")
        country = clean_text(geo_el.get_text()) if geo_el else ""
        geo_focus = country or "Global"

        # ── Build record ──────────────────────────────────────────────────
        source_url = f"{self.LIST_URL}"
        if profile_url and self.BASE_URL in profile_url:
            source_url = profile_url

        return self.make_record(
            investor_name    = firm_name,
            investor_type    = "Venture Capital",
            firm_name        = firm_name,
            website_url      = profile_url,
            country          = country,
            geo_focus        = geo_focus,
            sector_focus     = sector_focus,
            source_url       = source_url,
            investment_thesis = thesis[:500] if thesis else None,
            investment_stage  = stage_focus,
        )

    # ── Classification helpers ────────────────────────────────────────────────

    def _classify_sectors(self, tags: list[str], text: str = "") -> str:
        """
        Map raw tag strings or free text to our canonical sector taxonomy.
        Returns the best-matching sector, or 'Generalist' if nothing matches.
        """
        combined = " ".join(tags + [text]).lower()
        MAPPING = {
            "fintech":       "FinTech",
            "finance":       "FinTech",
            "payment":       "FinTech",
            "health":        "HealthTech",
            "medtech":       "HealthTech",
            "edtech":        "EdTech",
            "education":     "EdTech",
            "cleantech":     "CleanTech / GreenTech",
            "climate":       "CleanTech / GreenTech",
            "green":         "CleanTech / GreenTech",
            "ai":            "DeepTech / AI / ML",
            "machine learning": "DeepTech / AI / ML",
            "deep tech":     "DeepTech / AI / ML",
            "saas":          "SaaS / Enterprise Software",
            "enterprise":    "SaaS / Enterprise Software",
            "b2b":           "SaaS / Enterprise Software",
            "crypto":        "Crypto / Web3",
            "web3":          "Crypto / Web3",
            "blockchain":    "Crypto / Web3",
            "biotech":       "BioTech / Life Sciences",
            "life science":  "BioTech / Life Sciences",
            "pharma":        "BioTech / Life Sciences",
            "consumer":      "Consumer",
            "marketplace":   "Marketplace",
            "security":      "CyberSecurity",
            "cyber":         "CyberSecurity",
            "proptech":      "PropTech",
            "real estate":   "PropTech",
            "foodtech":      "FoodTech",
            "food":          "FoodTech",
            "agri":          "AgriTech",
            "gaming":        "Gaming",
            "game":          "Gaming",
            "mobility":      "Mobility / AutoTech",
            "automotive":    "Mobility / AutoTech",
            "logistics":     "Logistics / Supply Chain",
            "supply chain":  "Logistics / Supply Chain",
            "hrtech":        "HRTech",
            "insurance":     "InsurTech",
            "legaltech":     "LegalTech",
            "legal":         "LegalTech",
            "space":         "SpaceTech",
            "hardware":      "Hardware / IoT",
            "iot":           "Hardware / IoT",
            "media":         "Media / Content",
            "content":       "Media / Content",
            "ecommerce":     "E-commerce",
            "e-commerce":    "E-commerce",
            "industrial":    "Industrial Tech",
        }
        for keyword, canonical in MAPPING.items():
            if keyword in combined:
                return canonical
        return "Generalist"

    def _classify_stage(self, tags: list[str], text: str = "") -> Optional[str]:
        """Attempt to extract investment stage from tags / text."""
        combined = " ".join(tags + [text]).lower()
        for stage_kw, canonical in [
            ("pre-seed", "Pre-Seed"), ("preseed", "Pre-Seed"),
            ("seed", "Seed"),
            ("series a", "Series A"),
            ("series b", "Series B"),
            ("series c", "Series C+"),
            ("growth", "Growth"),
            ("late stage", "Late Stage"),
            ("buyout", "Buyout"),
        ]:
            if stage_kw in combined:
                return canonical
        return None
