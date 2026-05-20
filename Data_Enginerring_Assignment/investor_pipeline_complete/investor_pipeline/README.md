# Investor Data Collection & Enrichment Pipeline

> **Production-quality Python pipeline** for collecting, cleaning, deduplicating, enriching, and validating 2,000+ investor records for AI-ready data ingestion.

---

## Overview

This pipeline automates the full lifecycle of investor data collection — from web scraping and public API access through to structured, validated, AI-ready datasets exported as CSV, JSON, and Excel.

It was built to engineering production standards: modular architecture, typed data models, comprehensive tests, structured logging, and clean separation of concerns between each pipeline stage.

---

## Architecture

```
Raw Sources                Pipeline Stages              Output
─────────────             ─────────────────            ──────────────
Curated Dataset  ──┐
OpenVC           ──┤   Scrape → Clean → Dedup    →    data/raw/
VC Websites      ──┤     → Enrich → Validate     →    data/processed/
Public JSON      ──┘       → Export              →    output/
                                                        ├── Investor_Dataset.csv
                                                        ├── Investor_Dataset.json
                                                        ├── Investor_Dataset.xlsx
                                                        └── pipeline_summary.json
```

### Data Flow

| Stage | Input | Output | Key Operation |
|-------|-------|--------|---------------|
| **Scrape** | Seed URLs, public pages | `data/raw/raw_combined.json` | HTTP + async fetch |
| **Clean** | Raw records | `data/processed/cleaned.json` | Normalise, standardise, filter |
| **Dedup** | Cleaned records | `data/processed/deduped.json` | SHA-256 fingerprint + fuzzy match |
| **Enrich** | Deduped records | `data/enriched/enriched.json` | Rule-based + optional Claude AI |
| **Validate** | Enriched records | `data/validated/validated.json` | Schema + format + semantic checks |
| **Export** | Validated records | `output/` | CSV, JSON, Excel |

---

## Quickstart

### 1. Clone and install

```bash
git clone https://github.com/yourname/investor-pipeline.git
cd investor-pipeline
pip install -r requirements.txt
```

### 2. (Optional) Set your Anthropic API key for AI enrichment

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Run the pipeline

```bash
# Full pipeline — no AI enrichment
python pipeline_runner.py

# Full pipeline with Claude AI enrichment
python pipeline_runner.py --use-ai

# Scrape and clean only (no AI, no export)
python pipeline_runner.py --scrape-only

# Re-export from existing validated data
python pipeline_runner.py --export-only
```

### 4. Find your data

```
output/
├── Investor_Dataset.csv        ← Primary deliverable
├── Investor_Dataset.json       ← AI ingestion format
├── Investor_Dataset.xlsx       ← Stakeholder-friendly Excel
├── pipeline_summary.json       ← Distribution breakdown
└── run_summary.json            ← Pipeline execution stats
```

---

## Dataset Schema

### Mandatory Fields (100% coverage guaranteed)

| Field | Type | Description |
|-------|------|-------------|
| `investor_name` | str | Full name of the investor or firm |
| `investor_type` | str | Taxonomy: VC, Angel, Family Office, PE, etc. |
| `firm_name` | str | Legal / trading name of the firm |
| `website_url` | str | Normalised public URL |
| `country` | str | Country of headquarters |
| `geo_focus` | str | Geographic investment focus (canonical region) |
| `sector_focus` | str | One or more canonical sectors |
| `source_url` | str | URL where the record was sourced |
| `data_source` | str | Source system name |
| `date_collected` | str | ISO-8601 collection date |

### Nice-to-Have Fields

| Field | Description |
|-------|-------------|
| `city_region` | City or sub-national region |
| `investment_thesis` | Public investment thesis (≤600 chars) |
| `investment_stage` | Canonical stage(s): Pre-Seed → Buyout |
| `ticket_size` | Typical cheque size |
| `portfolio_companies` | Known portfolio companies |
| `key_partners` | Named decision-makers |
| `contact_email` | Public contact email |
| `linkedin_url` | LinkedIn profile / company page |
| `confidence_score` | 0.0–1.0 quality score |
| `notes` | Flags, warnings, caveats |

---

## Project Structure

