"""
Model inference wiring (Member 2).

Runs the three SAGE artifacts over a cleaned burn-in frame and produces the
rows that populate `predictions`, `risk_assessments` and `explanations`:

  • Module A — `models/pipeline.joblib`        anomaly risk + QA decision
               (AnomalyPipeline; classes re-created in `model_compat` because
                the artifact was pickled from a notebook `__main__` namespace)
  • Module B — `models/drift_prediction_model.joblib`  168h drift forecast
               (rf + gb + ridge meta-stack, quantile intervals; pickled with
                an older sklearn that referenced the bare `_loss` module —
                aliased to `sklearn._loss._loss` before loading)
  • Module C — reliability index composition (no artifact; the notebook's
               weights/thresholds are reproduced here verbatim)

Every load/predict is defensive: if an artifact cannot be loaded (missing
dependency, version drift, file absent), the pipeline degrades to a pure
statistical fallback (lot z-score + early drift) so ingestion never hard-fails.
"""

from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# PLAUSIBLE_RANGES available if needed for future range checks

# Module C constants (reliability_index.ipynb).
WEIGHT_ANOMALY = 0.5
WEIGHT_PREDICTED_DRIFT = 0.3
WEIGHT_UNCERTAINTY = 0.2
ABSOLUTE_FAIL_RISK_FLOOR = 65.0
RISK_SAFE_MAX = 30.0
RISK_BORDERLINE_MAX = 60.0

MODEL_VERSION = "sage-1.0"

CHECKPOINTS = ["0h", "24h", "96h", "168h"]
PARAMETERS = ["Leakage", "Resistance", "Vth"]

# Which parameter each proxy stress is expected to hit hardest (reliability notebook).
STRESS_PHYSICS: Dict[str, str] = {
    "Thermal-High": "Leakage",
    "Radiation-Low": "Vth",
    "Cycling-Medium": "Resistance",
}

_ARTIFACT_DIR = None  # resolved lazily relative to this package


def _artifact_path(name: str):
    global _ARTIFACT_DIR
    if _ARTIFACT_DIR is None:
        import os
        from pathlib import Path
        _ARTIFACT_DIR = Path(__file__).resolve().parent.parent / "models"
        if not _ARTIFACT_DIR.exists():
            _ARTIFACT_DIR = Path(os.getenv("SAGE_MODEL_DIR", "models"))
    return _ARTIFACT_DIR / name


# ---------------------------------------------------------------------------
# Module A — anomaly pipeline
# ---------------------------------------------------------------------------

def _register_notebook_classes():
    """Publish the model_compat classes under __main__ so pickle can resolve them."""
    import __main__
    import sys
    import database.model_compat as mc
    names = [
        "AnomalyPipeline", "PipelineConfig", "LotMedianImputer", "LotRelativeScorer",
        "MultivariateAnomalyScorer", "DriftSeverityScorer", "AbsoluteSpecLayer",
        "add_drift_features", "validate_schema", "SchemaError", "decide", "combine", "GLOBAL_KEY",
    ]
    for n in names:
        setattr(__main__, n, getattr(mc, n))
        sys.modules.setdefault("__main__", __main__)


@lru_cache(maxsize=1)
def load_anomaly_pipeline():
    """Load Module A (pipeline.joblib). Raises RuntimeError if unavailable."""
    path = _artifact_path("pipeline.joblib")
    if not path.exists():
        raise RuntimeError(f"Module A artifact not found: {path}")
    import joblib
    _register_notebook_classes()
    try:
        return joblib.load(str(path))
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        raise RuntimeError(f"Failed to load Module A pipeline: {exc}") from exc


# ---------------------------------------------------------------------------
# Module B — drift forecast bundle
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def load_drift_bundle():
    """Load Module B (drift_prediction_model.joblib), shimming old sklearn refs."""
    path = _artifact_path("drift_prediction_model.joblib")
    if not path.exists():
        raise RuntimeError(f"Module B artifact not found: {path}")
    import sys
    try:
        # Older sklearn pickles reference the bare `_loss` module.
        import sklearn._loss._loss as real_loss  # noqa: PLC0415
        sys.modules.setdefault("_loss", real_loss)
    except Exception:  # noqa: BLE001 — newer sklearn doesn't need the shim
        pass
    import joblib
    try:
        bundle = joblib.load(str(path))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to load Module B drift bundle: {exc}") from exc

    # Newer sklearn removed SimpleImputer._fill_dtype (older pickles reference it).
    imputer = bundle.get("imputer")
    if imputer is not None and not hasattr(imputer, "_fill_dtype"):
        stats = getattr(imputer, "statistics_", None)
        imputer._fill_dtype = stats.dtype if stats is not None else None
        bundle["imputer"] = imputer
    return bundle


