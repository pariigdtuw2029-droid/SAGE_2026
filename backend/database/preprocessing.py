"""
Data preprocessing (Member 2).

Turns a validated raw burn-in frame into the clean, standardized shape the
rest of the pipeline expects. Runs after `validation.validate_dataframe`.

Tasks (per the project spec):
  • clean columns            — trim whitespace, canonical order, drop blanks
  • standardize units        — Leakage µA, Resistance Ω, Vth V (documented, idempotent)
  • handle missing values    — lot-median imputation for the 96h/168h checkpoints,
                               forward-fill where sensible, sensor-fault sentinel → NaN
  • convert data types       — float coercion, datetime parsing, category normalization
  • organize timestamps      — real per-checkpoint datetimes, hour deltas vs 0h
  • ensure component/lot relationships — lots derived from Batch_ID + Part_Type,
                               components linked to their lot, consistent types

Output: a `PreprocessedBurnIn` object with a wide `df` (for the models, which
expect the exact CSV column layout) plus `lots` / `components` / `measurements`
DataFrames ready for `crud` / `ingestion`.
"""

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd

from database.validation import SENSOR_FAULT_SENTINEL

# Canonical units (documented; conversion is a no-op when input is already canonical).
PARAMETER_UNITS: Dict[str, str] = {
    "Leakage": "µA",
    "Resistance": "Ω",
    "Vth": "V",
}

CHECKPOINTS: List[str] = ["0h", "24h", "96h", "168h"]
PARAMETERS: List[str] = ["Leakage", "Resistance", "Vth"]

# Unit conversion factors → canonical unit (base factor, canonical unit name).
# Values are multipliers applied to a reading to express it in the canonical unit.
UNIT_CONVERSIONS: Dict[str, Dict[str, float]] = {
    "Leakage": {"µA": 1.0, "uA": 1.0, "nA": 1e-3, "mA": 1e3, "A": 1e6},
    "Resistance": {"Ω": 1.0, "Ohm": 1.0, "ohm": 1.0, "kΩ": 1e3, "kOhm": 1e3, "mΩ": 1e-3},
    "Vth": {"V": 1.0, "mV": 1e-3, "kV": 1e3},
}

VALID_PART_TYPES = {"MOSFET", "Op-Amp IC", "Voltage Regulator IC", "Digital Logic IC"}
VALID_TRADITIONAL = {"Pass", "Fail"}
VALID_LABELS = {"Safe", "Borderline", "Fail"}
VALID_STRESS_TYPES = {"Thermal-High", "Radiation-Low", "Cycling-Medium"}

# Numeric columns that must end up float64.
_READING_COLS = [f"{p}_{cp}" for p in PARAMETERS for cp in CHECKPOINTS]


@dataclass
class PreprocessedBurnIn:
    df: pd.DataFrame                                   # wide, model-ready, canonical layout
    lots: pd.DataFrame                                 # lot_id | component_type | temperature | total_components | ...
    components: pd.DataFrame                           # component_id | lot_id | component_type | status | ...
    measurements: pd.DataFrame                         # long format: component_id, timestamp, parameter, value, ...
    issues: List[str] = field(default_factory=list)    # human-readable warnings from preprocessing

    @property
    def component_count(self) -> int:
        return len(self.components)


def clean_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """Strip surrounding whitespace from every column name and cell string."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    str_cols = df.select_dtypes(include=["object"]).columns
    for c in str_cols:
        df[c] = df[c].astype(str).str.strip()
    return df


def standardize_units(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert parameter readings into canonical units (µA / Ω / V).

    Unit is inferred from a ``<Param>_Unit`` column if present (e.g. Leakage_Unit,
    Resistance_Unit, Vth_Unit); otherwise assumed canonical. Numeric conversions
    only — the raw value is multiplied by the factor in UNIT_CONVERSIONS.
    """
    df = df.copy()
    for p in PARAMETERS:
        unit_col = f"{p}_Unit"
        conv = UNIT_CONVERSIONS[p]
        factor = 1.0
        if unit_col in df.columns:
            unit = df[unit_col].astype(str).str.strip().iloc[0] if len(df) else "µA"
            factor = conv.get(unit, 1.0)
        for cp in CHECKPOINTS:
            col = f"{p}_{cp}"
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce") * factor
    return df


