"""
Data validation (Member 2).

Runs BEFORE anything is stored. Given a raw parsed CSV frame, produce a
ValidationReport listing every problem found, plus the list of clean rows.

Checks implemented (per the project spec):
  • wrong columns          — required columns missing / unexpected extras
  • invalid component IDs  — bad format, e.g. not ``C<number>``
  • invalid lot IDs        — empty / malformed batch IDs
  • duplicate components   — same Component_ID appearing twice
  • missing values         — NaN readings in the early-window feature columns
  • invalid numbers        — non-numeric or un-parseable measurement cells
  • invalid temperature    — readings outside the physically plausible range
                             (the generator's sensor-fault sentinel -1.0 counts)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

# The canonical SAGE burn-in schema (scripts/generate_burn_in_dataset.py FIELDNAMES).
REQUIRED_COLUMNS: List[str] = [
    "Component_ID", "Batch_ID", "Part_Type", "Part_Family",
    "Proxy_Stress", "Stress_Level", "Stress_Unit",
    "Timestamp_0h", "Timestamp_24h", "Timestamp_96h", "Timestamp_168h",
    "Leakage_0h", "Leakage_24h", "Leakage_96h", "Leakage_168h",
    "Resistance_0h", "Resistance_24h", "Resistance_96h", "Resistance_168h",
    "Vth_0h", "Vth_24h", "Vth_96h", "Vth_168h",
    "Traditional_Test_Result", "Label",
]

OPTIONAL_COLUMNS: List[str] = ["Sensor_ID", "Operator", "Notes"]

# Physically plausible ranges (same bounds as the dataset generator).
PLAUSIBLE_RANGES: Dict[str, tuple] = {
    "Leakage": (0.0, 50.0),        # µA — generous headroom above generator max 5.0
    "Resistance": (10.0, 500.0),   # Ω  — headroom above 90–150
    "Vth": (0.0, 10.0),            # V  — headroom above 0.8–1.5
}
SENSOR_FAULT_SENTINEL = -1.0

# Timestamps are allowed a tolerance around the 0/24/96/168h schedule.
CHECKPOINT_HOURS = {"0h": 0.0, "24h": 24.0, "96h": 96.0, "168h": 168.0}
TIMESTAMP_TOLERANCE_HOURS = 6.0


@dataclass
class ValidationIssue:
    row_index: Optional[int]          # None for dataset-level issues
    component_id: Optional[str]
    column: Optional[str]
    issue_type: str                   # e.g. "missing_value", "duplicate_id"
    detail: str


@dataclass
class ValidationReport:
    total_rows: int = 0
    valid_rows: int = 0
    rejected_rows: int = 0
    issues: List[ValidationIssue] = field(default_factory=list)
    # Row indices that failed hard checks (duplicate/bad ID, non-numeric,
    # implausible reading, missing identity fields). Rows that merely carry
    # imputable issues (missing readings/timestamps) stay in `rejected_indices`
    # off-list — validation flags them but preprocessing repairs them.
    rejected_indices: set = field(default_factory=set)

    @property
    def is_valid(self) -> bool:
        """Dataset is valid if nothing fatal was found (per-row rejects allowed)."""
        return not any(i.issue_type in FATAL_ISSUE_TYPES for i in self.issues)

    @property
    def has_errors(self) -> bool:
        return bool(self.issues)

    def add(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)

    def summary(self) -> Dict:
        by_type: Dict[str, int] = {}
        for i in self.issues:
            by_type[i.issue_type] = by_type.get(i.issue_type, 0) + 1
        return {
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "rejected_rows": self.rejected_rows,
            "issue_count": len(self.issues),
            "issues_by_type": by_type,
            "is_valid": self.is_valid,
        }


# Issues that make the whole file unacceptable (vs. row-level rejects).
FATAL_ISSUE_TYPES = {"wrong_columns", "no_data"}


class DataValidationError(ValueError):
    """Raised when a dataset cannot be ingested at all (fatal issues)."""

    def __init__(self, report: ValidationReport):
        self.report = report
        details = "; ".join(
            f"{i.issue_type}: {i.detail}" for i in report.issues if i.issue_type in FATAL_ISSUE_TYPES
        )
        super().__init__(details or "Dataset failed validation.")


def _is_missing(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or (isinstance(v, str) and not v.strip())


def _to_number(v) -> Optional[float]:
    """Parse a cell into float; return None if not coercible."""
    if _is_missing(v):
        return None
    try:
        f = float(v)
        # pd.isna covers NaN/inf from strings like 'NaN'
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def validate_dataframe(df: pd.DataFrame) -> ValidationReport:
    """Validate a raw burn-in frame. Never raises; inspect the returned report."""
    report = ValidationReport(total_rows=len(df))

    # ---- dataset-level: wrong columns -------------------------------------
    if df.empty:
        report.add(ValidationIssue(None, None, None, "no_data", "Uploaded file has no data rows."))
        return report

    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    extra_cols = [c for c in df.columns if c not in REQUIRED_COLUMNS and c not in OPTIONAL_COLUMNS]
    if missing_cols:
        report.add(
            ValidationIssue(
                None, None, None, "wrong_columns",
                f"Missing required columns: {missing_cols}",
            )
        )
        if extra_cols:
            report.add(
                ValidationIssue(None, None, None, "wrong_columns", f"Unexpected extra columns: {extra_cols}")
            )
        return report  # cannot reliably check rows without the schema
    if extra_cols:
        report.add(
            ValidationIssue(None, None, None, "wrong_columns", f"Unexpected extra columns (ignored): {extra_cols}")
        )

    # ---- row-level checks ---------------------------------------------------
    seen_ids: Dict[str, int] = {}
    id_series = df["Component_ID"].astype(str).str.strip()

    for idx, row in df.iterrows():
        cid = id_series.loc[idx]
        lot_id = row.get("Batch_ID")
        rejected = False

        # invalid component ID
        if _is_missing(cid) or not _valid_component_id(cid):
            report.add(ValidationIssue(idx, str(cid) if not _is_missing(cid) else None,
                                       "Component_ID", "invalid_component_id",
                                       f"Component ID '{cid}' does not match expected pattern (e.g. C101)."))
            rejected = True

        # duplicate component
        if cid in seen_ids:
            report.add(ValidationIssue(idx, cid, "Component_ID", "duplicate_id",
                                       f"Duplicate component ID '{cid}' (first seen at row {seen_ids[cid]})."))
            rejected = True
        else:
            seen_ids[cid] = idx

        # invalid lot ID
        if _is_missing(lot_id) or not str(lot_id).strip():
            report.add(ValidationIssue(idx, cid, "Batch_ID", "invalid_lot_id",
                                       f"Missing/empty lot (Batch_ID) for '{cid}'."))
            rejected = True

        # numeric parameter cells: missing values + non-numeric + range checks
        for param, (lo, hi) in PLAUSIBLE_RANGES.items():
            for cp in ("0h", "24h", "96h", "168h"):
                col = f"{param}_{cp}"
                raw = row.get(col)

                if _is_missing(raw):
                    # Missing readings are flagged here and imputed by
                    # preprocessing (lot-median) — not a hard reject.
                    report.add(ValidationIssue(idx, cid, col, "missing_value",
                                               f"Missing {param} reading at {cp} for '{cid}' (imputed later)."))
                    continue

                num = _to_number(raw)
                if num is None:
                    report.add(ValidationIssue(idx, cid, col, "invalid_number",
                                               f"Non-numeric value '{raw}' in {col} for '{cid}'."))
                    rejected = True
                    continue

                if num == SENSOR_FAULT_SENTINEL or num < lo or num > hi:
                    report.add(ValidationIssue(idx, cid, col, "invalid_temperature",
                                               f"Implausible {param} value {num} in {col} for '{cid}' "
                                               f"(allowed {lo}–{hi}; {SENSOR_FAULT_SENTINEL} = sensor fault)."))
                    rejected = True

        # timestamp sanity: parseable, ordered, within tolerance of the schedule
        for cp, nominal in CHECKPOINT_HOURS.items():
            col = f"Timestamp_{cp}"
            ts = row.get(col)
            if _is_missing(ts):
                # Flag only; preprocessing falls back to the nominal schedule.
                report.add(ValidationIssue(idx, cid, col, "missing_value",
                                           f"Missing timestamp {col} for '{cid}' (nominal schedule used)."))
                continue
            parsed = pd.to_datetime(ts, errors="coerce")
            if pd.isna(parsed):
                report.add(ValidationIssue(idx, cid, col, "invalid_timestamp",
                                           f"Unparseable timestamp '{ts}' in {col} for '{cid}'."))
                rejected = True
                continue
            if nominal > 0:
                delta_h = abs((parsed - pd.to_datetime(row["Timestamp_0h"], errors="coerce")).total_seconds() / 3600
                              - nominal)
                if delta_h > TIMESTAMP_TOLERANCE_HOURS:
                    report.add(ValidationIssue(idx, cid, col, "invalid_timestamp",
                                               f"{col} deviates {delta_h:.1f}h from nominal {nominal}h for '{cid}'."))

        # Part_Type must be present (it drives lot typing and model grouping)
        if _is_missing(row.get("Part_Type")):
            report.add(ValidationIssue(idx, cid, "Part_Type", "missing_value",
                                       f"Missing Part_Type for '{cid}'."))
            rejected = True

        if not rejected:
            report.valid_rows += 1
        else:
            report.rejected_rows += 1
            report.rejected_indices.add(idx)

    return report


def _valid_component_id(cid: str) -> bool:
    """C101 → valid; C-101, 101, c101 (lenient) etc. checked here."""
    cid = str(cid).strip()
    return bool(cid) and cid[0].upper() == "C" and cid[1:].isdigit()


def filter_valid_rows(df: pd.DataFrame, report: ValidationReport) -> pd.DataFrame:
    """
    Return only rows that survived validation, preserving order.

    Only hard-rejected rows are dropped. Rows with imputable issues (missing
    readings/timestamps, out-of-schedule timestamps) pass through so
    preprocessing can repair them.
    """
    rejected_idx = report.rejected_indices
    return df.loc[[i for i in df.index if i not in rejected_idx]].copy()
