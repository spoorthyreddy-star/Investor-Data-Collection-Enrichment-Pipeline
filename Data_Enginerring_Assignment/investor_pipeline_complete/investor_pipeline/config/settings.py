"""
Global configuration for the Investor Data Pipeline.
All tunable parameters live here to avoid magic numbers scattered through code.
"""

import os
from pathlib import Path

# ── Project root (resolves regardless of CWD) ────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent

# ── Data layer paths ──────────────────────────────────────────────────────────
DATA_DIR        = ROOT_DIR / "data"
RAW_DIR         = DATA_DIR / "raw"
PROCESSED_DIR   = DATA_DIR / "processed"
ENRICHED_DIR    = DATA_DIR / "enriched"
VALIDATED_DIR   = DATA_DIR / "validated"
FINAL_DIR       = DATA_DIR / "final"
OUTPUT_DIR      = ROOT_DIR / "output"
LOGS_DIR        = ROOT_DIR / "logs"

# Ensure all directories exist at import time
for _d in [RAW_DIR, PROCESSED_DIR, ENRICHED_DIR, VALIDATED_DIR, FINAL_DIR, OUTPUT_DIR, LOGS_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ── HTTP / scraping settings ──────────────────────────────────────────────────
REQUEST_TIMEOUT     = 15          # seconds per HTTP request
REQUEST_DELAY_MIN   = 1.5         # minimum polite crawl delay (seconds)
REQUEST_DELAY_MAX   = 4.0         # maximum polite crawl delay (seconds)
MAX_RETRIES         = 3
BACKOFF_FACTOR      = 2           # exponential back-off multiplier
MAX_CONCURRENT      = 5           # async semaphore limit

# Rotate through these to avoid single-UA blocking (no impersonation intended)
USER_AGENTS = [
    "Mozilla/5.0 (compatible; InvestorResearchBot/1.0; +https://example.com/bot)",
    "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# ── Data quality thresholds ───────────────────────────────────────────────────
MIN_COMPLETENESS_SCORE  = 0.40    # drop records below 40 % field fill
DEDUPE_SIMILARITY_RATIO = 0.88    # fuzzy-match threshold for near-duplicates
CONFIDENCE_HIGH         = 0.85
CONFIDENCE_MEDIUM       = 0.60
CONFIDENCE_LOW          = 0.40

# ── Mandatory dataset fields ──────────────────────────────────────────────────
MANDATORY_FIELDS = [
    "investor_name",
    "investor_type",
    "firm_name",
    "website_url",
    "country",
    "geo_focus",
    "sector_focus",
    "source_url",
    "data_source",
    "date_collected",
]

OPTIONAL_FIELDS = [
    "city_region",
    "investment_thesis",
    "investment_stage",
    "ticket_size",
    "portfolio_companies",
    "key_partners",
    "contact_email",
    "contact_phone",
    "linkedin_url",
    "confidence_score",
    "notes",
]

ALL_FIELDS = MANDATORY_FIELDS + OPTIONAL_FIELDS

# ── Investor type taxonomy ────────────────────────────────────────────────────
INVESTOR_TYPES = [
    "Venture Capital",
    "Angel Investor",
    "Family Office",
    "Private Equity",
    "Corporate Venture",
    "Accelerator / Incubator",
    "Limited Partner",
    "Startup Fund",
    "Strategic Investor",
    "Hedge Fund",
    "Sovereign Wealth Fund",
    "Government / Public Fund",
    "Other",
]

# ── Geographic focus taxonomy ─────────────────────────────────────────────────
GEO_REGIONS = [
    "Global",
    "North America",
    "Europe",
    "Asia Pacific",
    "South Asia",
    "Southeast Asia",
    "Latin America",
    "Middle East & Africa",
    "DACH",
    "Nordics",
    "CEE",
    "MENA",
]

# ── Sector taxonomy ───────────────────────────────────────────────────────────
SECTORS = [
    "FinTech", "HealthTech", "EdTech", "CleanTech / GreenTech",
    "DeepTech / AI / ML", "SaaS / Enterprise Software", "Consumer",
    "Marketplace", "Crypto / Web3", "BioTech / Life Sciences",
    "AgriTech", "PropTech", "LegalTech", "SpaceTech", "Mobility / AutoTech",
    "CyberSecurity", "HRTech", "InsurTech", "FoodTech", "Media / Content",
    "E-commerce", "Gaming", "Hardware / IoT", "Industrial Tech",
    "Logistics / Supply Chain", "Generalist",
]

# ── Investment stage taxonomy ─────────────────────────────────────────────────
STAGES = ["Pre-Seed", "Seed", "Series A", "Series B", "Series C+",
          "Growth", "Late Stage", "Buyout", "Debt", "Generalist"]

# ── AI enrichment ─────────────────────────────────────────────────────────────
ANTHROPIC_MODEL     = "claude-sonnet-4-20250514"
AI_BATCH_SIZE       = 20          # records per AI enrichment call
AI_MAX_TOKENS       = 1500

# ── Export ────────────────────────────────────────────────────────────────────
FINAL_CSV_NAME  = "Investor_Dataset.csv"
FINAL_JSON_NAME = "Investor_Dataset.json"
FINAL_XLSX_NAME = "Investor_Dataset.xlsx"