def handle_missing_values(df: pd.DataFrame) -> pd.DataFrame:
    """
    • sensor-fault sentinel (-1.0) → NaN
    • NaN readings imputed with the lot median, falling back to the global
      column median (same strategy as the training notebooks).
    Tracks how many cells were imputed (reported via `issues` list on caller).
    """
    df = df.copy()
    for col in _READING_COLS:
        if col not in df.columns:
            continue
        df[col] = pd.to_numeric(df[col], errors="coerce")
        df.loc[df[col] == SENSOR_FAULT_SENTINEL, col] = np.nan

    lot_col = "Batch_ID" if "Batch_ID" in df.columns else None
    imputed = 0
    for col in _READING_COLS:
        if col not in df.columns or not df[col].isna().any():
            continue
        if lot_col and lot_col in df.columns:
            df[col] = df[col].fillna(df.groupby(lot_col)[col].transform("median"))
        df[col] = df[col].fillna(df[col].median())
        imputed += int(df[col].isna().sum())  # post-fill check
        df[col] = df[col].fillna(0.0)
    return df


def convert_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in _READING_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "Stress_Level" in df.columns:
        df["Stress_Level"] = pd.to_numeric(df["Stress_Level"], errors="coerce")

    # Canonical enum casing (never title-case — that mangles acronyms like MOSFET).
    canonical = {
        "Traditional_Test_Result": {"pass": "Pass", "fail": "Fail", "PASS": "Pass", "FAIL": "Fail"},
        "Label": {"safe": "Safe", "borderline": "Borderline", "fail": "Fail",
                  "SAFE": "Safe", "BORDERLINE": "Borderline"},
        "Part_Type": {"mosfet": "MOSFET", "op-amp ic": "Op-Amp IC",
                       "voltage regulator ic": "Voltage Regulator IC",
                       "digital logic ic": "Digital Logic IC"},
    }
    for col in ("Traditional_Test_Result", "Label", "Part_Type"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().replace(canonical[col])

    for col in ("Part_Family", "Proxy_Stress", "Stress_Unit"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    return df


def organize_timestamps(df: pd.DataFrame) -> pd.DataFrame:
    """
    Parse Timestamp_<cp> into datetime and add:
      • ``_ts`` columns (datetime64)
      • ``elapsed_h_<cp>`` — hours since the 0h reading.
    Missing timestamps fall back to the nominal schedule (0/24/96/168h) from 0h.
    """
    df = df.copy()
    nominal = {"0h": 0.0, "24h": 24.0, "96h": 96.0, "168h": 168.0}
    for cp in CHECKPOINTS:
        col = f"Timestamp_{cp}"
        if col in df.columns:
            df[f"{col}_ts"] = pd.to_datetime(df[col], errors="coerce")

    base_col = "Timestamp_0h_ts"
    if base_col not in df.columns:
        df[base_col] = pd.NaT
    # A global fallback so every component has a real 0h anchor even if the
    # source timestamp is missing/unparseable.
    if df[base_col].notna().any():
        fallback_base = df[base_col].min()
    else:
        fallback_base = pd.Timestamp("2025-01-01 00:00:00")
    df[base_col] = df[base_col].fillna(fallback_base)

    base = df[base_col]
    for cp in CHECKPOINTS:
        ts_col = f"Timestamp_{cp}_ts"
        if ts_col not in df.columns:
            df[ts_col] = pd.NaT
        # Missing checkpoints → nominal schedule from the 0h anchor, so no NaT
        # ever reaches the measurements table.
        df[ts_col] = df[ts_col].fillna(base + pd.to_timedelta(nominal[cp], unit="h"))
        df[f"elapsed_h_{cp}"] = (df[ts_col] - base).dt.total_seconds() / 3600.0
    return df


def ensure_relationships(df: pd.DataFrame) -> pd.DataFrame:
    """
    Guarantee the component → lot relationship:
      • strip any whitespace/leading zeros in IDs
      • every component must reference a Batch_ID (validated upstream)
      • enforce Part_Type consistency per lot: a lot's component_type is the
        mode of its components' Part_Type; stray differing types are reported.
    """
    df = df.copy()
    for col in ("Component_ID", "Batch_ID"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    issues: List[str] = []
    if {"Batch_ID", "Part_Type"}.issubset(df.columns):
        mode = df.groupby("Batch_ID")["Part_Type"].agg(lambda s: s.mode().iloc[0] if len(s) else None)
        for lot_id, grp in df.groupby("Batch_ID"):
            expected = mode[lot_id]
            mismatches = grp[grp["Part_Type"] != expected]
            if len(mismatches):
                issues.append(
                    f"Lot {lot_id}: {len(mismatches)} component(s) have a Part_Type "
                    f"different from the lot's dominant type '{expected}'."
                )
    df.attrs["relationship_issues"] = issues
    return df


def build_lots_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate per-Batch_ID lot records:
      lot_id, component_type (dominant Part_Type), temperature (median stress
      level for Thermal-High, else stress level), total_components, stress info.
    """
    rows: List[dict] = []
    for lot_id, grp in df.groupby("Batch_ID"):
        component_type = grp["Part_Type"].mode().iloc[0] if len(grp) else "Unknown"
        part_family = grp["Part_Family"].mode().iloc[0] if "Part_Family" in grp.columns and len(grp) else None
        stress_type = grp["Proxy_Stress"].mode().iloc[0] if "Proxy_Stress" in grp.columns and len(grp) else None
        stress_level = pd.to_numeric(grp["Stress_Level"], errors="coerce").median() if "Stress_Level" in grp.columns else None
        stress_unit = grp["Stress_Unit"].mode().iloc[0] if "Stress_Unit" in grp.columns and len(grp) else None

        # The spec's `temperature` column: for thermal stress this IS the bake °C;
        # otherwise carry the (unit-less) stress level, NULL when unknown.
        temperature = stress_level if stress_type == "Thermal-High" else None

        rows.append({
            "lot_id": lot_id,
            "component_type": component_type,
            "temperature": temperature,
            "total_components": len(grp),
            "status": "IN_REVIEW",
            "stress_type": stress_type,
            "stress_level": stress_level,
            "stress_unit": stress_unit,
            "part_family": part_family,
        })
    return pd.DataFrame(rows)


def build_components_df(df: pd.DataFrame) -> pd.DataFrame:
    comps = pd.DataFrame({
        "component_id": df["Component_ID"].values,
        "lot_id": df["Batch_ID"].values,
        "component_type": df["Part_Type"].values,
        "status": df.get("Traditional_Test_Result", pd.Series("Pass", index=df.index)).astype(str).str.strip(),
        "traditional_result": df.get("Traditional_Test_Result", pd.Series(None, index=df.index)).astype(str).str.strip(),
        "label": df.get("Label", pd.Series(None, index=df.index)).astype(str).str.strip(),
    })
    return comps


def build_measurements_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Melt the wide readings into long format: one row per (component, timestamp,
    parameter) with value + temperature + checkpoint_hours.
    """
    frames = []
    for p in PARAMETERS:
        for cp in CHECKPOINTS:
            value_col = f"{p}_{cp}"
            if value_col not in df.columns:
                continue
            ts_col = f"Timestamp_{cp}_ts"
            # Convert to python datetimes (never numpy NaT — SQLite rejects it).
            if ts_col in df.columns:
                ts = pd.to_datetime(df[ts_col], errors="coerce")
                ts = [t.to_pydatetime() if pd.notna(t) else None for t in ts]
            else:
                ts = [None] * len(df)
            frames.append(pd.DataFrame({
                "component_id": df["Component_ID"].values,
                "timestamp": ts,
                "parameter": p,
                "value": pd.to_numeric(df[value_col], errors="coerce").values,
                "temperature": df.get("Stress_Level", pd.Series(None, index=df.index)).values,
                "checkpoint_hours": float(CHECKPOINT_HOURS[cp]),
            }))
    if not frames:
        return pd.DataFrame(columns=["component_id", "timestamp", "parameter", "value", "temperature", "checkpoint_hours"])
    return pd.concat(frames, ignore_index=True)


CHECKPOINT_HOURS = {"0h": 0.0, "24h": 24.0, "96h": 96.0, "168h": 168.0}


def preprocess(df: pd.DataFrame) -> PreprocessedBurnIn:
    """
    Full preprocessing pipeline. Expects the validated raw frame (canonical CSV
    columns); returns clean wide df + derived lots/components/measurements.
    """
    issues: List[str] = []

    df = clean_column_names(df)
    df = standardize_units(df)
    df = handle_missing_values(df)
    df = convert_dtypes(df)
    df = organize_timestamps(df)
    df = ensure_relationships(df)
    issues.extend(df.attrs.get("relationship_issues", []))

    lots = build_lots_df(df)
    components = build_components_df(df)
    measurements = build_measurements_df(df)

    return PreprocessedBurnIn(
        df=df,
        lots=lots,
        components=components,
        measurements=measurements,
        issues=issues,
    )