def run_module_b(df: pd.DataFrame) -> pd.DataFrame:
    """
    Forecast the 168h checkpoint for Leakage/Resistance/Vth and compute
    Predicted_Drift_Score + Uncertainty_Score (mirrors reliability_index.ipynb).
    Returns a frame keyed by Component_ID with Pred_<P>_168h, Interval_Width_<P>,
    Predicted_Drift_Score, Uncertainty_Score.
    """
    bundle = load_drift_bundle()
    df = df.copy()

    feature_cols_raw = list(bundle["feature_cols_raw"])

    # same sensor-fault handling as the training notebook
    reading_cols = [f"{m}_{cp}" for m in PARAMETERS for cp in CHECKPOINTS]
    for col in reading_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df.loc[df[col] < 0, col] = np.nan

    # elapsed hours from real timestamps
    for cp in CHECKPOINTS:
        col = f"Timestamp_{cp}"
        if col in df.columns:
            df[f"{col}_ts"] = pd.to_datetime(df[col], errors="coerce")
    if "Timestamp_24h_ts" in df.columns and "Timestamp_0h_ts" in df.columns:
        df["elapsed_24h"] = (df["Timestamp_24h_ts"] - df["Timestamp_0h_ts"]).dt.total_seconds() / 3600.0
        df["elapsed_24h"] = df["elapsed_24h"].fillna(24.0)
    else:
        df["elapsed_24h"] = 24.0

    df.loc[:, feature_cols_raw] = bundle["imputer"].transform(df[feature_cols_raw])

    for m in PARAMETERS:
        df[f"{m}_slope"] = (df[f"{m}_24h"] - df[f"{m}_0h"]) / df["elapsed_24h"].replace(0, 1e-9)

    feature_cols_num = feature_cols_raw + [
        "Stress_Level", "elapsed_24h", "Leakage_slope", "Resistance_slope", "Vth_slope",
    ]
    X = pd.get_dummies(
        df[feature_cols_num + ["Proxy_Stress", "Part_Type"]],
        columns=["Proxy_Stress", "Part_Type"],
    )
    X = X.reindex(columns=bundle["feature_columns"], fill_value=0)

    rf_pred = bundle["rf"].predict(X)
    gb_pred = bundle["gb"].predict(X)
    stacked_raw = bundle["meta_model"].predict(np.hstack([rf_pred, gb_pred]))
    lower_raw = bundle["quantile_lower"].predict(X)
    upper_raw = bundle["quantile_upper"].predict(X)

    def reconstruct(raw) -> pd.DataFrame:
        out = pd.DataFrame(index=df.index)
        out["Leakage_168h"] = np.expm1(raw[:, 1])
        out["Resistance_168h"] = df["Resistance_24h"].values + raw[:, 3]
        out["Vth_168h"] = df["Vth_24h"].values + raw[:, 5]
        return out

    point = reconstruct(stacked_raw)
    lower = reconstruct(lower_raw)
    upper = reconstruct(upper_raw)

    result = pd.DataFrame({"Component_ID": df["Component_ID"].values})
    for m in PARAMETERS:
        result[f"Pred_{m}_168h"] = point[f"{m}_168h"].values
        result[f"Interval_Width_{m}"] = (upper[f"{m}_168h"] - lower[f"{m}_168h"]).clip(lower=0).values

    # Predicted_Drift_Score: max relative drift between 24h value and forecast,
    # normalized to its 99th percentile (same normalization as Module C).
    rel = []
    for m in PARAMETERS:
        denom = df[f"{m}_24h"].abs().replace(0, 1e-9)
        rel.append(((result[f"Pred_{m}_168h"] - df[f"{m}_24h"].values) / denom.values).abs())
    severity = pd.concat(rel, axis=1).max(axis=1)
    bound = severity.quantile(0.99)
    result["Predicted_Drift_Score"] = (severity.clip(upper=bound) / max(bound, 1e-9) * 100).values

    # Uncertainty_Score: max normalized interval width across parameters.
    iw = result[[f"Interval_Width_{m}" for m in PARAMETERS]].copy()
    iw_norm = iw / iw.quantile(0.99).clip(lower=1e-9)
    result["Uncertainty_Score"] = (iw_norm.max(axis=1) * 100).clip(0, 100).values

    result["model_version"] = MODEL_VERSION
    return result


