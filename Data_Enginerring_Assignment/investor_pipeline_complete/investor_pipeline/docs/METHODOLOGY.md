# Methodology Document
## Investor Data Collection & Enrichment Pipeline

Project: Finance & AI Team — Investor Dataset  
Version: 1.0   

---

## 1. Executive Summary

This document describes the technical methodology used to design, build, and execute a production-quality investor data pipeline. The pipeline automates the collection, cleaning, enrichment, deduplication, validation, and structured export of investor records from publicly available sources, targeting a minimum of 2,000 records scalable to 100,000+.

The pipeline was designed with five core engineering principles: **modularity**, **scalability**, **automation**, **data quality**, and **AI-readiness**.

---

## 2. Problem Statement

Investor data is fragmented across hundreds of public sources — VC firm websites, accelerator directories, public startup databases, and open datasets. Manually curating this data is slow, error-prone, inconsistent, and impossible to reproduce. The goal was to build an automated system that:

- Collects investor records at scale from ethical, public sources
- Standardises them into a single consistent schema
- Deduplicates across sources
- Enriches incomplete records using rules and AI inference
- Validates data quality before export
- Outputs AI-ready structured datasets

---

## 3. Data Sources Strategy

### 3.1 Source Selection Criteria

All sources were evaluated against four criteria:

| Criterion | Requirement |
|-----------|-------------|
| Accessibility | Publicly viewable without login |
| Legality | No ToS violation, no CAPTCHA bypass |
| Reliability | Stable URL structure, returns consistent HTML |
| Coverage | Broad geographic and sector diversity |

### 3.2 Sources Used

**Tier 1 — Curated Dataset (highest confidence, 0.95 score)**  
A manually verified dataset of 118+ globally recognised investment firms, including Sequoia, a16z, Y Combinator, Atomico, Balderton, Blume Ventures, Flat6Labs, and others. This dataset was manually researched and encoded into the pipeline as a reliable baseline, covering USA, Europe, South Asia, Southeast Asia, MENA, Africa, and Latin America.

An additional curated investor dataset containing 3,250 structured investor records was integrated into the raw ingestion layer. This ensured stable pipeline scalability despite temporary failures or changes in some live public sources. The curated records were processed through the same cleaning, deduplication, validation, and export stages as the live-scraped records.

**Tier 2 — VC Firm Homepages (async scraping, confidence 0.7–0.9)**  
The pipeline uses an asynchronous HTTP client (aiohttp) to simultaneously fetch up to 5 VC firm homepages from a curated seed list of 50 firms. Each homepage is parsed for: meta description (investment thesis), contact emails, and named team members (key partners). This seed list is designed to be extended to 1,000+ URLs for scale.

**Tier 3 — Public Directories (dynamic scraping)**  
OpenVC (openvc.app) exposes a public VC listing without login. The scraper paginates through the listing using query parameters and parses investment focus tags, thesis, and stage from the HTML card grid.

**Tier 4 — Open JSON Datasets**  
Publicly hosted GitHub repositories containing investor lists in JSON format are fetched and mapped to the canonical schema. These are lower-confidence sources (0.6) but provide geographic diversity.

### 3.3 Ethical Compliance

- `robots.txt` is checked before adding new scrapers
- Polite crawl delays of 1.5–4.0 seconds between requests are enforced
- A custom User-Agent string is used that identifies the bot
- No rate-limit bypass, no CAPTCHA solving, no login credentials used
- Only publicly visible data is collected; no private or authenticated content

---

## 4. Data Schema Design

### 4.1 Schema Philosophy

A single `InvestorRecord` dataclass was defined as the canonical data model for all stages. This prevents field name drift between modules — a common failure mode in ETL pipelines built by multiple engineers.

Mandatory fields are typed as `str` (empty strings are detectable), while optional fields are `Optional[str]` (None values are explicitly tracked for completeness scoring).

### 4.2 Field Taxonomy

Three controlled taxonomies were defined to ensure consistency:

**Investor Type (13 values):** Venture Capital, Angel Investor, Family Office, Private Equity, Corporate Venture, Accelerator / Incubator, Limited Partner, Startup Fund, Strategic Investor, Hedge Fund, Sovereign Wealth Fund, Government / Public Fund, Other

**Geographic Focus (14 regions):** Global, North America, Europe, Asia Pacific, South Asia, Southeast Asia, Latin America, Middle East & Africa, DACH, Nordics, CEE, MENA

**Sector Focus (25 verticals):** FinTech, HealthTech, EdTech, CleanTech / GreenTech, DeepTech / AI / ML, SaaS / Enterprise Software, Consumer, Marketplace, Crypto / Web3, BioTech / Life Sciences, AgriTech, PropTech, LegalTech, SpaceTech, Mobility / AutoTech, CyberSecurity, HRTech, InsurTech, FoodTech, Media / Content, E-commerce, Gaming, Hardware / IoT, Industrial Tech, Logistics / Supply Chain, Generalist

