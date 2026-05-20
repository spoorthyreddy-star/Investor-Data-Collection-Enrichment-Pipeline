"""
pipeline_runner.py
───────────────────
Main orchestrator for the Investor Data Collection & Enrichment Pipeline.

Execution order:
  1. Scraping   → collect raw InvestorRecord objects from all sources
  2. Cleaning   → normalise, standardise, filter low-quality records
  3. Dedup      → remove exact and fuzzy duplicates
  4. Enrichment → rule-based + optional AI enrichment
  5. Validation → schema, format, semantic, quality scoring
  6. Export     → CSV, JSON, Excel, summary

Run:
    python pipeline_runner.py                   # full pipeline (no AI)
    python pipeline_runner.py --use-ai          # include AI enrichment
    python pipeline_runner.py --scrape-only     # scrape and clean only
    python pipeline_runner.py --export-only     # re-export from validated data
"""


from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config.settings import (
    RAW_DIR, PROCESSED_DIR, ENRICHED_DIR, VALIDATED_DIR,
    FINAL_DIR, OUTPUT_DIR, LOGS_DIR,
)
from src.utils.helpers import get_logger
from src.utils.schema import InvestorRecord, records_to_df, df_to_records

from src.scrapers.public_lists_scraper import PublicListsScraper
from src.scrapers.openvc_scraper import OpenVCScraper
from src.scrapers.vc_websites_scraper import scrape_vc_websites

from src.cleaning.cleaner import clean_records
from src.cleaning.deduplicator import deduplicate_records
from src.enrichment.ai_enricher import enrich_records
from src.validation.validator import validate_records
from src.export.exporter import export_records

logger = get_logger("pipeline.runner")


def stage_scrape() -> list[InvestorRecord]:
    logger.info("=" * 60)
    logger.info("STAGE 1: SCRAPING")
    logger.info("=" * 60)

    all_records: list[InvestorRecord] = []

    scrapers = [
        ("PublicLists (Curated + Open Data)", lambda: PublicListsScraper().scrape()),
        ("OpenVC", lambda: OpenVCScraper(save_raw=True).scrape()),
        ("VC Websites (Async)", scrape_vc_websites),
    ]

    for name, scrape_fn in scrapers:
        logger.info(f"\n── Running: {name} ──")
        try:
            t0 = time.time()
            records = scrape_fn()
            elapsed = round(time.time() - t0, 1)
            logger.info(f"  ✓ {len(records)} records in {elapsed}s")
            all_records.extend(records)
        except Exception as exc:
            logger.error(f"  ✗ {name} failed: {exc}", exc_info=True)

    logger.info(f"\nTotal raw records collected: {len(all_records)}")

    raw_path = RAW_DIR / "raw_combined.json"
    _save_records(all_records, raw_path)
    logger.info(f"Raw data saved → {raw_path}")

    return all_records


def stage_clean(records: list[InvestorRecord]) -> list[InvestorRecord]:
    logger.info("\n" + "=" * 60)
    logger.info("STAGE 2: CLEANING")
    logger.info("=" * 60)

    cleaned = clean_records(records)

    path = PROCESSED_DIR / "cleaned.json"
    _save_records(cleaned, path)
    logger.info(f"Cleaned data saved → {path}")

    return cleaned


def stage_deduplicate(records: list[InvestorRecord]) -> list[InvestorRecord]:
    logger.info("\n" + "=" * 60)
    logger.info("STAGE 3: DEDUPLICATION")
    logger.info("=" * 60)

    deduped = deduplicate_records(records)

    path = PROCESSED_DIR / "deduped.json"
    _save_records(deduped, path)
    logger.info(f"Deduped data saved → {path}")

    return deduped


def stage_enrich(records: list[InvestorRecord], use_ai: bool = False) -> list[InvestorRecord]:
    logger.info("\n" + "=" * 60)
    logger.info("STAGE 4: ENRICHMENT" + (" (+ AI)" if use_ai else " (rule-based only)"))
    logger.info("=" * 60)

    enriched = enrich_records(records, use_ai=use_ai)

    path = ENRICHED_DIR / "enriched.json"
    _save_records(enriched, path)
    logger.info(f"Enriched data saved → {path}")

    return enriched


def stage_validate(records: list[InvestorRecord]) -> tuple[list[InvestorRecord], dict]:
    logger.info("\n" + "=" * 60)
    logger.info("STAGE 5: VALIDATION")
    logger.info("=" * 60)

    valid_records, stats = validate_records(records)

    path = VALIDATED_DIR / "validated.json"
    _save_records(valid_records, path)

    stats_path = VALIDATED_DIR / "validation_stats.json"
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)

    logger.info(f"Validated data saved → {path}")
    logger.info(f"Validation stats saved → {stats_path}")

    _print_validation_report(stats)

    return valid_records, stats


def stage_export(records: list[InvestorRecord]) -> dict[str, Path]:
    logger.info("\n" + "=" * 60)
    logger.info("STAGE 6: EXPORT")
    logger.info("=" * 60)

    paths = export_records(records, output_dir=OUTPUT_DIR)

    return paths


