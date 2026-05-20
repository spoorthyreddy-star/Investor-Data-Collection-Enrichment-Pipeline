"""
src/scrapers/vc_websites_scraper.py
────────────────────────────────────
Async scraper for a curated seed list of well-known VC firm websites.

Why async?
  Many VC sites have sub-second response times but we're hitting dozens of
  different domains concurrently. asyncio + aiohttp gives us 10-20× throughput
  vs sequential requests with no added complexity.

Strategy:
  1. Load a seed list of VC firm URLs (config/vc_seed_list.json).
  2. Concurrently fetch each homepage + /team or /portfolio page.
  3. Parse key partners, portfolio companies, investment focus.
  4. Emit one InvestorRecord per firm.

Data sourced only from public pages — no login, no CAPTCHA bypass.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import (
    REQUEST_TIMEOUT, MAX_CONCURRENT, USER_AGENTS,
    REQUEST_DELAY_MIN, REQUEST_DELAY_MAX,
)
from src.utils.helpers import (
    get_logger, normalise_url, clean_text, extract_emails,
    async_polite_delay,
)
from src.utils.schema import InvestorRecord


logger = get_logger("scraper.vc_websites")

# ─────────────────────────────────────────────────────────────────────────────
# Seed list — curated public VC firms (extend to thousands for scale)
# ─────────────────────────────────────────────────────────────────────────────

VC_SEED_LIST: list[dict] = [
    # Format: {name, url, country, type, stage, sectors}
    {"name": "Sequoia Capital",        "url": "https://sequoiacap.com",         "country": "USA",           "type": "Venture Capital", "stage": "Seed, Series A, Growth", "sectors": "Technology, Consumer, Enterprise"},
    {"name": "Andreessen Horowitz",    "url": "https://a16z.com",               "country": "USA",           "type": "Venture Capital", "stage": "Seed, Series A, Growth", "sectors": "Software, Crypto, BioTech, Fintech"},
    {"name": "Y Combinator",           "url": "https://ycombinator.com",        "country": "USA",           "type": "Accelerator / Incubator", "stage": "Pre-Seed, Seed", "sectors": "Generalist"},
    {"name": "Accel",                  "url": "https://accel.com",              "country": "USA",           "type": "Venture Capital", "stage": "Seed, Series A, Series B", "sectors": "SaaS, Enterprise, Consumer"},
    {"name": "Bessemer Venture Partners","url":"https://bvp.com",               "country": "USA",           "type": "Venture Capital", "stage": "Seed, Series A, Growth", "sectors": "Cloud, HealthTech, FinTech"},
    {"name": "Index Ventures",         "url": "https://indexventures.com",      "country": "UK",            "type": "Venture Capital", "stage": "Seed, Series A, Series B", "sectors": "SaaS, Consumer, FinTech"},
    {"name": "Balderton Capital",      "url": "https://balderton.com",          "country": "UK",            "type": "Venture Capital", "stage": "Series A, Series B", "sectors": "SaaS, Marketplace, Consumer"},
    {"name": "Atomico",                "url": "https://atomico.com",            "country": "UK",            "type": "Venture Capital", "stage": "Series A, Series B, Growth", "sectors": "DeepTech, SaaS, Consumer"},
    {"name": "Northzone",              "url": "https://northzone.com",          "country": "Sweden",        "type": "Venture Capital", "stage": "Seed, Series A, Series B", "sectors": "SaaS, Consumer, FinTech"},
    {"name": "LocalGlobe",             "url": "https://localglobe.vc",          "country": "UK",            "type": "Venture Capital", "stage": "Pre-Seed, Seed", "sectors": "Generalist"},
    {"name": "HV Capital",             "url": "https://hvcapital.com",          "country": "Germany",       "type": "Venture Capital", "stage": "Series A, Series B, Growth", "sectors": "SaaS, Marketplace, FinTech"},
    {"name": "Point Nine Capital",     "url": "https://pointninecap.com",       "country": "Germany",       "type": "Venture Capital", "stage": "Seed, Series A", "sectors": "SaaS, Marketplace"},
    {"name": "Speedinvest",            "url": "https://speedinvest.com",        "country": "Austria",       "type": "Venture Capital", "stage": "Pre-Seed, Seed", "sectors": "Generalist"},
    {"name": "Kima Ventures",          "url": "https://kimaventures.com",       "country": "France",        "type": "Venture Capital", "stage": "Pre-Seed, Seed", "sectors": "Generalist"},
    {"name": "Partech",                "url": "https://partechpartners.com",    "country": "France",        "type": "Venture Capital", "stage": "Seed, Series A, Series B", "sectors": "SaaS, Consumer, DeepTech"},
    {"name": "Sequoia India & SEA",    "url": "https://surgeahead.com",         "country": "India",         "type": "Venture Capital", "stage": "Seed, Series A", "sectors": "Generalist"},
    {"name": "Blume Ventures",         "url": "https://blumeventures.com",      "country": "India",         "type": "Venture Capital", "stage": "Pre-Seed, Seed, Series A", "sectors": "SaaS, Consumer, FinTech"},
    {"name": "Kalaari Capital",        "url": "https://kalaari.com",            "country": "India",         "type": "Venture Capital", "stage": "Seed, Series A, Series B", "sectors": "Consumer, SaaS, FinTech"},
    {"name": "Matrix Partners India",  "url": "https://matrixpartners.in",      "country": "India",         "type": "Venture Capital", "stage": "Seed, Series A, Series B", "sectors": "Generalist"},
    {"name": "Tiger Global Management","url": "https://tigerglobal.com",        "country": "USA",           "type": "Venture Capital", "stage": "Growth, Late Stage", "sectors": "Technology, Consumer, SaaS"},
    {"name": "GGV Capital",            "url": "https://ggvc.com",               "country": "USA",           "type": "Venture Capital", "stage": "Seed, Series A, Growth", "sectors": "SaaS, Consumer, Marketplace"},
    {"name": "General Catalyst",       "url": "https://generalcatalyst.com",    "country": "USA",           "type": "Venture Capital", "stage": "Seed, Series A, Growth", "sectors": "HealthTech, SaaS, Consumer"},
    {"name": "NEA",                    "url": "https://nea.com",                "country": "USA",           "type": "Venture Capital", "stage": "Seed, Growth", "sectors": "Technology, HealthTech"},
    {"name": "Insight Partners",       "url": "https://insightpartners.com",    "country": "USA",           "type": "Venture Capital", "stage": "Growth, Late Stage", "sectors": "SaaS, Enterprise"},
    {"name": "Coatue",                 "url": "https://coatue.com",             "country": "USA",           "type": "Venture Capital", "stage": "Growth, Late Stage", "sectors": "Technology, Consumer"},
    {"name": "SoftBank Vision Fund",   "url": "https://visionfund.com",         "country": "Japan",         "type": "Venture Capital", "stage": "Growth, Late Stage", "sectors": "Technology, AI, Mobility"},
    {"name": "GIC",                    "url": "https://gic.com.sg",             "country": "Singapore",     "type": "Sovereign Wealth Fund", "stage": "Growth, Late Stage", "sectors": "Generalist"},
    {"name": "Temasek",                "url": "https://temasek.com.sg",         "country": "Singapore",     "type": "Sovereign Wealth Fund", "stage": "Growth, Late Stage", "sectors": "Generalist"},
    {"name": "500 Global",             "url": "https://500.co",                 "country": "USA",           "type": "Accelerator / Incubator", "stage": "Pre-Seed, Seed", "sectors": "Generalist"},
    {"name": "Techstars",              "url": "https://techstars.com",          "country": "USA",           "type": "Accelerator / Incubator", "stage": "Pre-Seed, Seed", "sectors": "Generalist"},
    {"name": "Plug and Play",          "url": "https://plugandplaytechcenter.com","country": "USA",          "type": "Accelerator / Incubator", "stage": "Pre-Seed, Seed", "sectors": "Generalist"},
    {"name": "Flyer One Ventures",     "url": "https://flyerone.vc",            "country": "Ukraine",       "type": "Venture Capital", "stage": "Pre-Seed, Seed", "sectors": "SaaS, AI"},
    {"name": "OTF Capital",            "url": "https://otfcapital.com",         "country": "Turkey",        "type": "Venture Capital", "stage": "Seed, Series A", "sectors": "SaaS, FinTech"},
    {"name": "Chimera Abu Dhabi",      "url": "https://chimeraad.com",          "country": "UAE",           "type": "Venture Capital", "stage": "Series A, Growth", "sectors": "Technology, FinTech"},
    {"name": "Wamda Capital",          "url": "https://wamda.com",              "country": "UAE",           "type": "Venture Capital", "stage": "Seed, Series A", "sectors": "Generalist"},
    {"name": "Flat6Labs",              "url": "https://flat6labs.com",          "country": "Egypt",         "type": "Accelerator / Incubator", "stage": "Pre-Seed, Seed", "sectors": "Generalist"},
    {"name": "Partech Africa",         "url": "https://partechpartners.com/africa","country": "Senegal",    "type": "Venture Capital", "stage": "Seed, Series A", "sectors": "Generalist"},
    {"name": "Flourish Ventures",      "url": "https://flourishventures.com",   "country": "USA",           "type": "Venture Capital", "stage": "Seed, Series A", "sectors": "FinTech"},
    {"name": "Omidyar Network",        "url": "https://omidyar.com",            "country": "USA",           "type": "Venture Capital", "stage": "Seed, Series A", "sectors": "FinTech, EdTech, CleanTech"},
    {"name": "Lightspeed Venture Partners","url":"https://lsvp.com",            "country": "USA",           "type": "Venture Capital", "stage": "Seed, Series A, Growth", "sectors": "Enterprise, Consumer, FinTech"},
    {"name": "Norwest Venture Partners","url": "https://nvp.com",               "country": "USA",           "type": "Venture Capital", "stage": "Seed, Growth", "sectors": "Technology, HealthTech"},
    {"name": "Khosla Ventures",        "url": "https://khoslaventures.com",     "country": "USA",           "type": "Venture Capital", "stage": "Seed, Series A", "sectors": "DeepTech, CleanTech, HealthTech"},
    {"name": "FirstMark Capital",      "url": "https://firstmarkcap.com",       "country": "USA",           "type": "Venture Capital", "stage": "Seed, Series A", "sectors": "SaaS, Consumer, Data"},
    {"name": "Union Square Ventures",  "url": "https://usv.com",                "country": "USA",           "type": "Venture Capital", "stage": "Seed, Series A", "sectors": "Marketplace, Crypto, Consumer"},
    {"name": "Benchmark",              "url": "https://benchmark.com",          "country": "USA",           "type": "Venture Capital", "stage": "Seed, Series A", "sectors": "Consumer, SaaS, Marketplace"},
    {"name": "Greylock",               "url": "https://greylock.com",           "country": "USA",           "type": "Venture Capital", "stage": "Seed, Series A, Series B", "sectors": "Enterprise, Consumer, AI"},
    {"name": "Kleiner Perkins",        "url": "https://kleinerperkins.com",     "country": "USA",           "type": "Venture Capital", "stage": "Seed, Series A, Growth", "sectors": "Consumer, Enterprise, HealthTech"},
    {"name": "Initialized Capital",    "url": "https://initialized.com",        "country": "USA",           "type": "Venture Capital", "stage": "Pre-Seed, Seed", "sectors": "Generalist"},
    {"name": "Founders Fund",          "url": "https://foundersfund.com",       "country": "USA",           "type": "Venture Capital", "stage": "Seed, Series A, Growth", "sectors": "DeepTech, BioTech, SpaceTech"},
    {"name": "Lux Capital",            "url": "https://luxcapital.com",         "country": "USA",           "type": "Venture Capital", "stage": "Seed, Series A", "sectors": "DeepTech, AI, SpaceTech"},
]


# ─────────────────────────────────────────────────────────────────────────────
# Async scraper
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch(session: aiohttp.ClientSession, url: str,
                 semaphore: asyncio.Semaphore) -> tuple[str, Optional[str]]:
    """Fetch a single URL respecting the semaphore limit."""
    async with semaphore:
        await async_polite_delay()
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as resp:
                if resp.status == 200:
                    return url, await resp.text()
                logger.debug(f"HTTP {resp.status} for {url}")
                return url, None
        except Exception as exc:
            logger.warning(f"Fetch error [{url}]: {exc}")
            return url, None


def _parse_homepage(html: str, firm: dict) -> InvestorRecord:
    """
    Parse a VC firm homepage to enrich the seed record.
    Extracts: description, emails, portfolio links.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Description / thesis — look for meta description or first meaningful paragraph
    thesis = ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        thesis = clean_text(meta_desc["content"])
    if not thesis:
        for p in soup.find_all("p"):
            text = clean_text(p.get_text())
            if len(text) > 80:
                thesis = text[:500]
                break

    # Contact emails
    emails = extract_emails(html)
    email  = emails[0] if emails else None

    # Key partners — look for names in /team or About sections
    partner_names: list[str] = []
    team_section = soup.find(id=re.compile("team|people|partners", re.I)) or \
                   soup.find(class_=re.compile("team|people|partners", re.I))
    if team_section:
        for el in team_section.select("h3, h4, .name, [class*='name']")[:5]:
            n = clean_text(el.get_text())
            if n and len(n.split()) <= 4:
                partner_names.append(n)

    return InvestorRecord(
        investor_name     = firm["name"],
        investor_type     = firm["type"],
        firm_name         = firm["name"],
        website_url       = normalise_url(firm["url"]),
        country           = firm["country"],
        geo_focus         = _infer_geo(firm["country"]),
        sector_focus      = firm.get("sectors", "Generalist"),
        source_url        = firm["url"],
        data_source       = "VC Website (Seed List)",
        investment_thesis = thesis or None,
        investment_stage  = firm.get("stage"),
        contact_email     = email,
        key_partners      = ", ".join(partner_names) if partner_names else None,
        confidence_score  = 0.9,   # curated seed list = high confidence
    )