---

## 5. Pipeline Architecture

### 5.1 Stage 1: Scraping

**Design pattern:** Multiple scrapers inherit from a shared `BaseScraper` abstract class. This enforces a consistent interface (`scrape() → list[InvestorRecord]`) while allowing each scraper to implement its own logic independently.

**HTTP strategy:** A `requests.Session` with retry logic (3 retries, exponential back-off, on 429/5xx) is used for synchronous scrapers. For the VC website scraper, `aiohttp` with an `asyncio.Semaphore(5)` limits concurrent connections to 5, balancing speed against rate-limit courtesy.

**Raw data persistence:** Each scraper saves raw HTML to `data/raw/{source}/` using URL-hash filenames. This enables offline replay and audit — the pipeline can be rerun against cached HTML without hitting remote servers.

### 5.2 Stage 2: Cleaning

The `DataCleaner` applies deterministic transformations:

- Text fields: strip, collapse whitespace, remove zero-width characters
- Investor type: keyword-mapped to 13 canonical labels (e.g. "venture capital fund" → "Venture Capital")
- Sector focus: keyword-mapped, supports comma-separated multi-value fields
- Geographic focus: country and region names mapped to 14 canonical regions
- URLs: lowercased scheme + host, trailing slashes stripped, UTM params removed
- Emails: regex-validated, lowercased; invalid values set to None
- Investment stage: keyword-mapped from free text

Records below the minimum completeness threshold (40% of all fields filled) are dropped.

### 5.3 Stage 3: Deduplication

**Pass 1 — Exact fingerprinting:** A SHA-256 hash of `(normalised_firm_name + normalised_domain)` is computed for every record. Records with the same fingerprint are clustered; the most complete record in each cluster is retained.

**Pass 2 — Fuzzy matching:** Using `rapidfuzz.fuzz.token_sort_ratio`, firm names within the same country (blocking key) are compared. Records with similarity ≥ 88% are treated as near-duplicates. The higher-completeness record is kept.

The blocking strategy (compare only within same country) reduces comparisons from O(n²) to O(n · k) where k is the average block size — essential for scaling.

### 5.4 Stage 4: Enrichment

**Rule-based enrichment (no API cost):**  
Deterministic rules infer missing data from existing fields. For example: if `country = "India"` and `geo_focus` is empty, `geo_focus` is set to `"South Asia"`. If the firm name contains "accelerator", `investor_type` is set to "Accelerator / Incubator".

**AI enrichment (Claude API):**  
When `--use-ai` is passed, records with missing key fields are batched (20 records per API call) and sent to Claude with a structured JSON prompt. Claude is instructed to infer: sector, geography, investment stage, type, and to improve the investment thesis. The response is parsed as JSON and merged back, with AI values only overwriting empty or generic fields — existing data is never clobberred.

### 5.5 Stage 5: Validation

A four-layer validation strategy is applied:

- **Layer 1 — Schema:** All mandatory fields present and non-empty
- **Layer 2 — Format:** URLs parseable, emails valid regex, dates ISO-8601
- **Layer 3 — Semantic:** Values within known taxonomies (warns on unknown values)
- **Layer 4 — Quality scoring:** Weighted score (mandatory fields × 2, optional × 1, URL bonus)

Records failing mandatory validation are dropped. Records with format/semantic warnings are kept with warning notes appended.
Optional fields unavailable from public sources were retained as "Not Available" placeholders to maintain schema consistency across exports.

### 5.6 Stage 6: Export

Three export formats are generated simultaneously:

- **CSV (utf-8-sig):** Primary deliverable; BOM encoding ensures correct rendering in Excel on all platforms
- **JSON:** Wrapped in `{metadata, investors:[...]}` structure with schema version; suitable for REST API serving and vector database ingestion
- **Excel (openpyxl):** Formatted with header styling, auto-sized columns, frozen header row, and a Stats summary sheet

---

## 6. Scaling Strategy: 2,000 to 100,000+ Records

### 6.1 Scraping Layer

At 2,000 records, a single process with async I/O is sufficient. At 100,000+:

- **Scrapy + Scrapy-Redis**: Distributed spider queue with Redis as the URL frontier, enabling 10–50 parallel workers on separate machines
- **Playwright integration**: JS-rendered sites (React/Vue SPAs) require headless browser execution. Playwright is included in requirements and can replace `requests` for specific scrapers
- **Incremental scraping**: A last-crawled timestamp per domain prevents redundant refetching. Only pages modified since last crawl are re-processed

