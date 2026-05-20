"""
src/enrichment/ai_enricher.py
──────────────────────────────
AI-assisted enrichment using the Claude API (claude-sonnet-4-20250514).

For each batch of records, this module asks Claude to:
  1. Infer missing sector_focus from firm name + thesis
  2. Classify geo_focus if country is known but region is missing
  3. Extract investment stage from description
  4. Assign a data confidence score
  5. Infer investor_type if ambiguous
  6. Improve / summarise investment_thesis

Claude is used ONLY for inference from already-public data —
NOT for generating fabricated investor information.

The module uses a structured JSON prompt so that Claude returns
machine-parseable responses that can be merged back into records.

Usage:
    from src.enrichment.ai_enricher import AIEnricher
    enricher = AIEnricher()
    records = enricher.enrich(records)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import (
    AI_BATCH_SIZE, AI_MAX_TOKENS, ANTHROPIC_MODEL,
    SECTORS, STAGES, GEO_REGIONS, INVESTOR_TYPES,
)
from src.utils.helpers import get_logger
from src.utils.schema import InvestorRecord

logger = get_logger("enrichment.ai_enricher")


# ─────────────────────────────────────────────────────────────────────────────
# Prompt templates
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a senior investment data analyst.
Your task is to enrich incomplete investor records using only the information provided.
Do NOT fabricate data. If you cannot infer a value from the given information, return null.
Always respond with valid JSON only — no markdown, no explanation."""

ENRICHMENT_PROMPT_TEMPLATE = """
Enrich the following investor records. For each record, infer or improve these fields:
- sector_focus: pick 1-3 values from: {sectors}
- geo_focus: pick from: {geo_regions}
- investment_stage: pick from: {stages}
- investor_type: pick from: {investor_types}
- confidence_score: float 0-1 based on data completeness
- investment_thesis: improve or summarise in ≤120 words if possible

Return a JSON array with one object per input record, using these keys:
  id, sector_focus, geo_focus, investment_stage, investor_type, confidence_score, investment_thesis

Records:
{records_json}
"""


# ─────────────────────────────────────────────────────────────────────────────
# AI Enricher
# ─────────────────────────────────────────────────────────────────────────────

