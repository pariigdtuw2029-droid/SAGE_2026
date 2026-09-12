"""Tests for Member 2's preprocessing layer (database/preprocessing.py)."""

import numpy as np
import pandas as pd

from database.preprocessing import (
    PARAMETER_UNITS,
    build_measurements_df,
    handle_missing_values,
    organize_timestamps,
    preprocess,
)


def _df(rows):
    return pd.DataFrame(rows)


def test_parameter_units_documented():
    assert PARAMETER_UNITS["Leakage"] == "µA"
    assert PARAMETER_UNITS["Resistance"] == "Ω"
    assert PARAMETER_UNITS["Vth"] == "V"


def test_missing_value_imputation_uses_lot_median():
    df = _df([
        {"Batch_ID": "LOT-A", "Leakage_96h": 1.0, "Leakage_168h": 2.0},
        {"Batch_ID": "LOT-A", "Leakage_96h": 3.0, "Leakage_168h": np.nan},
        {"Batch_ID": "LOT-A", "Leakage_96h": np.nan, "Leakage_168h": 4.0},
    ])
    out = handle_missing_values(df)
    assert not out["Leakage_96h"].isna().any()
    assert not out["Leakage_168h"].isna().any()
    # Lot median of [1,3] = 2.0
    assert out["Leakage_96h"].iloc[2] == 2.0


def test_sensor_fault_sentinel_becomes_nan_then_imputed():
    df = _df([
        {"Batch_ID": "LOT-A", "Leakage_0h": 1.0, "Leakage_24h": 2.0, "Leakage_96h": 3.0, "Leakage_168h": -1.0},
        {"Batch_ID": "LOT-A", "Leakage_0h": 1.5, "Leakage_24h": 2.5, "Leakage_96h": 3.5, "Leakage_168h": 4.5},
    ])
    out = handle_missing_values(df)
    assert not out["Leakage_168h"].isna().any()
    assert out["Leakage_168h"].iloc[0] == 4.5  # lot median of [4.5] fallback


def test_timestamp_organization_builds_elapsed_hours():
    df = _df([{
        "Component_ID": "C101",
        "Timestamp_0h": "2025-01-06 00:00:00",
        "Timestamp_24h": "2025-01-07 00:00:00",
        "Timestamp_96h": "2025-01-10 00:00:00",
        "Timestamp_168h": "2025-01-13 00:00:00",
    }])
    out = organize_timestamps(df)
    assert abs(out["elapsed_h_24h"].iloc[0] - 24.0) < 1e-6
    assert abs(out["elapsed_h_96h"].iloc[0] - 96.0) < 1e-6
    assert abs(out["elapsed_h_168h"].iloc[0] - 168.0) < 1e-6


def test_build_measurements_long_format():
    df = _df([{
        "Component_ID": "C101",
        "Stress_Level": 200,
        "Timestamp_0h": "2025-01-06 00:00:00",
        "Timestamp_24h": "2025-01-07 00:00:00",
        "Timestamp_96h": "2025-01-10 00:00:00",
        "Timestamp_168h": "2025-01-13 00:00:00",
        "Leakage_0h": 1.0, "Leakage_24h": 1.1, "Leakage_96h": 1.2, "Leakage_168h": 1.3,
        "Resistance_0h": 100.0, "Resistance_24h": 100.5, "Resistance_96h": 101.0, "Resistance_168h": 101.5,
        "Vth_0h": 1.20, "Vth_24h": 1.19, "Vth_96h": 1.18, "Vth_168h": 1.17,
    }])
    out = build_measurements_df(organize_timestamps(df))
    # 3 parameters × 4 checkpoints
    assert len(out) == 12
    assert set(out["parameter"]) == {"Leakage", "Resistance", "Vth"}
    assert out["checkpoint_hours"].nunique() == 4
    assert (out["temperature"] == 200).all()


def test_full_preprocess_returns_expected_frames():
    rows = [{
        "Component_ID": f"C{i}",
        "Batch_ID": "ISRO-SCL-250106-01",
        "Part_Type": "MOSFET",
        "Part_Family": "IRF-Series N-Channel",
        "Proxy_Stress": "Thermal-High",
        "Stress_Level": 140.0,
        "Stress_Unit": "°C",
        "Timestamp_0h": "2025-01-06 00:00:00",
        "Timestamp_24h": "2025-01-07 00:00:00",
        "Timestamp_96h": "2025-01-10 00:00:00",
        "Timestamp_168h": "2025-01-13 00:00:00",
        "Traditional_Test_Result": "Pass",
        "Label": "Safe",
    } for i in range(2)]
    for i, r in enumerate(rows):
        for p, base in (("Leakage", 1.0), ("Resistance", 100.0), ("Vth", 1.2)):
            for cp, mult in (("0h", 1), ("24h", 2), ("96h", 3), ("168h", 4)):
                r[f"{p}_{cp}"] = base + (i * 0.01) + (mult * 0.01)

    cleaned = preprocess(_df(rows))
    assert cleaned.component_count == 2
    assert len(cleaned.lots) == 1
    assert cleaned.lots.iloc[0]["component_type"] == "MOSFET"
    assert cleaned.lots.iloc[0]["temperature"] == 140.0  # thermal → bake temp
    assert len(cleaned.measurements) == 24