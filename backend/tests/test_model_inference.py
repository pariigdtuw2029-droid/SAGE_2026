"""
Unit tests for the Module A/B/C wiring (database/ml_inference.py).

These run without scikit-learn, joblib or shap: the tuned constants, the risk
floors, the SHAP string parsing and the split enrichment are all pure
pandas/numpy and are the parts most likely to drift when the notebooks retune.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from database import ingestion, ml_inference

BACKEND_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BACKEND_DIR / "models" / "config.json"
SPLIT_CSV = BACKEND_DIR / "data" / "space_reliability_index.csv"


def test_reliability_config_matches_config_json():
    cfg = ml_inference.reliability_config()
    on_disk = json.loads(CONFIG_PATH.read_text())["module_c"]
    for key, value in on_disk.items():
        if key in cfg:
            assert cfg[key] == pytest.approx(float(value)), key


def test_weights_sum_to_one_and_tiers_are_ordered():
    cfg = ml_inference.reliability_config()
    total = cfg["weight_anomaly"] + cfg["weight_predicted_drift"] + cfg["weight_uncertainty"]
    assert total == pytest.approx(1.0, abs=0.02)
    assert cfg["risk_safe_max"] < cfg["risk_borderline_max"]
    # A slope-flagged component is pushed to at least the borderline cut.
    assert cfg["slope_reject_risk_floor"] >= cfg["risk_borderline_max"]


def test_compute_reliability_applies_slope_and_absolute_floors():
    cfg = ml_inference.reliability_config()
    anomaly = pd.DataFrame({
        "Component_ID": ["C1", "C2", "C3"],
        "Anomaly_Risk_Score": [5.0, 5.0, 5.0],
        "Absolute_Spec_Fail": [0, 1, 0],
    })
    drift = pd.DataFrame({
        "Component_ID": ["C1", "C2", "C3"],
        "Predicted_Drift_Score": [1.0, 1.0, 1.0],
        "Uncertainty_Score": [10.0, 10.0, 10.0],
        "Slope_Reject_Flag": [False, False, True],
    })
    merged = ml_inference.compute_reliability(anomaly, drift).set_index("Component_ID")

    assert merged.loc["C2", "risk_score"] >= cfg["absolute_fail_risk_floor"]
    assert merged.loc["C3", "risk_score"] >= cfg["slope_reject_risk_floor"]
    assert merged.loc["C1", "risk_score"] < merged.loc["C3", "risk_score"]
    assert merged.loc["C1", "reliability_tier"] == "Space-Safe"
    assert merged.loc["C2", "reliability_tier"] == "High-Risk"
    assert merged.loc["C1", "reliability_index"] == pytest.approx(100 - merged.loc["C1", "risk_score"])
    assert 0.0 <= merged.loc["C1", "confidence"] <= 1.0


def test_compute_reliability_survives_a_missing_drift_module():
    """Module B failing must not sink the fusion (drift columns absent)."""
    anomaly = pd.DataFrame({
        "Component_ID": ["C1"],
        "Anomaly_Risk_Score": [90.0],
        "Absolute_Spec_Fail": [0],
    })
    drift = pd.DataFrame({
        "Component_ID": ["C1"],
        "Predicted_Drift_Score": [0.0],
        "Uncertainty_Score": [0.0],
    })
    merged = ml_inference.compute_reliability(anomaly, drift)
    assert merged.loc[0, "risk_score"] > 0
    assert merged.loc[0, "reliability_tier"] in {"Space-Safe", "Borderline", "High-Risk"}


def test_parse_shap_features():
    text = "Leakage_slope(+0.303), Resistance_slope(-0.088)"
    assert ml_inference.parse_shap_features(text) == [
        ("Leakage_slope", 0.303),
        ("Resistance_slope", -0.088),
    ]
    assert ml_inference.parse_shap_features(None) == []
    assert ml_inference.parse_shap_features("") == []


def test_build_explanations_includes_shap_and_slope_rows():
    row = pd.Series({
        "Proxy_Stress": "Thermal-High",
        "Leakage_0h": 1.0, "Leakage_24h": 1.5,
        "Resistance_0h": 100.0, "Resistance_24h": 101.0,
        "Vth_0h": 1.2, "Vth_24h": 1.21,
        "Worst_Lot_Zscore": 2.5,
        "Absolute_Spec_Fail": 0,
        "Slope_Reject_Flag": True,
        "Predicted_Drift_Score": 40.0,
        "Uncertainty_Score": 70.0,
        "SHAP_Top_Anomaly_Features": "Leakage_delta_24h(+0.10), Vth_24h(-0.03)",
        "SHAP_Top_Drift_Features": "Leakage_slope(+0.303)",
        "Dominant_Drift_Metric": "Leakage",
    })
    features = [f for f, _, _ in ml_inference.build_explanations(row)]
    assert "Leakage_slope" in features          # drift SHAP
    assert "Leakage_delta_24h" in features      # anomaly SHAP
    assert "Slope_Reject_Flag" in features      # v5.1 early rejection
    assert "Lot_Relative" in features
    reasons = [r for _, _, r in ml_inference.build_explanations(row)]
    assert all(isinstance(r, str) and r for r in reasons)


def test_absolute_fail_still_explains_without_signal():
    row = pd.Series({"Proxy_Stress": "Thermal-High", "Absolute_Spec_Fail": 1})
    out = ml_inference.build_explanations(row)
    assert out and out[0][0] == "Traditional_Test_Result"


def test_component_split_map_and_enrichment():
    if not SPLIT_CSV.exists():
        pytest.skip("space_reliability_index.csv not present")
    mapping = ingestion._component_split_map()
    assert len(mapping) > 100
    assert set(mapping.values()) <= {"train", "val", "test"}

    frame = pd.DataFrame({"Component_ID": list(mapping)[:5]})
    enriched = ingestion._enrich_split(frame)
    assert "split" in enriched.columns
    assert enriched["split"].notna().all()
    # An explicit split is never overwritten.
    explicit = pd.DataFrame({"Component_ID": list(mapping)[:5], "split": ["train"] * 5})
    assert (ingestion._enrich_split(explicit)["split"] == "train").all()


def test_fallback_scores_still_forecast_and_decision():
    """Without artifacts the stack degrades to statistics but still scores every row."""
    df = pd.DataFrame({
        "Component_ID": ["C1", "C2"],
        "Batch_ID": ["LOT-1", "LOT-1"],
        "Part_Type": ["MOSFET", "MOSFET"],
        "Traditional_Test_Result": ["Pass", "Fail"],
        "elapsed_h_24h": [24.0, 24.0],
        "Leakage_0h": [1.0, 1.0], "Leakage_24h": [1.1, 4.0],
        "Resistance_0h": [100.0, 100.0], "Resistance_24h": [100.5, 130.0],
        "Vth_0h": [1.2, 1.2], "Vth_24h": [1.21, 1.45],
    })
    out = ml_inference.fallback_scores(df)
    assert len(out) == 2
    assert {"Pred_Leakage_168h", "Pred_Resistance_168h", "Pred_Vth_168h"} <= set(out.columns)
    # the degraded component extrapolates further than the healthy one
    assert out.loc[1, "Pred_Leakage_168h"] > out.loc[0, "Pred_Leakage_168h"]
    assert out.loc[1, "Anomaly_Risk_Score"] > out.loc[0, "Anomaly_Risk_Score"]
    assert set(out["model_version"]) == {"fallback-stats"}