class AIEnricher:
    """
    Enriches investor records using the Claude API.
    Processes records in batches to stay within context window limits.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._client = None

    def _get_client(self):
        """Lazy-load the Anthropic client."""
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except ImportError:
                raise RuntimeError(
                    "anthropic package not installed. Run: pip install anthropic"
                )
        return self._client

    def enrich(self, records: list[InvestorRecord],
               only_incomplete: bool = True) -> list[InvestorRecord]:
        """
        Enrich a list of InvestorRecord objects using Claude.

        Args:
            records: List of records to enrich.
            only_incomplete: If True, only send records missing key fields
                             to save API tokens.

        Returns:
            Records with enriched fields merged in.
        """
        if not self.api_key:
            logger.warning("No ANTHROPIC_API_KEY set — skipping AI enrichment.")
            return records

        if only_incomplete:
            to_enrich = [r for r in records if self._needs_enrichment(r)]
            to_keep   = [r for r in records if not self._needs_enrichment(r)]
        else:
            to_enrich = records
            to_keep   = []

        logger.info(f"AI enrichment: {len(to_enrich)} records to enrich, "
                    f"{len(to_keep)} already complete")

        enriched = []
        for i in range(0, len(to_enrich), AI_BATCH_SIZE):
            batch = to_enrich[i : i + AI_BATCH_SIZE]
            logger.info(f"  Enriching batch {i // AI_BATCH_SIZE + 1} "
                        f"({len(batch)} records) …")
            try:
                batch = self._enrich_batch(batch)
            except Exception as exc:
                logger.error(f"  Batch enrichment failed: {exc}")
            enriched.extend(batch)
            time.sleep(1.5)   # be polite to the API

        return to_keep + enriched

    # ── Private methods ───────────────────────────────────────────────────────

    def _needs_enrichment(self, rec: InvestorRecord) -> bool:
        """Return True if any key field is missing."""
        return any([
            not rec.sector_focus or rec.sector_focus in ("", "Generalist"),
            not rec.geo_focus or rec.geo_focus in ("", "Global"),
            not rec.investment_stage,
            not rec.investment_thesis,
            not rec.investor_type or rec.investor_type == "Other",
        ])

    def _enrich_batch(self, batch: list[InvestorRecord]) -> list[InvestorRecord]:
        """Send one batch to Claude and merge the enriched data back."""
        payload = [
            {
                "id": i,
                "investor_name":    rec.investor_name,
                "firm_name":        rec.firm_name,
                "investor_type":    rec.investor_type,
                "country":          rec.country,
                "sector_focus":     rec.sector_focus,
                "geo_focus":        rec.geo_focus,
                "investment_stage": rec.investment_stage,
                "investment_thesis": rec.investment_thesis,
                "website_url":      rec.website_url,
            }
            for i, rec in enumerate(batch)
        ]

        prompt = ENRICHMENT_PROMPT_TEMPLATE.format(
            sectors        = ", ".join(SECTORS),
            geo_regions    = ", ".join(GEO_REGIONS),
            stages         = ", ".join(STAGES),
            investor_types = ", ".join(INVESTOR_TYPES),
            records_json   = json.dumps(payload, ensure_ascii=False, indent=2),
        )

        client = self._get_client()
        message = client.messages.create(
            model      = ANTHROPIC_MODEL,
            max_tokens = AI_MAX_TOKENS,
            system     = SYSTEM_PROMPT,
            messages   = [{"role": "user", "content": prompt}],
        )

        raw_text = message.content[0].text.strip()

        # Strip markdown fences if present
        if raw_text.startswith("```"):
            raw_text = "\n".join(raw_text.split("\n")[1:])
            raw_text = raw_text.rstrip("`").strip()

        try:
            enrichment_data: list[dict] = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            logger.error(f"JSON parse error in AI response: {exc}\nRaw: {raw_text[:300]}")
            return batch

        # Merge enriched fields back
        enrichment_map = {item["id"]: item for item in enrichment_data
                          if isinstance(item, dict)}

        for i, rec in enumerate(batch):
            data = enrichment_map.get(i)
            if not data:
                continue
            rec = self._merge(rec, data)
            batch[i] = rec

        return batch

    def _merge(self, rec: InvestorRecord, data: dict) -> InvestorRecord:
        """
        Merge AI-generated enrichment into a record.
        Only overwrites empty / generic fields — never clobbers existing data.
        """
        def _overwrite_if_empty(current: Optional[str], new: Optional[str]) -> Optional[str]:
            if new and current in (None, "", "Generalist", "Global", "Other"):
                return str(new)[:300]
            return current

        rec.sector_focus     = _overwrite_if_empty(rec.sector_focus,     data.get("sector_focus"))
        rec.geo_focus        = _overwrite_if_empty(rec.geo_focus,         data.get("geo_focus"))
        rec.investment_stage = _overwrite_if_empty(rec.investment_stage,  data.get("investment_stage"))
        rec.investor_type    = _overwrite_if_empty(rec.investor_type,     data.get("investor_type"))
        rec.investment_thesis = _overwrite_if_empty(rec.investment_thesis, data.get("investment_thesis"))

        # Confidence score: take the higher of existing and AI-estimated
        ai_score = data.get("confidence_score")
        if isinstance(ai_score, (int, float)):
            rec.confidence_score = max(
                rec.confidence_score or 0.0,
                float(ai_score)
            )

        return rec


# ─────────────────────────────────────────────────────────────────────────────
# Rule-based enricher (no API needed — a fast first pass)
# ─────────────────────────────────────────────────────────────────────────────

class RuleBasedEnricher:
    """
    Applies deterministic enrichment rules before calling the AI enricher.
    This reduces AI API calls by filling gaps that can be inferred from
    existing data without machine learning.
    """

    def enrich(self, records: list[InvestorRecord]) -> list[InvestorRecord]:
        logger.info(f"Rule-based enrichment on {len(records)} records …")
        enriched_count = 0
        for rec in records:
            changed = self._enrich_record(rec)
            if changed:
                enriched_count += 1
        logger.info(f"  Rule-based enrichment updated {enriched_count} records")
        return records

    def _enrich_record(self, rec: InvestorRecord) -> bool:
        changed = False

        # Infer country from geo_focus
        if not rec.country and rec.geo_focus:
            country = self._geo_to_country(rec.geo_focus)
            if country:
                rec.country = country
                changed = True

        # Infer geo_focus from country
        if not rec.geo_focus and rec.country:
            from src.scrapers.vc_websites_scraper import _infer_geo
            rec.geo_focus = _infer_geo(rec.country)
            changed = True

        # Default investor_type from name keywords
        if not rec.investor_type or rec.investor_type == "Other":
            inferred = self._infer_type_from_name(rec.investor_name or rec.firm_name)
            if inferred:
                rec.investor_type = inferred
                changed = True

        # Default date_collected
        if not rec.date_collected:
            from datetime import date
            rec.date_collected = date.today().isoformat()
            changed = True

        # Build notes about confidence
        if rec.confidence_score and rec.confidence_score < 0.5 and not rec.notes:
            rec.notes = "Low confidence — verify manually"
            changed = True

        return changed

    def _geo_to_country(self, geo: str) -> Optional[str]:
        MAP = {
            "North America": "USA",
            "Europe":        "Europe (various)",
            "South Asia":    "India",
            "Southeast Asia": "Singapore",
            "Asia Pacific":  "Asia (various)",
            "MENA":          "UAE",
            "Latin America": "Brazil",
            "Middle East & Africa": "Africa (various)",
        }
        return MAP.get(geo)

    def _infer_type_from_name(self, name: str) -> Optional[str]:
        if not name:
            return None
        n = name.lower()
        if any(k in n for k in ["accelerator", "incubator", "techstars", "ycombinator", "y combinator", "500 "]):
            return "Accelerator / Incubator"
        if any(k in n for k in ["angel", "angels"]):
            return "Angel Investor"
        if any(k in n for k in ["family office", "family capital"]):
            return "Family Office"
        if any(k in n for k in ["private equity", " pe ", " buyout"]):
            return "Private Equity"
        if any(k in n for k in ["sovereign", "national fund", "government fund"]):
            return "Sovereign Wealth Fund"
        if any(k in n for k in ["corporate venture", "innovation fund", " cvc"]):
            return "Corporate Venture"
        return None


def enrich_records(records: list[InvestorRecord],
                   use_ai: bool = False) -> list[InvestorRecord]:
    """
    Convenience function: run rule-based enrichment first,
    then optionally call the AI enricher.
    """
    records = RuleBasedEnricher().enrich(records)
    if use_ai:
        records = AIEnricher().enrich(records)
    return records