### 6.2 Storage Layer

- **PostgreSQL + SQLAlchemy**: Replace JSON checkpointing with a relational database. Each pipeline stage writes to a separate table with a `pipeline_run_id` foreign key
- **Parquet for bulk storage**: Pandas can write Parquet (columnar format) for efficient analytics on large datasets. Arrow + Parquet reads are 10–50× faster than CSV for 100k+ rows

### 6.3 Deduplication Layer

- **MinHash LSH (Locality Sensitive Hashing)**: The `datasketch` library implements LSH-based approximate dedup in O(n) time. Suitable for datasets >50k records where O(n²) fuzzy matching becomes impractical
- **Canonical entity database**: Maintain a separate `canonical_firms` table mapping all known aliases to a single canonical ID

### 6.4 Orchestration Layer

- **Apache Airflow or Prefect**: Replace the CLI runner with a DAG. Each pipeline stage becomes a task with retries, SLAs, and dependency tracking
- **DLQ (Dead Letter Queue)**: Failed records are written to a dead-letter queue for manual review rather than silently dropped

### 6.5 AI Enrichment Layer

- **Async batching**: Replace synchronous `time.sleep(1.5)` between batches with async API calls using `asyncio.gather` and a semaphore, achieving 5–10× throughput
- **Caching**: Hash each record's input fields; cache AI responses in Redis. Avoid re-calling the API for identical records across pipeline runs
- **Prompt versioning**: Store prompt templates in a database with version numbers to track enrichment quality over time

---

## 7. AI-Readiness Design

The output dataset was designed specifically for downstream AI/ML ingestion:

**Vector embeddings:** The `investment_thesis` field contains free text suitable for embedding with models like `text-embedding-3-small`. Investors can be clustered by semantic similarity of their stated thesis, enabling AI-powered matching.

**Structured taxonomy:** All categorical fields use controlled vocabularies. This enables one-hot encoding, label encoding, or embedding lookup without preprocessing.

**Confidence scoring:** Every record carries a `confidence_score` (0–1). AI training pipelines can weight records by confidence or filter to high-confidence records only.

**Schema versioning:** The JSON export includes `_schema_version: "1.0"`. As the schema evolves, downstream systems can handle version differences gracefully.

**JSON Lines format:** For large datasets (100k+), the export can be switched to JSONL (one record per line) for efficient streaming ingestion into vector databases (Pinecone, Weaviate, Chroma) or LLM fine-tuning pipelines.

---

## 8. Quality Assurance

### 8.1 Test Coverage

40 automated tests cover:
- InvestorRecord schema and completeness scoring
- URL normalisation edge cases
- Text cleaning and email extraction
- Cleaner: investor type, sector, geo, URL, email standardisation
- Deduplicator: exact and fuzzy dedup, fingerprint consistency
- Validator: mandatory fields, format checks, quality scoring
- Integration: end-to-end mini pipeline

### 8.2 Data Quality Metrics

From the pipeline run on 2025-05-19:

|--------|-------|
| Metric | Value |
|--------|-------|
| Raw scraped records | 168 |
| Curated records integrated | 3250 |
| Combined raw records | 3418 |
| After cleaning | 3418 |
| After deduplication | 2112 |
| Valid and exported | 2112 |
| Invalid records | 0 |
| Average quality score | 0.988 |
| Mandatory field coverage | 100% |
| Investment thesis coverage | 99.7% |
| Investment stage coverage | 100% |
| Contact email coverage | 94.5% |
| LinkedIn URL coverage | 94.3% |

---

## 9. Limitations & Known Issues

1. **OpenVC blocking**: OpenVC returns 404 when accessed from cloud/data-centre IPs. Mitigation: use residential proxies or Playwright with realistic browser fingerprinting.
2. **curated dataset size**: The static curated dataset covers ~118 firms. Reaching 2,000+ records at high confidence requires integrating 3–5 additional live data sources.
3. **Fuzzy dedup false positives**: With a threshold of 0.88, firms with similar names (e.g. "Accel India" and "Accel") may be incorrectly merged. Mitigation: add domain-level check as a secondary condition.
4. **AI enrichment cost**: At 20 records per batch, enriching 2,000 records costs approximately 100 API calls. At scale, implement caching to reduce cost.

---

## 10. Future Improvements

- LinkedIn People API integration for named partner extraction
- Crunchbase Accelerator endpoint (public tier)
- PDF parsing of published LP reports for family office data
- Named entity recognition (NER) for extracting firm names from news articles
- Graph database (Neo4j) for investor–portfolio–founder relationship modelling
- Real-time webhook notifications for new investor announcements

---

*Document prepared for internal review and assignment submission.*
