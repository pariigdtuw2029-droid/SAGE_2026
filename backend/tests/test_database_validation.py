"""Tests for Member 2's data validation layer (database/validation.py)."""

import pandas as pd
import pytest

from database.validation import (
    REQUIRED_COLUMNS,
    DataValidationError,
    filter_valid_rows,
    validate_dataframe,
)


def _valid_row(component_id="C101", batch="ISRO-SCL-250106-01", part="MOSFET", **overrides):
    row = {
        "Component_ID": component_id,
        "Batch_ID": batch,
        "Part_Type": part,
        "Part_Family": "IRF-Series N-Channel",
        "Proxy_Stress": "Cycling-Medium",
        "Stress_Level": 200,
        "Stress_Unit": "cycles",
        "Timestamp_0h": "2025-01-06 00:00:00",
        "Timestamp_24h": "2025-01-07 00:00:00",
        "Timestamp_96h": "2025-01-10 00:00:00",
        "Timestamp_168h": "2025-01-13 00:00:00",
        "Traditional_Test_Result": "Pass",
        "Label": "Safe",
    }
    for p in ("Leakage", "Resistance", "Vth"):
        for cp in ("0h", "24h", "96h", "168h"):
            row[f"{p}_{cp}"] = {"Leakage": 1.0, "Resistance": 100.0, "Vth": 1.2}[p]
    row.update(overrides)
    return row


def _df(rows):
    return pd.DataFrame(rows)


def test_valid_row_passes():
    report = validate_dataframe(_df([_valid_row()]))
    assert report.is_valid
    assert report.valid_rows == 1
    assert report.rejected_rows == 0


def test_wrong_columns_is_fatal():
    df = _df([_valid_row()]).drop(columns=["Leakage_0h"])
    report = validate_dataframe(df)
    assert any(i.issue_type == "wrong_columns" for i in report.issues)
    assert not report.is_valid
    with pytest.raises(DataValidationError):
        if not report.is_valid:
            raise DataValidationError(report)


def test_duplicate_component_ids_rejected():
    df = _df([_valid_row("C101"), _valid_row("C101")])
    report = validate_dataframe(df)
    assert any(i.issue_type == "duplicate_id" for i in report.issues)
    assert report.rejected_rows == 1
    assert report.valid_rows == 1


def test_invalid_component_id():
    df = _df([_valid_row(component_id="101")])
    report = validate_dataframe(df)
    assert any(i.issue_type == "invalid_component_id" for i in report.issues)


def test_invalid_lot_id():
    df = _df([_valid_row(batch="")])
    report = validate_dataframe(df)
    assert any(i.issue_type == "invalid_lot_id" for i in report.issues)


def test_non_numeric_value_rejected():
    df = _df([_valid_row(**{"Leakage_24h": "abc"})])
    report = validate_dataframe(df)
    assert any(i.issue_type == "invalid_number" for i in report.issues)
    assert report.rejected_rows == 1


def test_sensor_fault_sentinel_flagged():
    df = _df([_valid_row(**{"Leakage_24h": -1.0})])
    report = validate_dataframe(df)
    assert any(i.issue_type == "invalid_temperature" for i in report.issues)


def test_missing_value_flagged_but_row_kept():
    # A missing reading is flagged for imputation but does not reject the row.
    df = _df([_valid_row(**{"Resistance_96h": None})])
    report = validate_dataframe(df)
    assert any(i.issue_type == "missing_value" for i in report.issues)
    assert report.valid_rows == 1
    assert report.rejected_rows == 0


def test_filter_valid_rows_keeps_only_good():
    df = _df([_valid_row("C101"), _valid_row("C101", **{"Leakage_24h": "bad"})])
    report = validate_dataframe(df)
    cleaned = filter_valid_rows(df, report)
    assert list(cleaned["Component_ID"]) == ["C101"]


def test_required_columns_constant_covers_schema():
    assert "Component_ID" in REQUIRED_COLUMNS
    assert "Timestamp_168h" in REQUIRED_COLUMNS
    assert "Vth_168h" in REQUIRED_COLUMNS