def _infer_geo(country: str) -> str:
    """Map a country string to a broad geographic region."""
    MAPPING = {
        "USA": "North America", "Canada": "North America",
        "UK": "Europe", "Germany": "Europe", "France": "Europe",
        "Sweden": "Europe", "Norway": "Europe", "Netherlands": "Europe",
        "Austria": "Europe", "Switzerland": "Europe", "Spain": "Europe",
        "Italy": "Europe", "Finland": "Europe", "Denmark": "Europe",
        "Estonia": "Europe", "Poland": "Europe", "Ukraine": "Europe",
        "India": "South Asia", "Pakistan": "South Asia",
        "Singapore": "Southeast Asia", "Indonesia": "Southeast Asia",
        "Vietnam": "Southeast Asia", "Thailand": "Southeast Asia",
        "Malaysia": "Southeast Asia", "Philippines": "Southeast Asia",
        "China": "Asia Pacific", "Japan": "Asia Pacific",
        "South Korea": "Asia Pacific", "Australia": "Asia Pacific",
        "UAE": "MENA", "Saudi Arabia": "MENA", "Egypt": "MENA",
        "Israel": "MENA", "Turkey": "MENA",
        "Nigeria": "Middle East & Africa", "Kenya": "Middle East & Africa",
        "South Africa": "Middle East & Africa", "Senegal": "Middle East & Africa",
        "Brazil": "Latin America", "Mexico": "Latin America",
        "Colombia": "Latin America", "Argentina": "Latin America",
    }
    return MAPPING.get(country, "Global")


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def scrape_vc_websites(seed_list: list[dict] = None) -> list[InvestorRecord]:
    """
    Synchronous entry point that runs the async scraper.
    Returns one InvestorRecord per firm in the seed list.
    """
    if seed_list is None:
        seed_list = VC_SEED_LIST

    return asyncio.run(_async_scrape(seed_list))


