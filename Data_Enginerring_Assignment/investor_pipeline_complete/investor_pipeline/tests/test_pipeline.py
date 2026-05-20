"""
tests/test_pipeline.py
───────────────────────
Unit tests for all major pipeline components.
Run with: pytest tests/ -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.utils.schema import InvestorRecord, records_to_df, df_to_records
from src.utils.helpers import (
    normalise_url, extract_domain, clean_text, extract_emails,
)
from src.cleaning.cleaner import DataCleaner, clean_records
from src.cleaning.deduplicator import Deduplicator, _make_fingerprint
from src.validation.validator import DataValidator


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def make_record(**kwargs) -> InvestorRecord:
    defaults = dict(
        investor_name="Test VC",
        investor_type="Venture Capital",
        firm_name="Test VC Partners",
        website_url="https://testvc.com",
        country="USA",
        geo_focus="North America",
        sector_focus="SaaS / Enterprise Software",
        source_url="https://testvc.com",
        data_source="Test",
        date_collected="2025-01-01",
    )
    defaults.update(kwargs)
    return InvestorRecord(**defaults)


# ─────────────────────────────────────────────────────────────────────────────
# Schema tests
# ─────────────────────────────────────────────────────────────────────────────

class TestInvestorRecord:

    def test_completeness_full_record(self):
        rec = make_record(
            city_region="San Francisco, CA",
            investment_thesis="Early-stage B2B SaaS",
            investment_stage="Seed",
            ticket_size="$500K–$2M",
            contact_email="info@testvc.com",
            confidence_score=0.9,
        )
        score = rec.completeness()
        assert 0.5 < score <= 1.0, f"Expected >0.5, got {score}"

    def test_completeness_minimal_record(self):
        rec = make_record()
        score = rec.completeness()
        # Mandatory fields only → ~40–50%
        assert 0.3 < score < 0.7, f"Expected 0.3–0.7, got {score}"

    def test_mandatory_completeness_full(self):
        rec = make_record()
        assert rec.mandatory_completeness() == 1.0

    def test_mandatory_completeness_missing(self):
        rec = make_record(country="", geo_focus="")
        assert rec.mandatory_completeness() < 1.0

    def test_from_dict_roundtrip(self):
        rec = make_record(investment_thesis="Test thesis")
        d   = rec.to_dict()
        rec2 = InvestorRecord.from_dict(d)
        assert rec2.investor_name == rec.investor_name
        assert rec2.investment_thesis == rec.investment_thesis

    def test_from_dict_ignores_unknown_keys(self):
        d = make_record().to_dict()
        d["unknown_field_xyz"] = "should be ignored"
        rec = InvestorRecord.from_dict(d)
        assert not hasattr(rec, "unknown_field_xyz")

    def test_records_to_df(self):
        records = [make_record(investor_name=f"VC {i}") for i in range(5)]
        df = records_to_df(records)
        assert len(df) == 5
        assert "investor_name" in df.columns

    def test_df_to_records_roundtrip(self):
        records = [make_record(investor_name=f"VC {i}") for i in range(3)]
        df = records_to_df(records)
        back = df_to_records(df)
        assert len(back) == 3
        assert back[0].investor_name == records[0].investor_name


# ─────────────────────────────────────────────────────────────────────────────
# Helper tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHelpers:

    def test_normalise_url_strips_trailing_slash(self):
        assert normalise_url("https://example.com/") == "https://example.com"

    def test_normalise_url_lowercase(self):
        assert normalise_url("HTTPS://EXAMPLE.COM") == "https://example.com"

    def test_normalise_url_strips_utm(self):
        url = normalise_url("https://example.com?utm_source=google&utm_medium=cpc")
        assert "utm" not in url

    def test_normalise_url_adds_scheme(self):
        result = normalise_url("example.com")
        assert result.startswith("https://")

    def test_normalise_url_empty(self):
        assert normalise_url("") == ""
        assert normalise_url(None) == ""

    def test_extract_domain(self):
        assert extract_domain("https://www.sequoiacap.com/about") == "sequoiacap.com"

    def test_clean_text_collapses_whitespace(self):
        assert clean_text("  hello   world  ") == "hello world"

    def test_clean_text_removes_nbsp(self):
        result = clean_text("hello\xa0world")
        assert "\xa0" not in result

    def test_extract_emails(self):
        text = "Contact us at info@example.com or support@test.org"
        emails = extract_emails(text)
        assert "info@example.com" in emails
        assert "support@test.org" in emails

    def test_extract_emails_no_match(self):
        assert extract_emails("no emails here") == []


# ─────────────────────────────────────────────────────────────────────────────
# Cleaner tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDataCleaner:
    cleaner = DataCleaner()

    def test_standardises_investor_type_vc(self):
        rec = make_record(investor_type="venture capital fund")
        cleaned = self.cleaner.clean([rec])[0]
        assert cleaned.investor_type == "Venture Capital"

    def test_standardises_investor_type_angel(self):
        rec = make_record(investor_type="Angel Network")
        cleaned = self.cleaner.clean([rec])[0]
        assert cleaned.investor_type == "Angel Investor"

    def test_standardises_sector_fintech(self):
        rec = make_record(sector_focus="fintech startup")
        cleaned = self.cleaner.clean([rec])[0]
        assert "FinTech" in cleaned.sector_focus

    def test_normalises_url(self):
        rec = make_record(website_url="HTTP://EXAMPLE.COM/")
        cleaned = self.cleaner.clean([rec])[0]
        # normalise_url lowercases scheme + host, strips trailing slash
        assert cleaned.website_url == "http://example.com"

    def test_drops_low_completeness_records(self):
        rec = InvestorRecord(investor_name="Ghost Record")
        # This record is missing almost everything — should be dropped
        result = self.cleaner.clean([rec])
        assert len(result) == 0

    def test_clean_email_invalid(self):
        rec = make_record(contact_email="not-an-email")
        cleaned = self.cleaner.clean([rec])[0]
        assert cleaned.contact_email is None

    def test_clean_email_valid(self):
        rec = make_record(contact_email="Info@Example.COM")
        cleaned = self.cleaner.clean([rec])[0]
        assert cleaned.contact_email == "info@example.com"

    def test_standardises_geo_usa(self):
        rec = make_record(geo_focus="United States")
        cleaned = self.cleaner.clean([rec])[0]
        assert cleaned.geo_focus == "North America"

    def test_standardises_stage_multi(self):
        rec = make_record(investment_stage="seed, series a, series b")
        cleaned = self.cleaner.clean([rec])[0]
        assert "Seed" in (cleaned.investment_stage or "")


# ─────────────────────────────────────────────────────────────────────────────
# Deduplicator tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDeduplicator:
    deduper = Deduplicator()

    def test_exact_dedup_removes_identical(self):
        rec1 = make_record(investor_name="Acme VC", firm_name="Acme VC",
                           website_url="https://acme.vc")
        rec2 = make_record(investor_name="Acme VC", firm_name="Acme VC",
                           website_url="https://acme.vc")
        result = self.deduper.deduplicate([rec1, rec2])
        assert len(result) == 1

    def test_different_domains_not_deduped(self):
        rec1 = make_record(firm_name="Alpha VC", website_url="https://alpha.vc")
        rec2 = make_record(firm_name="Beta VC",  website_url="https://beta.vc")
        result = self.deduper.deduplicate([rec1, rec2])
        assert len(result) == 2

    def test_keeps_most_complete(self):
        rec1 = make_record(firm_name="Complete VC", website_url="https://complete.vc",
                           investment_thesis="Great thesis")
        rec2 = make_record(firm_name="Complete VC", website_url="https://complete.vc")
        result = self.deduper.deduplicate([rec1, rec2])
        assert len(result) == 1
        assert result[0].investment_thesis == "Great thesis"

    def test_fingerprint_consistent(self):
        rec = make_record(firm_name="Test VC", website_url="https://testvc.com")
        fp1 = _make_fingerprint(rec)
        fp2 = _make_fingerprint(rec)
        assert fp1 == fp2

    def test_fingerprint_differs_by_domain(self):
        rec1 = make_record(firm_name="Test VC", website_url="https://testvc.com")
        rec2 = make_record(firm_name="Test VC", website_url="https://othervc.com")
        assert _make_fingerprint(rec1) != _make_fingerprint(rec2)

    def test_assigns_record_ids(self):
        records = [make_record(firm_name=f"VC {i}", website_url=f"https://vc{i}.com")
                   for i in range(5)]
        result = self.deduper.deduplicate(records)
        for rec in result:
            assert rec.record_id is not None
            assert len(rec.record_id) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Validator tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDataValidator:
    validator = DataValidator()

    def test_valid_record_passes(self):
        rec = make_record()
        valid, stats = self.validator.validate([rec])
        assert len(valid) == 1
        assert stats["valid"] == 1

    def test_missing_mandatory_fails(self):
        rec = InvestorRecord(investor_name="", investor_type="VC")
        valid, stats = self.validator.validate([rec])
        assert len(valid) == 0
        assert stats["invalid"] == 1

    def test_quality_score_assigned(self):
        rec = make_record(investment_thesis="Rich thesis", investment_stage="Seed")
        valid, _ = self.validator.validate([rec])
        assert len(valid) == 1
        assert valid[0].confidence_score is not None
        assert 0 < valid[0].confidence_score <= 1.0

    def test_invalid_email_generates_warning(self):
        rec = make_record(contact_email="bad-email")
        valid, stats = self.validator.validate([rec])
        # Record should still pass (email is optional), but have a warning in notes
        assert len(valid) == 1

    def test_field_coverage_in_stats(self):
        records = [make_record() for _ in range(10)]
        _, stats = self.validator.validate(records)
        assert "field_coverage" in stats
        assert "investor_name" in stats["field_coverage"]

    def test_multiple_records_stats(self):
        good = [make_record(investor_name=f"VC {i}") for i in range(5)]
        bad  = [InvestorRecord(investor_name="")]
        _, stats = self.validator.validate(good + bad)
        assert stats["total"] == 6
        assert stats["valid"] == 5
        assert stats["invalid"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Integration test (light — no real HTTP calls)
# ─────────────────────────────────────────────────────────────────────────────

class TestIntegration:

    def test_mini_pipeline(self):
        """Run clean → dedup → validate on a small synthetic dataset."""
        records = [
            make_record(investor_name="Alpha VC",   website_url="https://alpha.vc",
                        investment_thesis="Early stage generalist"),
            make_record(investor_name="Alpha VC",   website_url="https://alpha.vc"),  # dup
            make_record(investor_name="Beta Capital", website_url="https://beta.vc",
                        sector_focus="fintech"),
            make_record(investor_name="",            website_url=""),                  # invalid
        ]

        cleaned = clean_records(records)
        assert 0 < len(cleaned) < len(records)   # some dropped

        deduped = Deduplicator().deduplicate(cleaned)
        assert len(deduped) <= len(cleaned)       # some deduped

        valid, stats = DataValidator().validate(deduped)
        assert stats["valid"] >= 1
        assert stats["avg_quality_score"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