# ---------------------------------------------------------------------------
# Module C — reliability composition + decisions
# ---------------------------------------------------------------------------

def compute_reliability(anomaly: pd.DataFrame, drift: pd.DataFrame) -> pd.DataFrame:
    """
    Fuse Module A (anomaly) + Module B (drift) into a per-component risk record
    exactly as reliability_index.ipynb does. Inputs are keyed by Component_ID.
    """
    merged = anomaly.merge(drift, on="Component_ID", how="left", suffixes=("", "_b"))

    risk = (
        WEIGHT_ANOMALY * merged["Anomaly_Risk_Score"]
        + WEIGHT_PREDICTED_DRIFT * merged["Predicted_Drift_Score"].fillna(0.0)
        + WEIGHT_UNCERTAINTY * merged["Uncertainty_Score"].fillna(0.0)
    )
    risk = np.where(merged["Absolute_Spec_Fail"] == 1, np.maximum(risk, ABSOLUTE_FAIL_RISK_FLOOR), risk)
    risk = np.clip(risk, 0, 100).round(1)

    def tier(score):
        if score < RISK_SAFE_MAX:
            return "Space-Safe"
        if score < RISK_BORDERLINE_MAX:
            return "Borderline"
        return "High-Risk"

    merged["risk_score"] = risk
    merged["reliability_index"] = (100 - risk).round(1)
    merged["reliability_tier"] = merged["risk_score"].apply(tier)
    # Confidence: high uncertainty ⇒ low confidence in the risk call.
    merged["confidence"] = (1 - merged["Uncertainty_Score"].fillna(0.0) / 100.0).clip(0.05, 0.99).round(3)
    return merged


def _risk_level_from_tier(tier: str) -> str:
    return {"Space-Safe": "LOW", "Borderline": "MEDIUM", "High-Risk": "HIGH"}.get(tier, "UNKNOWN")


# ---------------------------------------------------------------------------
# Explanations
# ---------------------------------------------------------------------------

def build_explanations(row: pd.Series) -> List[Tuple[str, str, Optional[float]]]:
    """
    Build explanation rows (feature, contribution, reason) for one component
    using the same physics framing as the reliability notebook.
    """
    out: List[Tuple[str, str, Optional[float]]] = []
    stress = str(row.get("Proxy_Stress", "")).strip()
    expected_param = STRESS_PHYSICS.get(stress)

    # Per-parameter early drift (0h → 24h), the strongest explainer.
    for m in PARAMETERS:
        v0 = row.get(f"{m}_0h")
        v24 = row.get(f"{m}_24h")
        if pd.isna(v0) or pd.isna(v24) or v0 == 0:
            continue
        pct = float((v24 - v0) / abs(v0) * 100.0)
        note = ""
        if m == expected_param:
            note = " (expected stress response)"
        elif abs(pct) > 5:
            note = " — atypical for this stress, worth a second look"
        out.append((m, round(pct, 1), f"{m} moved {pct:+.1f}% in the first 24h under {stress}{note}."))

    # Lot-relative outlier signal from Module A.
    z = row.get("Worst_Lot_Zscore")
    if z is not None and not pd.isna(z):
        out.append(("Lot_Relative", round(float(z), 2),
                    f"Batch-relative outlier (z={float(z):.2f} vs its own lot)."))

    # Absolute spec check.
    if int(row.get("Absolute_Spec_Fail", 0) or 0) == 1:
        out.append(("Traditional_Test_Result", 1.0,
                    "Failed traditional fixed-limit spec check."))

    # Forecast signal from Module B.
    pds = row.get("Predicted_Drift_Score")
    if pds is not None and not pd.isna(pds):
        out.append(("Predicted_Drift", round(float(pds), 1),
                    "Forecast to 168h shows continued significant drift."))

    us = row.get("Uncertainty_Score")
    if us is not None and not pd.isna(us):
        out.append(("Uncertainty", round(float(us), 1),
                    "Forecast confidence is low — recommend extended monitoring."))

    if not out:
        out.append(("Summary", None, "No significant deviation detected."))
    return out