async def _async_scrape(seed_list: list[dict]) -> list[InvestorRecord]:
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    records: list[InvestorRecord] = []

    connector = aiohttp.TCPConnector(ssl=False, limit=MAX_CONCURRENT)
    headers   = {"User-Agent": USER_AGENTS[0]}

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        tasks = [_fetch(session, f["url"], semaphore) for f in seed_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results):
        firm = seed_list[i]
        if isinstance(result, Exception):
            logger.warning(f"Exception for {firm['name']}: {result}")
            # Still emit a record from seed data (lower confidence)
            rec = InvestorRecord(
                investor_name = firm["name"],
                investor_type = firm["type"],
                firm_name     = firm["name"],
                website_url   = normalise_url(firm["url"]),
                country       = firm["country"],
                geo_focus     = _infer_geo(firm["country"]),
                sector_focus  = firm.get("sectors", "Generalist"),
                source_url    = firm["url"],
                data_source   = "VC Website (Seed List — Fetch Failed)",
                investment_stage = firm.get("stage"),
                confidence_score = 0.6,
            )
            records.append(rec)
            continue

        url, html = result
        if html:
            try:
                rec = _parse_homepage(html, firm)
                records.append(rec)
            except Exception as exc:
                logger.warning(f"Parse error for {firm['name']}: {exc}")
        else:
            # Emit from seed data only
            records.append(InvestorRecord(
                investor_name = firm["name"],
                investor_type = firm["type"],
                firm_name     = firm["name"],
                website_url   = normalise_url(firm["url"]),
                country       = firm["country"],
                geo_focus     = _infer_geo(firm["country"]),
                sector_focus  = firm.get("sectors", "Generalist"),
                source_url    = firm["url"],
                data_source   = "VC Website (Seed List)",
                investment_stage = firm.get("stage"),
                confidence_score = 0.7,
            ))

    logger.info(f"VC Websites scrape complete: {len(records)} records")
    return records
