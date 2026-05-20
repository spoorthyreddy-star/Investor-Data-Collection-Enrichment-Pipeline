"""
src/utils/schema.py
───────────────────
Single source of truth for the InvestorRecord dataclass.
Every pipeline stage reads and writes this structure, ensuring
column names never drift between modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional
import pandas as pd

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import MANDATORY_FIELDS, ALL_FIELDS


@dataclass
class InvestorRecord:
    """
    Canonical data model for a single investor record.

    Mandatory fields are non-optional str; nice-to-have fields are Optional[str].
    This makes completeness scoring trivial: count non-None values / total fields.
    """
    # ── Mandatory ──────────────────────────────────────────────────────────
    investor_name:  str = ""
    investor_type:  str = ""
    firm_name:      str = ""
    website_url:    str = ""
    country:        str = ""
    geo_focus:      str = ""
    sector_focus:   str = ""
    source_url:     str = ""
    data_source:    str = ""
    date_collected: str = field(default_factory=lambda: date.today().isoformat())

    # ── Nice-to-have ───────────────────────────────────────────────────────
    city_region:          Optional[str] = None
    investment_thesis:    Optional[str] = None
    investment_stage:     Optional[str] = None
    ticket_size:          Optional[str] = None
    portfolio_companies:  Optional[str] = None
    key_partners:         Optional[str] = None
    contact_email:        Optional[str] = None
    contact_phone:        Optional[str] = None
    linkedin_url:         Optional[str] = None
    confidence_score:     Optional[float] = None
    notes:                Optional[str] = None

    # ── Internal / pipeline metadata ───────────────────────────────────────
    record_id:   Optional[str] = None   # SHA-256 fingerprint assigned during dedup
    is_duplicate: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def completeness(self) -> float:
        """
        Return fraction of ALL_FIELDS that are non-empty.
        Used for data-quality scoring and filtering.
        """
        d = self.to_dict()
        filled = sum(1 for f in ALL_FIELDS if d.get(f) not in (None, "", []))
        return round(filled / len(ALL_FIELDS), 4)

    def mandatory_completeness(self) -> float:
        """Fraction of MANDATORY fields that are non-empty."""
        d = self.to_dict()
        filled = sum(1 for f in MANDATORY_FIELDS if d.get(f) not in (None, "", []))
        return round(filled / len(MANDATORY_FIELDS), 4)

    @classmethod
    def from_dict(cls, d: dict) -> "InvestorRecord":
        """Build from a raw dict; unknown keys are silently ignored."""
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# ─────────────────────────────────────────────────────────────────────────────
# DataFrame ↔ list[InvestorRecord] helpers
# ─────────────────────────────────────────────────────────────────────────────

def records_to_df(records: list[InvestorRecord]) -> pd.DataFrame:
    """Convert a list of InvestorRecord objects to a pandas DataFrame."""
    return pd.DataFrame([r.to_dict() for r in records])


def df_to_records(df: pd.DataFrame) -> list[InvestorRecord]:
    """Convert a DataFrame back to InvestorRecord objects."""
    return [InvestorRecord.from_dict(row) for row in df.to_dict(orient="records")]
