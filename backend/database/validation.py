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
import re
from typing import Dict, List, Optional

import pandas as pd

# The canonical SAGE burn-in schema (early-window screening: 0h and 24h checkpoints).
REQUIRED_COLUMNS: List[str] = [
    "Component_ID", "Batch_ID", "Part_Type", "Part_Family",
    "Proxy_Stress", "Stress_Level", "Stress_Unit",
    "Timestamp_0h", "Timestamp_24h",
    "Leakage_0h", "Leakage_24h",
]

# Recognised-but-optional columns: late checkpoints (96h, 168h), ground-truth
# evaluation labels, the notebook's batch-grouped train/val/test role,
# free-form provenance fields and unit annotations, as well as multi-metric columns.
OPTIONAL_COLUMNS: List[str] = [
    "Timestamp_96h", "Timestamp_168h",
    "Leakage_96h", "Leakage_168h",
    "Traditional_Test_Result", "Label",
    "Sensor_ID", "Operator", "Notes", "split",
    "Leakage_Unit", "Resistance_Unit", "Vth_Unit",
    "Resistance_0h", "Resistance_24h", "Resistance_96h", "Resistance_168h",
    "Vth_0h", "Vth_24h", "Vth_96h", "Vth_168h",
]

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

# Controlled Issue Categories for Phase 16 Data Quality Gate
CATEGORY_MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
CATEGORY_INVALID_TYPE = "INVALID_TYPE"
CATEGORY_INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
CATEGORY_INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
CATEGORY_INVALID_NUMERIC_VALUE = "INVALID_NUMERIC_VALUE"
CATEGORY_DUPLICATE_RECORD = "DUPLICATE_RECORD"
CATEGORY_INVALID_RANGE = "INVALID_RANGE"
CATEGORY_UNIT_INCONSISTENCY = "UNIT_INCONSISTENCY"
CATEGORY_MALFORMED_RECORD = "MALFORMED_RECORD"

VALID_ISSUE_CATEGORIES = {
    CATEGORY_MISSING_REQUIRED_FIELD,
    CATEGORY_INVALID_TYPE,
    CATEGORY_INVALID_TIMESTAMP,
    CATEGORY_INVALID_IDENTIFIER,
    CATEGORY_INVALID_NUMERIC_VALUE,
    CATEGORY_DUPLICATE_RECORD,
    CATEGORY_INVALID_RANGE,
    CATEGORY_UNIT_INCONSISTENCY,
    CATEGORY_MALFORMED_RECORD,
}


@dataclass
class ValidationIssue:
    row_index: Optional[int]          # None for dataset-level issues
    component_id: Optional[str]
    column: Optional[str]
    issue_type: str                   # legacy: "missing_value", "duplicate_id", etc.
    detail: str
    category: Optional[str] = None    # Controlled category for Phase 16
    severity: str = "ERROR"           # "ERROR" | "WARNING"

    def __post_init__(self):
        if self.category is None:
            if self.issue_type == "wrong_columns":
                self.category = CATEGORY_MISSING_REQUIRED_FIELD
            elif self.issue_type == "no_data":
                self.category = CATEGORY_MALFORMED_RECORD
            elif self.issue_type in ("invalid_component_id", "invalid_lot_id"):
                self.category = CATEGORY_INVALID_IDENTIFIER
            elif self.issue_type == "duplicate_id":
                self.category = CATEGORY_DUPLICATE_RECORD
            elif self.issue_type == "invalid_number":
                self.category = CATEGORY_INVALID_NUMERIC_VALUE
            elif self.issue_type == "invalid_temperature":
                self.category = CATEGORY_INVALID_RANGE
            elif self.issue_type == "invalid_timestamp":
                self.category = CATEGORY_INVALID_TIMESTAMP
            elif self.issue_type == "unit_inconsistency":
                self.category = CATEGORY_UNIT_INCONSISTENCY
            elif self.issue_type == "missing_value":
                self.category = CATEGORY_MISSING_REQUIRED_FIELD
            else:
                self.category = CATEGORY_MALFORMED_RECORD