```
investor_pipeline/
├── config/
│   ├── __init__.py
│   └── settings.py              ← All tunable parameters
├── src/
│   ├── scrapers/
│   │   ├── base_scraper.py      ← Abstract base class
│   │   ├── openvc_scraper.py    ← OpenVC public directory
│   │   ├── vc_websites_scraper.py ← Async VC homepage scraper
│   │   └── public_lists_scraper.py ← Curated + open JSON datasets
│   ├── cleaning/
│   │   ├── cleaner.py           ← Normalisation & standardisation
│   │   └── deduplicator.py      ← Exact + fuzzy dedup
│   ├── enrichment/
│   │   └── ai_enricher.py       ← Rule-based + Claude AI enrichment
│   ├── validation/
│   │   └── validator.py         ← 4-layer quality validation
│   ├── export/
│   │   └── exporter.py          ← CSV / JSON / Excel export
│   └── utils/
│       ├── helpers.py           ← HTTP, logging, text utilities
│       └── schema.py            ← InvestorRecord dataclass
├── data/
│   ├── raw/                     ← Stage 1 output
│   ├── processed/               ← Stages 2–3 output
│   ├── enriched/                ← Stage 4 output
│   └── validated/               ← Stage 5 output
├── output/                      ← Final exports (CSV, JSON, Excel)
├── tests/
│   └── test_pipeline.py         ← 40 unit + integration tests
├── logs/                        ← Per-module log files
├── docs/
│   ├── METHODOLOGY.md
│   └── SUMMARY_REPORT.md
├── pipeline_runner.py           ← Main orchestrator / CLI entry point
└── requirements.txt
```

---

## Data Sources

All data is sourced from **publicly accessible** pages only. No paywalls, no CAPTCHAs, no login bypass.

| Source | Type | Records | Notes |
|--------|------|---------|-------|
| Curated Dataset | Static | 118+ | Hand-curated, highest confidence |
| VC Websites | Async scrape | 50 | Seed list of known firms |
| OpenVC | Web scrape | Variable | Public investor directory |
| Public JSON Repos | HTTP fetch | Variable | Open GitHub datasets |

---

## Scaling to 100,000+ Records

See [docs/METHODOLOGY.md](docs/METHODOLOGY.md) for the full scaling strategy. Key points:

- **Replace sequential scraping** with Scrapy + Redis queue for distributed crawling
- **Replace in-process dedup** with MinHash LSH (Locality Sensitive Hashing) — O(n) vs O(n²)
- **Add a database layer**: PostgreSQL + SQLAlchemy for persistence and incremental updates
- **Use Airflow / Prefect** for orchestration with retry/backfill
- **Partition AI enrichment** into async batches across multiple API keys
- **Add Kafka** for streaming ingestion from live data feeds

---

## AI Enrichment (Claude)

When `--use-ai` is passed, the pipeline calls the Claude API to:

1. **Infer sector_focus** from firm name + investment thesis
2. **Classify geo_focus** from country context
3. **Extract investment stage** from descriptive text
4. **Improve investment_thesis** to a concise 120-word summary
5. **Assign confidence scores** based on data richness

The AI only infers from information already present in each record — it does not generate or hallucinate investor data.

---

## Running Tests

```bash
pytest tests/ -v
# 40 tests: schema, helpers, cleaning, dedup, validation, integration
```

---

## Ethics & Compliance

- Only public, freely accessible data is collected
- Robots.txt is respected (check before adding new scrapers)
- Polite crawl delays (1.5–4.0s between requests) are enforced
- No login bypass, no CAPTCHA solving, no paywall circumvention
- Personal data (emails) only collected from public contact pages
- GDPR/CCPA considerations: apply your jurisdiction's rules before publishing

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `requests` / `aiohttp` | Sync / async HTTP |
| `beautifulsoup4` | HTML parsing |
| `pandas` | DataFrames and CSV/Excel I/O |
| `openpyxl` | Excel formatting |
| `rapidfuzz` | Fuzzy string matching for dedup |
| `anthropic` | Claude AI enrichment |
| `playwright` | Headless browser (JS-rendered sites) |

---

## License

MIT — use freely, attribute appreciated.