def run_pipeline(
    use_ai: bool = False,
    scrape_only: bool = False,
    export_only: bool = False,
) -> dict:
    start_time = time.time()

    logger.info("\n" + "=" * 60)
    logger.info("INVESTOR DATA PIPELINE — STARTING")
    logger.info(f"Timestamp: {datetime.now().isoformat()}")
    logger.info("=" * 60)

    summary = {"started_at": datetime.now().isoformat()}

    if export_only:
        validated_path = VALIDATED_DIR / "validated.json"

        if not validated_path.exists():
            logger.error("No validated.json found. Run full pipeline first.")
            return {}

        records = _load_records(validated_path)
        logger.info(f"Loaded {len(records)} records from validated cache")

        paths = stage_export(records)
        summary["output_paths"] = {k: str(v) for k, v in paths.items()}

        return summary

    raw = stage_scrape()

    scraped_count = len(raw)
    summary["scraped_records"] = scraped_count

    curated_path = RAW_DIR / "curated_2500_investors.json"

    if curated_path.exists():
        curated_records = _load_records(curated_path)
        raw.extend(curated_records)
        logger.info(f"Added {len(curated_records)} curated investor records from {curated_path}")
    else:
        logger.warning(f"No curated dataset found at {curated_path}")

    summary["raw_records"] = len(raw)

    combined_raw_path = RAW_DIR / "raw_combined_with_curated.json"
    _save_records(raw, combined_raw_path)
    logger.info(f"Combined raw data saved → {combined_raw_path}")

    cleaned = stage_clean(raw)
    summary["cleaned_records"] = len(cleaned)

    deduped = stage_deduplicate(cleaned)
    summary["deduped_records"] = len(deduped)

    if scrape_only:
        logger.info("\nScrape-only mode — stopping after dedup.")
        summary["mode"] = "scrape_only"
        return summary

    enriched = stage_enrich(deduped, use_ai=use_ai)
    summary["enriched_records"] = len(enriched)

    valid, stats = stage_validate(enriched)
    summary["valid_records"] = len(valid)
    summary["validation_stats"] = stats

    # Fill empty values
    for r in valid:
        data = r.to_dict()

        for key, value in data.items():
            if value in [None, "", [], {}]:
                setattr(r, key, "Not Available")

    paths = stage_export(valid)
    summary["output_paths"] = {k: str(v) for k, v in paths.items()}

    elapsed = round(time.time() - start_time, 1)

    summary["elapsed_seconds"] = elapsed
    summary["completed_at"] = datetime.now().isoformat()

    logger.info("\n" + "=" * 60)
    logger.info("PIPELINE COMPLETE")
    logger.info(f"  Scraped records: {summary.get('scraped_records', 0)}")
    logger.info(f"  Raw combined:    {summary.get('raw_records', 0)}")
    logger.info(f"  After cleaning:  {summary.get('cleaned_records', 0)}")
    logger.info(f"  After dedup:     {summary.get('deduped_records', 0)}")
    logger.info(f"  After enrich:    {summary.get('enriched_records', 0)}")
    logger.info(f"  Valid & exported:{summary.get('valid_records', 0)}")
    logger.info(f"  Elapsed: {elapsed}s")
    logger.info("=" * 60)

    summary_path = OUTPUT_DIR / "run_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    logger.info(f"Run summary → {summary_path}")

    return summary


def _save_records(records: list[InvestorRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    data = [r.to_dict() for r in records]

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def _load_records(path: Path) -> list[InvestorRecord]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    return [InvestorRecord.from_dict(d) for d in data]


def _print_validation_report(stats: dict) -> None:
    logger.info("\n── Validation Report ──")
    logger.info(f"  Total:    {stats.get('total', 0)}")
    logger.info(f"  Valid:    {stats.get('valid', 0)}")
    logger.info(f"  Invalid:  {stats.get('invalid', 0)}")
    logger.info(f"  Warnings: {stats.get('warnings_only', 0)}")
    logger.info(f"  Avg Quality Score: {stats.get('avg_quality_score', 0):.3f}")

    coverage = stats.get("field_coverage", {})

    if coverage:
        logger.info("\n── Field Coverage ──")
        for field, pct in sorted(coverage.items(), key=lambda x: -x[1]):
            bar = "█" * int(pct * 20) + "░" * (20 - int(pct * 20))
            logger.info(f"  {field:<30} {bar} {pct*100:.0f}%")


def main():
    parser = argparse.ArgumentParser(
        description="Investor Data Collection & Enrichment Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--use-ai",
        action="store_true",
        help="Enable Claude AI-assisted enrichment",
    )

    parser.add_argument(
        "--scrape-only",
        action="store_true",
        help="Run scraping, cleaning, and dedup stages only",
    )

    parser.add_argument(
        "--export-only",
        action="store_true",
        help="Skip scraping; re-export from existing validated data",
    )

    args = parser.parse_args()

    summary = run_pipeline(
        use_ai=args.use_ai,
        scrape_only=args.scrape_only,
        export_only=args.export_only,
    )

    if summary.get("output_paths"):
        print("\n✅ Output files:")
        for fmt, path in summary["output_paths"].items():
            print(f"   {fmt:8s}: {path}")


if __name__ == "__main__":
    main()