@dataclass
class ValidationReport:
    total_rows: int = 0
    valid_rows: int = 0
    rejected_rows: int = 0
    warning_rows: int = 0
    issues: List[ValidationIssue] = field(default_factory=list)
    # Row indices that failed hard checks (duplicate/bad ID, non-numeric,
    # implausible reading, missing identity fields).
    rejected_indices: set = field(default_factory=set)
    # Row indices with non-rejecting warnings (imputable missing values, schedule drift)
    warning_indices: set = field(default_factory=set)

    @property
    def is_valid(self) -> bool:
        """Dataset is valid if nothing fatal was found (per-row rejects allowed)."""
        return not any(i.issue_type in FATAL_ISSUE_TYPES for i in self.issues)

    @property
    def has_errors(self) -> bool:
        return bool(self.issues)

    def add(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)

    def data_quality_summary(self) -> Dict:
        """Standardized Phase 16 Data Quality result contract."""
        by_category: Dict[str, int] = {}
        for i in self.issues:
            cat = i.category or "MALFORMED_RECORD"
            by_category[cat] = by_category.get(cat, 0) + 1

        if not self.is_valid or (self.total_rows > 0 and self.valid_rows == 0):
            status = "REJECTED"
        elif self.rejected_rows > 0 or len(self.warning_indices) > 0:
            status = "WARNING"
        else:
            status = "PASS"

        return {
            "status": status,
            "total_rows": self.total_rows,
            "accepted_rows": self.valid_rows,
            "rejected_rows": self.rejected_rows,
            "warning_rows": len(self.warning_indices),
            "issues": [
                {
                    "row": i.row_index,
                    "component_id": i.component_id,
                    "field": i.column,
                    "category": i.category,
                    "severity": i.severity,
                    "reason": i.detail,
                }
                for i in self.issues
            ],
            "issues_by_category": by_category,
        }

    def summary(self) -> Dict:
        by_type: Dict[str, int] = {}
        for i in self.issues:
            by_type[i.issue_type] = by_type.get(i.issue_type, 0) + 1
        dq = self.data_quality_summary()
        return {
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "rejected_rows": self.rejected_rows,
            "warning_rows": dq["warning_rows"],
            "issue_count": len(self.issues),
            "issues_by_type": by_type,
            "issues_by_category": dq["issues_by_category"],
            "status": dq["status"],
            "is_valid": self.is_valid,
            "data_quality": dq,
        }


# Issues that make the whole file unacceptable (vs. row-level rejects).
FATAL_ISSUE_TYPES = {"wrong_columns", "no_data"}


