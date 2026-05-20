"""
src/validation/validator.py
────────────────────────────
Data validation pipeline.

Applies a multi-layer validation strategy:
  Layer 1 — Schema validation: mandatory fields present and typed correctly
  Layer 2 — Format validation: URLs parseable, emails valid, dates ISO-8601
  Layer 3 — Semantic validation: values within known taxonomies
  Layer 4 — Completeness scoring: weighted score per record
  Layer 5 — Flagging: attach validation warnings to notes field

Records that PASS all layers get is_valid=True.
Records that FAIL mandatory fields are dropped.
Records with warnings are kept with flags in notes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import (
    MANDATORY_FIELDS, INVESTOR_TYPES, SECTORS, GEO_REGIONS, STAGES,
)
from src.utils.helpers import get_logger
from src.utils.schema import InvestorRecord

logger = get_logger("validation.validator")

# ── Regex patterns ─────────────────────────────────────────────────────────
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
_DATE_RE  = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_URL_RE   = re.compile(r"^https?://[^\s/$.?#].[^\s]*$")


# ─────────────────────────────────────────────────────────────────────────────
# ValidationResult
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    is_valid:     bool           = True
    errors:       list[str]      = field(default_factory=list)
    warnings:     list[str]      = field(default_factory=list)
    quality_score: float         = 0.0    # 0.0 – 1.0

    def add_error(self, msg: str):
        self.errors.append(msg)
        self.is_valid = False

    def add_warning(self, msg: str):
        self.warnings.append(msg)

    def summary(self) -> str:
        parts = [f"valid={self.is_valid}", f"score={self.quality_score:.2f}"]
        if self.errors:
            parts.append(f"errors={len(self.errors)}")
        if self.warnings:
            parts.append(f"warnings={len(self.warnings)}")
        return ", ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Validator
# ─────────────────────────────────────────────────────────────────────────────

class DataValidator:

    def validate(self, records: list[InvestorRecord]) -> tuple[list[InvestorRecord], dict]:
        """
        Validate all records. Returns:
            - list of valid (passing) InvestorRecord objects
            - summary statistics dict
        """
        logger.info(f"Validating {len(records)} records …")
        valid_records = []
        stats = {
            "total": len(records),
            "valid": 0,
            "invalid": 0,
            "warnings_only": 0,
            "avg_quality_score": 0.0,
            "field_coverage": {},
        }
        scores = []

        for rec in records:
            result = self._validate_record(rec)
            rec.confidence_score = result.quality_score

            if result.warnings:
                warning_note = " | ".join(result.warnings)
                rec.notes = f"{rec.notes or ''} [WARNINGS: {warning_note}]".strip(" |")

            if result.is_valid:
                valid_records.append(rec)
                stats["valid"] += 1
                if result.warnings:
                    stats["warnings_only"] += 1
            else:
                stats["invalid"] += 1
                logger.debug(f"  INVALID [{rec.investor_name}]: {result.errors}")

            scores.append(result.quality_score)

        # Compute aggregate stats
        if scores:
            stats["avg_quality_score"] = round(sum(scores) / len(scores), 3)

        # Field coverage
        all_fields = MANDATORY_FIELDS + [
            "city_region", "investment_thesis", "investment_stage",
            "ticket_size", "portfolio_companies", "key_partners",
            "contact_email", "linkedin_url",
        ]
        for f_name in all_fields:
            filled = sum(
                1 for r in valid_records
                if getattr(r, f_name, None) not in (None, "")
            )
            stats["field_coverage"][f_name] = (
                round(filled / max(len(valid_records), 1), 3)
            )

        logger.info(
            f"Validation done: {stats['valid']} valid, {stats['invalid']} invalid, "
            f"avg_quality={stats['avg_quality_score']:.3f}"
        )
        return valid_records, stats

    # ── Per-record validation ─────────────────────────────────────────────────

    def _validate_record(self, rec: InvestorRecord) -> ValidationResult:
        result = ValidationResult()

        # Layer 1: Mandatory fields
        for f_name in MANDATORY_FIELDS:
            val = getattr(rec, f_name, None)
            if not val or (isinstance(val, str) and not val.strip()):
                result.add_error(f"Missing mandatory field: {f_name}")

        if not result.is_valid:
            result.quality_score = 0.0
            return result

        # Layer 2: Format validation
        self._validate_formats(rec, result)

        # Layer 3: Semantic validation
        self._validate_semantics(rec, result)

        # Layer 4: Quality score
        result.quality_score = self._compute_quality(rec)

        return result

    def _validate_formats(self, rec: InvestorRecord, result: ValidationResult):
        """Check URL, email, date formats."""
        # Website URL
        if rec.website_url and not _URL_RE.match(rec.website_url):
            result.add_warning(f"Malformed website_url: {rec.website_url[:60]}")

        # Source URL
        if rec.source_url and not _URL_RE.match(rec.source_url):
            result.add_warning(f"Malformed source_url: {rec.source_url[:60]}")

        # Contact email
        if rec.contact_email and not _EMAIL_RE.match(rec.contact_email):
            result.add_warning(f"Malformed email: {rec.contact_email}")

        # Date collected
        if rec.date_collected and not _DATE_RE.match(rec.date_collected):
            result.add_warning(f"Bad date format: {rec.date_collected}")

        # Investor name length sanity check
        if rec.investor_name and len(rec.investor_name) < 2:
            result.add_error("investor_name too short")
        if rec.investor_name and len(rec.investor_name) > 200:
            result.add_warning("investor_name suspiciously long")

    def _validate_semantics(self, rec: InvestorRecord, result: ValidationResult):
        """Check values are within known taxonomies."""
        if rec.investor_type and rec.investor_type not in INVESTOR_TYPES:
            result.add_warning(
                f"Unknown investor_type: '{rec.investor_type}' — "
                f"expected one of {INVESTOR_TYPES}"
            )

        if rec.geo_focus and rec.geo_focus not in GEO_REGIONS:
            result.add_warning(f"Unknown geo_focus: '{rec.geo_focus}'")

        # Multi-value sector field
        if rec.sector_focus:
            for s in [p.strip() for p in rec.sector_focus.split(",")]:
                if s and s not in SECTORS:
                    result.add_warning(f"Unknown sector: '{s}'")

    def _compute_quality(self, rec: InvestorRecord) -> float:
        """
        Weighted quality score (0-1).

        Mandatory fields:    weight 2 each
        Nice-to-have fields: weight 1 each
        URL / format valid:  bonus 0.05 each
        """
        score = 0.0
        max_score = 0.0

        # Mandatory fields (weight 2)
        for f_name in MANDATORY_FIELDS:
            max_score += 2
            val = getattr(rec, f_name, None)
            if val and str(val).strip():
                score += 2

        # Nice-to-have fields (weight 1)
        nice_fields = [
            "city_region", "investment_thesis", "investment_stage",
            "ticket_size", "portfolio_companies", "key_partners",
            "contact_email", "linkedin_url",
        ]
        for f_name in nice_fields:
            max_score += 1
            val = getattr(rec, f_name, None)
            if val and str(val).strip():
                score += 1

        # URL bonus
        if rec.website_url and _URL_RE.match(rec.website_url):
            score += 0.3; max_score += 0.3

        return round(min(score / max_score, 1.0), 4) if max_score > 0 else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Convenience function
# ─────────────────────────────────────────────────────────────────────────────

def validate_records(
    records: list[InvestorRecord],
) -> tuple[list[InvestorRecord], dict]:
    return DataValidator().validate(records)
