"""
src/export/exporter.py
───────────────────────
Export pipeline.

Exports the final validated dataset to:
  - CSV  (primary deliverable)
  - JSON (AI ingestion, API consumption)
  - Excel (stakeholder-friendly)

Column ordering: mandatory fields first, nice-to-have next, metadata last.
All string values are stripped and None → "" in CSV/Excel (so tools don't
see NaN/null values where empty strings are expected).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import (
    FINAL_DIR, OUTPUT_DIR,
    FINAL_CSV_NAME, FINAL_JSON_NAME, FINAL_XLSX_NAME,
    MANDATORY_FIELDS, OPTIONAL_FIELDS,
)
from src.utils.helpers import get_logger
from src.utils.schema import InvestorRecord, records_to_df

logger = get_logger("export.exporter")


# ── Column ordering for the final export ─────────────────────────────────────
EXPORT_COLUMNS = [
    # Mandatory
    "investor_name", "investor_type", "firm_name", "website_url",
    "country", "geo_focus", "sector_focus",
    "source_url", "data_source", "date_collected",
    # Nice-to-have
    "city_region", "investment_thesis", "investment_stage", "ticket_size",
    "portfolio_companies", "key_partners",
    "contact_email", "contact_phone", "linkedin_url",
    # Quality metadata
    "confidence_score", "notes",
    # Internal (kept for traceability)
    "record_id",
]


class DataExporter:

    def export_all(self, records: list[InvestorRecord],
                   output_dir: Path = OUTPUT_DIR) -> dict[str, Path]:
        """
        Export records to CSV, JSON, and Excel.
        Returns a dict of {format: Path}.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        df = self._prepare_dataframe(records)
        logger.info(f"Exporting {len(df)} records to {output_dir}")

        paths = {}
        paths["csv"]   = self._export_csv(df, output_dir)
        paths["json"]  = self._export_json(records, output_dir)
        paths["excel"] = self._export_excel(df, output_dir)

        # Summary stats file
        paths["summary"] = self._export_summary(df, records, output_dir)

        logger.info("Export complete:")
        for fmt, path in paths.items():
            logger.info(f"  {fmt:8s}: {path}")

        return paths

    # ── CSV ──────────────────────────────────────────────────────────────────

    def _export_csv(self, df: pd.DataFrame, output_dir: Path) -> Path:
        path = output_dir / FINAL_CSV_NAME
        df_export = df.copy()
        # Fill NaN with empty string for CSV cleanliness
        df_export = df_export.fillna("")
        df_export.to_csv(path, index=False, encoding="utf-8-sig")  # utf-8-sig for Excel compat
        logger.info(f"  CSV: {len(df_export)} rows → {path}")
        return path

    # ── JSON ─────────────────────────────────────────────────────────────────

    def _export_json(self, records: list[InvestorRecord], output_dir: Path) -> Path:
        path = output_dir / FINAL_JSON_NAME
        # Build a clean list of dicts, dropping None values for JSON neatness
        data = []
        for rec in records:
            d = {k: v for k, v in rec.to_dict().items()
                 if k in EXPORT_COLUMNS and v not in (None, "", [])}
            # Add schema version for AI ingestion
            d["_schema_version"] = "1.0"
            data.append(d)

        with open(path, "w", encoding="utf-8") as f:
            json.dump({"metadata": self._make_metadata(records),
                       "investors": data}, f, ensure_ascii=False, indent=2)
        logger.info(f"  JSON: {len(data)} records → {path}")
        return path

    # ── Excel ────────────────────────────────────────────────────────────────

    def _export_excel(self, df: pd.DataFrame, output_dir: Path) -> Path:
        path = output_dir / FINAL_XLSX_NAME
        df_export = df.copy().fillna("")

        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            # Main data sheet
            df_export.to_excel(writer, sheet_name="Investors", index=False)
            ws = writer.sheets["Investors"]

            # Auto-size columns (capped at 60 chars)
            for col_idx, col_name in enumerate(df_export.columns, 1):
                max_len = max(
                    df_export[col_name].astype(str).str.len().max(),
                    len(col_name),
                )
                ws.column_dimensions[
                    ws.cell(row=1, column=col_idx).column_letter
                ].width = min(max_len + 2, 60)

            # Style header row
            from openpyxl.styles import Font, PatternFill, Alignment
            HEADER_FILL  = PatternFill("solid", fgColor="2E4A6E")
            HEADER_FONT  = Font(color="FFFFFF", bold=True, size=11)
            ALIGN_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)

            for cell in ws[1]:
                cell.font      = HEADER_FONT
                cell.fill      = HEADER_FILL
                cell.alignment = ALIGN_CENTER

            # Freeze header row
            ws.freeze_panes = "A2"

            # Stats sheet
            stats_data = {
                "Metric": [
                    "Total Records",
                    "Unique Investor Types",
                    "Unique Countries",
                    "Unique Sectors",
                    "Records with Email",
                    "Records with LinkedIn",
                    "Records with Thesis",
                    "Avg Confidence Score",
                    "Export Date",
                ],
                "Value": [
                    len(df_export),
                    df_export["investor_type"].nunique(),
                    df_export["country"].nunique(),
                    df_export["sector_focus"].nunique(),
                    (df_export["contact_email"] != "").sum(),
                    (df_export["linkedin_url"] != "").sum(),
                    (df_export["investment_thesis"] != "").sum(),
                    round(pd.to_numeric(df_export["confidence_score"], errors="coerce").mean(), 3),
                    datetime.now().strftime("%Y-%m-%d"),
                ],
            }
            pd.DataFrame(stats_data).to_excel(writer, sheet_name="Stats", index=False)

        logger.info(f"  Excel: {len(df_export)} rows → {path}")
        return path

    # ── Summary ──────────────────────────────────────────────────────────────

    def _export_summary(self, df: pd.DataFrame,
                        records: list[InvestorRecord],
                        output_dir: Path) -> Path:
        path = output_dir / "pipeline_summary.json"
        summary = self._make_metadata(records)
        summary["type_distribution"]    = df["investor_type"].value_counts().to_dict()
        summary["geo_distribution"]     = df["geo_focus"].value_counts().to_dict()
        summary["sector_distribution"]  = df["sector_focus"].value_counts().to_dict()
        summary["country_distribution"] = df["country"].value_counts().head(20).to_dict()
        summary["source_distribution"]  = df["data_source"].value_counts().to_dict()

        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        return path

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _prepare_dataframe(self, records: list[InvestorRecord]) -> pd.DataFrame:
        """Convert records to a clean DataFrame with standardised column order."""
        df = records_to_df(records)
        # Keep only export columns (add missing ones as empty)
        for col in EXPORT_COLUMNS:
            if col not in df.columns:
                df[col] = ""
        df = df[EXPORT_COLUMNS]
        # Ensure confidence_score is float
        df["confidence_score"] = pd.to_numeric(df["confidence_score"], errors="coerce")
        return df

    def _make_metadata(self, records: list[InvestorRecord]) -> dict:
        return {
            "total_records":    len(records),
            "export_timestamp": datetime.now().isoformat(),
            "schema_version":   "1.0",
            "pipeline_version": "1.0",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Convenience function
# ─────────────────────────────────────────────────────────────────────────────

def export_records(records: list[InvestorRecord],
                   output_dir: Path = OUTPUT_DIR) -> dict[str, Path]:
    return DataExporter().export_all(records, output_dir)