class DataValidationError(ValueError):
    """Raised when a dataset cannot be ingested at all (fatal issues or zero valid rows)."""

    def __init__(self, report: ValidationReport):
        self.report = report
        details = "; ".join(
            f"{i.issue_type}: {i.detail}" for i in report.issues if i.issue_type in FATAL_ISSUE_TYPES
        )
        if not details and report.issues:
            details = "; ".join(f"{i.category or i.issue_type}: {i.detail}" for i in report.issues[:5])
        super().__init__(details or "Dataset failed validation: zero valid rows.")


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
                                       f"Component ID '{cid}' does not match expected pattern (e.g. C101, T0001)."))
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
        active_params = [
            param for param in PLAUSIBLE_RANGES
            if any(f"{param}_{cp}" in df.columns for cp in ("0h", "24h", "96h", "168h"))
        ]
        for param in active_params:
            lo, hi = PLAUSIBLE_RANGES[param]
            for cp in ("0h", "24h", "96h", "168h"):
                col = f"{param}_{cp}"
                if col not in df.columns:
                    continue
                raw = row.get(col)

                if _is_missing(raw):
                    # Missing readings are flagged here and imputed by
                    # preprocessing (lot-median) — not a hard reject.
                    report.add(ValidationIssue(idx, cid, col, "missing_value",
                                               f"Missing {param} reading at {cp} for '{cid}' (imputed later).",
                                               category=CATEGORY_MISSING_REQUIRED_FIELD, severity="WARNING"))
                    report.warning_indices.add(idx)
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
            if col not in df.columns:
                continue
            ts = row.get(col)
            if _is_missing(ts):
                # Flag only; preprocessing falls back to the nominal schedule.
                report.add(ValidationIssue(idx, cid, col, "missing_value",
                                           f"Missing timestamp {col} for '{cid}' (nominal schedule used).",
                                           category=CATEGORY_MISSING_REQUIRED_FIELD, severity="WARNING"))
                report.warning_indices.add(idx)
                continue
            parsed = pd.to_datetime(ts, errors="coerce")
            if pd.isna(parsed):
                report.add(ValidationIssue(idx, cid, col, "invalid_timestamp",
                                           f"Unparseable timestamp '{ts}' in {col} for '{cid}'."))
                rejected = True
                continue
            if nominal > 0:
                t0 = pd.to_datetime(row.get("Timestamp_0h"), errors="coerce")
                if pd.notna(t0):
                    delta_h = abs((parsed - t0).total_seconds() / 3600 - nominal)
                    if delta_h > TIMESTAMP_TOLERANCE_HOURS:
                        report.add(ValidationIssue(idx, cid, col, "invalid_timestamp",
                                                   f"{col} deviates {delta_h:.1f}h from nominal {nominal}h for '{cid}'.",
                                                   category=CATEGORY_INVALID_TIMESTAMP, severity="WARNING"))
                        report.warning_indices.add(idx)

        # Unit consistency check if explicit unit columns are supplied
        KNOWN_UNITS = {
            "Leakage": {"µA", "uA", "nA", "mA", "A"},
            "Resistance": {"Ω", "Ohm", "ohm", "kΩ", "kOhm", "mΩ"},
            "Vth": {"V", "mV", "kV"},
            "Stress": {"cycles", "hours", "hrs", "V", "°C", "degC", "krad"},
        }
        for param in ("Leakage", "Resistance", "Vth"):
            unit_col = f"{param}_Unit"
            if unit_col in df.columns:
                raw_unit = str(row.get(unit_col) or "").strip()
                if raw_unit and raw_unit not in KNOWN_UNITS[param]:
                    report.add(ValidationIssue(
                        idx, cid, unit_col, "unit_inconsistency",
                        f"Unrecognized unit '{raw_unit}' in {unit_col} for '{cid}'.",
                        category=CATEGORY_UNIT_INCONSISTENCY, severity="WARNING"
                    ))
                    report.warning_indices.add(idx)
        if "Stress_Unit" in df.columns:
            s_unit = str(row.get("Stress_Unit") or "").strip()
            if s_unit and s_unit not in KNOWN_UNITS["Stress"]:
                report.add(ValidationIssue(
                    idx, cid, "Stress_Unit", "unit_inconsistency",
                    f"Unrecognized stress unit '{s_unit}' for '{cid}'.",
                    category=CATEGORY_UNIT_INCONSISTENCY, severity="WARNING"
                ))
                report.warning_indices.add(idx)

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

    report.warning_rows = len(report.warning_indices)
    return report


_COMPONENT_ID_RE = re.compile(r"^[A-Za-z]{1,6}[-_]?\d+[A-Za-z]?$")


def _valid_component_id(cid: str) -> bool:
    """Validate component identifier: allows C101, T0001, DUT-01, IC101, etc."""
    cid = str(cid).strip()
    return bool(cid) and bool(_COMPONENT_ID_RE.match(cid))


def filter_valid_rows(df: pd.DataFrame, report: ValidationReport) -> pd.DataFrame:
    """
    Return only rows that survived validation, preserving order.

    Only hard-rejected rows are dropped. Rows with imputable issues (missing
    readings/timestamps, out-of-schedule timestamps) pass through so
    preprocessing can repair them.
    """
    rejected_idx = report.rejected_indices
    return df.loc[[i for i in df.index if i not in rejected_idx]].copy()