# ---------------------------------------------------------------------------
# Fallback scoring (no artifacts / no sklearn)
# ---------------------------------------------------------------------------

def fallback_scores(df: pd.DataFrame) -> pd.DataFrame:
    """
    Pure-statistical fallback used when the artifacts can't be loaded:
    lot-relative robust z-scores + early-drift severity, combined like Module C.
    """
    rows = []
    for _, grp in df.groupby("Batch_ID"):
        for _, row in grp.iterrows():
            worst_z, max_drift = 0.0, 0.0
            for m in PARAMETERS:
                vals = pd.to_numeric(grp[f"{m}_24h"], errors="coerce")
                med = vals.median()
                mad = (vals - med).abs().median() * 1.4826
                mad = mad if mad > 0 else 1e-9
                z = abs((row[f"{m}_24h"] - med) / mad) if not pd.isna(row[f"{m}_24h"]) else 0.0
                worst_z = max(worst_z, float(z))
                v0, v24 = row.get(f"{m}_0h"), row.get(f"{m}_24h")
                if not pd.isna(v0) and not pd.isna(v24) and v0 != 0:
                    max_drift = max(max_drift, abs(float((v24 - v0) / v0)))
            # anomaly score computed inline in the Anomaly_Risk_Score dict below
            rows.append({
                "Component_ID": row["Component_ID"],
                "Batch_ID": row["Batch_ID"],
                "Part_Type": row["Part_Type"],
                "Absolute_Spec_Fail": int(str(row.get("Traditional_Test_Result", "Pass")).strip().lower() == "fail"),
                "Lot_Relative_Score": round(min(worst_z / 8.0 * 100, 100.0), 1),
                "Worst_Lot_Zscore": round(worst_z, 2),
                "Multivariate_Score": 0.0,
                "Drift_Score": round(min(max_drift * 100, 100.0), 1),
                "Anomaly_Risk_Score": round(min(worst_z / 8.0 * 100, 100.0) * 0.5 + min(max_drift * 100, 100.0) * 0.5, 1),
                "QA_Decision": "PASS",
                "Predicted_Drift_Score": 0.0,
                "Uncertainty_Score": 0.0,
                "model_version": "fallback-stats",
            })
    out = pd.DataFrame(rows)
    return out


def run_inference(df: pd.DataFrame) -> Tuple[pd.DataFrame, str, List[str]]:
    """
    Run the full 3-module stack over the cleaned wide frame.
    Returns (scores_df, model_version_used, warnings).
    scores_df contains Module A columns + Module C risk/decision + Module B
    drift columns, one row per component.
    """
    warnings: List[str] = []
    df = df.copy()

    # Module A — anomaly risk + decision
    anomaly_df = pd.DataFrame()  # will be overwritten by try or except
    try:
        pipeline = load_anomaly_pipeline()
        anomaly_df = pipeline.predict(df)
        anomaly_df["model_version"] = MODEL_VERSION
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Module A unavailable ({exc}); using statistical fallback.")
        anomaly_df = fallback_scores(df)

    # Module B — 168h forecast
    try:
        drift = run_module_b(df)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Module B unavailable ({exc}); skipping drift forecast.")
        drift = pd.DataFrame({
            "Component_ID": df["Component_ID"].values,
            "Predicted_Drift_Score": 0.0,
            "Uncertainty_Score": 0.0,
        })

    merged = compute_reliability(anomaly_df, drift)
    merged["risk_level"] = merged["reliability_tier"].map(_risk_level_from_tier)
    # Map QA decision labels to the API vocabulary (already PASS/MONITOR/HOLD/REJECT).
    merged["decision"] = merged["QA_Decision"].astype(str).str.strip()
    merged["confidence"] = merged["confidence"].fillna(0.5)
    return merged, MODEL_VERSION, warnings